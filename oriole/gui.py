"""Oriole window: visual (DMD) on the left, the image to click spots on in the middle, holo (SLM) on the right.

Look and dark theme taken from Plerion; the holo side follows the MATLAB DHgui.
"""
from __future__ import annotations

import datetime as _dt
import html
import math
import sys
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from . import holo, visual
from .calibration import Calibration
from .config import DEFAULT_CONFIG_PATH, Config
from .images import load_gray
from .shutter import Shutter, ShutterError

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

BG, CARD, TEXT, DIM, BTN, ACCENT = "#121212", "#1E1E1E", "#E0E0E0", "#555555", "#2A2A2A", "#2D6A9F"
PANEL_BG = "#181818"                                    # the three parts are cards on the window background
VISUAL_COLOUR, IMAGE_COLOUR, HOLO_COLOUR = "#2D6A9F", "#3A3A3A", "#7A3E9D"
SPOT = (255, 40, 40)
SLM_RING = "#C77DFF"                                    # the spot whose mask is on the SLM (holo purple, lighter)
NO_GO = "#FF4040"                                       # outside Wavefront's field: red line, hatched area
FIELD_PENS = {"square": pg.mkPen(NO_GO, width=2),       # Wavefront's field outline; dashed: its 20 % (outer) and
              **{kind: pg.mkPen(colour, width=1.5, style=QtCore.Qt.PenStyle.DashLine)     # 90 % (inner) circles
                 for kind, colour in (("outer", "#FF9A3C"), ("inner", "#FFE14D"), ("cross", "#C8C8C8"))}}
LOG_COLOURS = {"info": "#00FF00", "warn": "#FFD700", "error": "#FF4444", "alarm": "#FF4444"}   # alarm: big, bold
ACTION_STYLE = """
    QPushButton { background: #1A4A1A; color: #00FF00; border: none; border-radius: 4px;
                  font: bold 11pt 'Consolas'; padding: 10px; }
    QPushButton:hover { background: #2A6A2A; }
    QPushButton:disabled { background: #3A3A3A; color: #666666; }
"""
EPI_SIZE = (672, 512)      # width, height of the epi camera images the calibrations are made for
AUTOLOAD_HINT = 'check that "Tools → Script engine → auto load (script folder)" is selected in Wavefront'
HELP_HTML = f"""
<h3>Spots</h3>
<p><b>Load image…</b> an epi image binned 2×2, {EPI_SIZE[0]} × {EPI_SIZE[1]} px (any other size: big red
warning).<br>
<b>Left click</b> adds a spot, <b>drag</b> a spot to move it, <b>right click</b> on a spot removes it.
<b>Undo</b> / <b>Clear</b>. <b>Spot Ø</b> is the diameter of every spot on the sample (µm).</p>
<h3>Calibrations (typed by hand, one per side)</h3>
<p>µm = R(−rotation) · diag(stretch X, stretch Y) · (pixel + 0.5 − image centre) + (X, Y)<br>
Stretches are µm per camera pixel; a negative stretch mirrors. Holo default = DHparams.m
(X 5, Y −8, stretch 0.67 / −0.67). The line under the image shows the mouse in both.</p>
<h3>Visual · DMD</h3>
<p><b>EXTRACT BIN</b> writes <code>YYYY_MM_DD_&lt;N&gt;Frames.bin</code>: for spot i (from 1), frame 2(i−1) is the
spot and 2(i−1)+1 its negative; a <code>.json</code> lists the frames.<br>
<b>Merge into bin…</b> appends it to a linked bin (inputs untouched; <code>.indices.json</code> gives the
new frame numbers).</p>
<h3>Holo · SLM</h3>
<p><b>Wavefront folder</b>: scripts go to its <code>script\\</code>, masks are read from its
<code>GeneratedPhasemasks\\</code>; 🧹 sweeps a folder clean, 📂 opens the phase mask folder in the file
explorer.<br>
On the image, through the holo calibration, Wavefront's field as Wavefront draws it: its square in
<span style="color:{NO_GO}">red</span>, the image outside it hatched red (no-go zone: Wavefront cannot put a spot
there), its 20 % diffraction efficiency circle in <span style="color:#FF9A3C">orange</span> dashes, its 90 % one in
<span style="color:#FFE14D">yellow</span>, its centre cross in light grey; <b>SLM field on the image</b> (holo
Settings) shows or hides it. With spots (partly) outside the field, Generate first asks: <b>Remove spots and
generate</b> (Undo brings them back), <b>Generate anyway</b> (their masks hold no spot, or a cut one; big red
warning in the log) or <b>Cancel</b> (a right click removes a spot).<br>
<b>GENERATE PHASE MASKS</b> has Wavefront save one mask per spot, <code>Pattern1…N</code>, each script
emptying Wavefront's pattern list first (a pattern left there, made by hand or loaded, would stop every mask from
being saved), each mask checked against the spot Wavefront drew for it (<code>rawSrc.png</code>).
It is <span style="color:#FFD700">yellow</span> while the script
or phase mask folder is not empty: it then warns and sweeps them only on a yes. While it runs it is a red
<b>STOP</b>.<br>
A script Wavefront does not take within {holo.NOT_READ_S:.0f} s, or a mask it does not save within
{holo.MASK_WAIT_S:.0f} s, is erased and sent again (attempt 1/{holo.MAX_ATTEMPTS}, 2/{holo.MAX_ATTEMPTS}…); at
{holo.MAX_ATTEMPTS}/{holo.MAX_ATTEMPTS} the generation is aborted. After STOP or an abort Oriole waits
{holo.ABORT_WAIT_S:.0f} s (Wavefront may still be computing), then sweeps both Wavefront folders, again every second
until they are empty: ready to generate again (check Tools → Script engine → auto load (script folder), or restart
Wavefront).<br>
<b>Manual</b>: pick a mask number and <b>Load</b> it onto the SLM (a script not taken is erased and sent again,
{holo.MAX_ATTEMPTS} attempts): its spot gets a
<span style="color:{SLM_RING}">purple SLM ring</span> on the image (where it was clicked, from
<code>Pattern_spots.json</code>); <b>Open</b> / <b>Close</b> the shutter (close it before a Plerion protocol).</p>
<p>Everything typed is saved in <code>~/.oriole/config.json</code>.</p>
"""
BLOCKED_STYLE = ACTION_STYLE.replace("#1A4A1A", "#4A4A1A").replace("#00FF00", "#FFD700").replace("#2A6A2A", "#6A6A2A")
STOP_STYLE = ACTION_STYLE.replace("#1A4A1A", "#5A1414").replace("#00FF00", "#FF6060").replace("#2A6A2A", "#7A2020")


def apply_dark_theme(app: QtWidgets.QApplication):
    """Plerion's dark palette and style sheet."""
    R = QtGui.QPalette.ColorRole
    palette = QtGui.QPalette()
    for role, colour in ((R.Window, BG), (R.WindowText, TEXT), (R.Base, CARD), (R.AlternateBase, BG),
                         (R.ToolTipBase, CARD), (R.ToolTipText, TEXT), (R.Text, TEXT), (R.Button, BTN),
                         (R.ButtonText, TEXT), (R.BrightText, "#FF4444"), (R.Link, ACCENT),
                         (R.Highlight, ACCENT), (R.HighlightedText, "#FFFFFF")):
        palette.setColor(role, QtGui.QColor(colour))
    for role in (R.Text, R.ButtonText):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, role, QtGui.QColor(DIM))
    app.setPalette(palette)
    app.setFont(QtGui.QFont("Segoe UI", 9))
    app.setStyleSheet("""
        QGroupBox { border: 1px solid #333; border-radius: 4px; margin-top: 10px;
                    padding: 12px 6px 6px 6px; font: bold 9pt 'Segoe UI'; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #E0E0E0; }
        QPushButton { border: none; border-radius: 3px; padding: 6px 14px; background: #2A2A2A; color: #E0E0E0; }
        QPushButton:hover { background: #3A3A3A; }
        QPushButton:pressed { background: #2D6A9F; }
        QPushButton:disabled { color: #555555; }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            border: 1px solid #333; border-radius: 3px; padding: 4px; background: #1E1E1E; color: #E0E0E0; }
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; border: none; }
        QComboBox QAbstractItemView { background: #1E1E1E; color: #E0E0E0;
                                      selection-background-color: #2D6A9F; selection-color: #FFFFFF; }
        QToolTip { background: #1E1E1E; color: #E0E0E0; border: 1px solid #333; }
    """)


def _title(text: str, colour: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setFont(QtGui.QFont("Consolas", 12, QtGui.QFont.Weight.Bold))
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background: {colour}; color: white; padding: 6px; border-radius: 4px;")
    return lbl


def _card(layout: QtWidgets.QLayout, name: str, colour: str, width: int | None = None) -> QtWidgets.QFrame:
    """One of the three parts: a framed card with a coloured top edge."""
    card = QtWidgets.QFrame()
    card.setObjectName(name)
    card.setStyleSheet(f"QFrame#{name} {{ background: {PANEL_BG}; border: 1px solid #2E2E2E;"
                       f" border-top: 3px solid {colour}; border-radius: 6px; }}")
    card.setLayout(layout)
    if width:
        card.setFixedWidth(width)
    return card


def camera_target(w: int = EPI_SIZE[0], h: int = EPI_SIZE[1]) -> np.ndarray:
    """'No image' placeholder, a cinema camera framing target of the expected size: RGB uint8 (h, w, 3)."""
    img = QtGui.QImage(w, h, QtGui.QImage.Format.Format_RGB888)
    img.fill(QtGui.QColor("#141414"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    cx, cy, m = w / 2, h / 2, 16
    grey, dark = QtGui.QColor("#3C3C3C"), QtGui.QColor("#2A2A2A")
    # countdown-leader sweep, then rings and crosshair with ticks
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor("#1B2530"))
    p.drawPie(QtCore.QRectF(cx - 200, cy - 200, 400, 400), 90 * 16, -110 * 16)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    for r, colour, width in ((200, grey, 1.5), (140, dark, 1.0), (80, grey, 1.5), (30, dark, 1.0)):
        p.setPen(QtGui.QPen(colour, width))
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
    p.setPen(QtGui.QPen(dark, 1))
    p.drawLine(QtCore.QPointF(m, cy), QtCore.QPointF(w - m, cy))
    p.drawLine(QtCore.QPointF(cx, m), QtCore.QPointF(cx, h - m))
    for k in range(-20, 21):
        t = 4 if k % 4 else 9
        p.drawLine(QtCore.QPointF(cx + 16 * k, cy - t), QtCore.QPointF(cx + 16 * k, cy + t))
        p.drawLine(QtCore.QPointF(cx - t, cy + 16 * k), QtCore.QPointF(cx + t, cy + 16 * k))
    # frame, dashed safe area, accent corner brackets
    p.setPen(QtGui.QPen(grey, 1.5))
    p.drawRect(QtCore.QRectF(m, m, w - 2 * m, h - 2 * m))
    p.setPen(QtGui.QPen(dark, 1, QtCore.Qt.PenStyle.DashLine))
    p.drawRect(QtCore.QRectF(w * 0.1, h * 0.1, w * 0.8, h * 0.8))
    p.setPen(QtGui.QPen(QtGui.QColor(ACCENT), 3))
    for x, y, dx, dy in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
        p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x + 44 * dx, y))
        p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x, y + 44 * dy))
    p.setBrush(QtGui.QColor(ACCENT))
    p.drawEllipse(QtCore.QPointF(cx, cy), 4, 4)
    # labels
    p.setPen(QtGui.QColor("#6A6A6A"))
    p.setFont(QtGui.QFont("Consolas", 26, QtGui.QFont.Weight.Bold))
    p.drawText(QtCore.QRectF(0, cy - 120, w, 40), QtCore.Qt.AlignmentFlag.AlignCenter, "NO IMAGE")
    p.setPen(QtGui.QColor("#565656"))
    p.setFont(QtGui.QFont("Consolas", 11))
    p.drawText(QtCore.QRectF(0, cy + 90, w, 24), QtCore.Qt.AlignmentFlag.AlignCenter,
               f"Load image…   epi  {w} × {h} px")
    p.setFont(QtGui.QFont("Consolas", 9))
    p.drawText(QtCore.QRectF(m + 8, m + 6, 200, 18), QtCore.Qt.AlignmentFlag.AlignLeft, "ORIOLE")
    p.drawText(QtCore.QRectF(w - m - 208, h - m - 24, 200, 18), QtCore.Qt.AlignmentFlag.AlignRight,
               f"{w} × {h}")
    p.end()
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    rows = np.frombuffer(bits, np.uint8).reshape(h, img.bytesPerLine())
    return rows[:, :3 * w].reshape(h, w, 3).copy()


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def _dates(paths) -> str:
    """Modification dates of ``paths`` as 'YYYY MM DD' or 'YYYY MM DD – YYYY MM DD'."""
    first, last = holo.date_range(paths)
    return f"{first:%Y %m %d}" if first == last else f"{first:%Y %m %d} – {last:%Y %m %d}"


def _action_button(text: str) -> QtWidgets.QPushButton:
    btn = QtWidgets.QPushButton(text)
    btn.setStyleSheet(ACTION_STYLE)
    btn.setMinimumHeight(44)
    return btn


# ----------------------------------------------------------------------------- widgets
class CalibrationBox(QtWidgets.QGroupBox):
    """X, Y, rotation, stretch X, stretch Y, typed by hand."""

    changed = QtCore.pyqtSignal()
    FIELDS = (("x_um", "X", " µm", 2, 0.5), ("y_um", "Y", " µm", 2, 0.5), ("rot_deg", "rotation", " °", 2, 0.5),
              ("stretch_x", "stretch X", " µm/px", 4, 0.005), ("stretch_y", "stretch Y", " µm/px", 4, 0.005))

    def __init__(self, cal: Calibration, parent=None):
        super().__init__("Calibration", parent)
        form = QtWidgets.QFormLayout(self)
        self.spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for key, label, suffix, decimals, step in self.FIELDS:
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-100000, 100000)
            sp.setDecimals(decimals)
            sp.setSingleStep(step)
            sp.setSuffix(suffix)
            sp.setValue(getattr(cal, key))
            sp.valueChanged.connect(self.changed)
            form.addRow(label, sp)
            self.spins[key] = sp
        self.spins["stretch_x"].setToolTip("µm per camera pixel; negative = mirror")
        self.spins["stretch_y"].setToolTip("µm per camera pixel; negative = mirror")

    def calibration(self) -> Calibration:
        return Calibration(**{k: sp.value() for k, sp in self.spins.items()})


class _ClickViewBox(pg.ViewBox):
    """Fixed view (no pan, no zoom): clicks and left-button drags are passed on in image coordinates."""

    clicked = QtCore.pyqtSignal(float, float, bool)     # x, y, right button
    dragged = QtCore.pyqtSignal(float, float, int)      # x, y, phase: 0 start (press point), 1 move, 2 end

    def __init__(self):
        super().__init__(lockAspect=True, invertY=True, enableMenu=False)
        self.setMouseEnabled(x=False, y=False)

    def mouseClickEvent(self, ev):
        if ev.button() in (QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.RightButton):
            ev.accept()
            p = self.mapToView(ev.pos())
            self.clicked.emit(p.x(), p.y(), ev.button() == QtCore.Qt.MouseButton.RightButton)

    def mouseDragEvent(self, ev, axis=None):
        ev.accept()
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if ev.isStart():
            p = self.mapToView(ev.buttonDownPos())
            self.dragged.emit(p.x(), p.y(), 0)
        p = self.mapToView(ev.pos())
        self.dragged.emit(p.x(), p.y(), 2 if ev.isFinish() else 1)

    def wheelEvent(self, ev, axis=None):
        ev.ignore()


class ImageView(pg.GraphicsLayoutWidget):
    """Grey image with fixed auto contrast (no histogram: power is always 100 %) and numbered spots."""

    mouseMoved = QtCore.pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(PANEL_BG)
        self.vb = _ClickViewBox()
        self.addItem(self.vb)
        self.image_item = pg.ImageItem()
        self.image_item.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        self.vb.addItem(self.image_item)
        self.field_items: list[pg.PlotCurveItem] = []   # Wavefront's field: children of the image, clipped to it
        self.no_go_items = []                           # outside its square: a light red veil, then red hatching
        for alpha, style in ((40, QtCore.Qt.BrushStyle.SolidPattern), (150, QtCore.Qt.BrushStyle.BDiagPattern)):
            colour = QtGui.QColor(NO_GO)
            colour.setAlpha(alpha)
            item = QtWidgets.QGraphicsPathItem(self.image_item)
            item.setBrush(QtGui.QBrush(colour, style))
            item.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
            item.setZValue(-1)                          # under the outline, over the image
            item.setVisible(False)
            self.no_go_items.append(item)
        self.spot_item = pg.ScatterPlotItem(pxMode=False, symbol="o", pen=None, brush=pg.mkBrush(SPOT))
        self.spot_item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)     # clicks go to the view
        self.vb.addItem(self.spot_item)
        self.slm_item = pg.ScatterPlotItem(pxMode=False, symbol="o", pen=pg.mkPen(SLM_RING, width=3),
                                           brush=pg.mkBrush(None))
        self.slm_label = pg.TextItem("SLM", color=SLM_RING, anchor=(0.5, 0.0))
        self.slm_label.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Bold))
        for item in (self.slm_item, self.slm_label):
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            item.setZValue(10)                          # above the spots, below their numbers
            self.vb.addItem(item)
        self.slm_label.hide()
        self.labels: list[pg.TextItem] = []
        self.scene().sigMouseMoved.connect(self._on_mouse)

    def set_image(self, image: np.ndarray):
        lo, hi = np.percentile(image, [1, 99.7])
        self.image_item.setImage(image, levels=(lo, hi if hi > lo else lo + 1))
        self.vb.autoRange(items=[self.image_item])

    def show_placeholder(self):
        self.image_item.setImage(camera_target(), levels=(0, 255))
        self.vb.autoRange(items=[self.image_item])

    def set_field(self, lines):
        """Wavefront's field outline, [(kind, (N, 2) image px polyline)], drawn on the image and clipped to it, the
        image outside the "square" hatched as a no-go zone; None hides it."""
        lines = lines or []
        while len(self.field_items) < len(lines):
            item = pg.PlotCurveItem()
            item.setParentItem(self.image_item)
            self.field_items.append(item)
        for k, item in enumerate(self.field_items):
            if k < len(lines):
                kind, pts = lines[k]
                item.setData(pts[:, 0], pts[:, 1], pen=FIELD_PENS[kind])
            item.setVisible(k < len(lines))
        square = next((pts for kind, pts in lines if kind == "square"), None)
        if square is not None:
            outside = QtGui.QPainterPath()
            outside.setFillRule(QtCore.Qt.FillRule.OddEvenFill)        # the image minus the square
            outside.addRect(self.image_item.boundingRect().adjusted(-1, -1, 1, 1))
            outside.addPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in square]))
            for item in self.no_go_items:
                item.setPath(outside)
        for item in self.no_go_items:
            item.setVisible(square is not None)

    def set_spots(self, spots, diameter_px: float):
        for lbl in self.labels:
            self.vb.removeItem(lbl)
        self.labels = []
        pts = np.asarray(spots, dtype=float).reshape(-1, 2)
        self.spot_item.setData(pos=pts, size=diameter_px)
        for k, (x, y) in enumerate(pts):
            lbl = pg.TextItem(str(k + 1), color=SPOT, anchor=(0.5, 1.0))
            lbl.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold))
            lbl.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            lbl.setZValue(20)
            lbl.setPos(x, y - diameter_px / 2)
            self.vb.addItem(lbl)
            self.labels.append(lbl)

    def set_slm_spot(self, xy, diameter_px: float = 0.0):
        """Ring around the spot whose phase mask is on the SLM; None hides it."""
        if xy is None:
            self.slm_item.clear()
            self.slm_label.hide()
            return
        ring = diameter_px + 26
        self.slm_item.setData(pos=[xy], size=ring)
        self.slm_label.setPos(xy[0], xy[1] + ring / 2)
        self.slm_label.show()

    def _on_mouse(self, pos):
        if self.vb.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.mouseMoved.emit(p.x(), p.y())


class FolderEdit(QtWidgets.QWidget):
    """Folder path (typed or picked with …); ``changed`` when it is edited."""

    changed = QtCore.pyqtSignal(str)

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit(value)
        self.edit.setToolTip(value)
        self.edit.editingFinished.connect(lambda: self._set(self.edit.text()))
        btn = QtWidgets.QPushButton("…")
        btn.setFixedWidth(30)
        btn.setStyleSheet("padding: 4px 0;")
        btn.clicked.connect(self._browse)
        row.addWidget(self.edit, stretch=1)
        row.addWidget(btn)

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder", self.edit.text())
        if folder:
            self._set(str(Path(folder)))

    def _set(self, folder: str):
        self.edit.setText(folder.strip())
        self.edit.setToolTip(folder.strip())
        self.changed.emit(folder.strip())


# ----------------------------------------------------------------------------- window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: Config, config_path=DEFAULT_CONFIG_PATH):
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.image: np.ndarray | None = None
        self.spots: list[tuple[float, float]] = []      # camera px
        self.undo_stack: list[list[tuple[float, float]]] = []
        self.dragging: int | None = None                # spot being dragged
        self.help_dialog: QtWidgets.QDialog | None = None
        self.last_spot_bin: Path | None = None
        self.slm_spot: dict | None = None               # where the mask loaded on the SLM was clicked
        self.shutter = Shutter(config.nidaq_device, config.shutter_ao)
        self.jobs: list[tuple[str, str, Path]] = []      # phase masks still to compute
        self.generation: dict | None = None
        self.attempt = 1                                # sends of the current mask script
        self.job_t0 = 0.0
        self.job_taken_t: float | None = None           # when Wavefront took the current mask script
        self.cleanup: dict | None = None                # aborted generation, swept once Wavefront is done
        self.load: dict | None = None                   # Load waiting for Wavefront: name, js, attempt
        self._load_t0 = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.setWindowTitle("Oriole")
        self.resize(1500, 900)
        self._build()
        self.redraw_spots()
        self.refresh_masks()
        self.log("Oriole ready: load an image, click spots (drag to move, right-click removes; ? for help).")

    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(10)
        root.addLayout(cols, stretch=1)

        # visual (DMD)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(_title("VISUAL · DMD", VISUAL_COLOUR))
        dmd = QtWidgets.QGroupBox("Settings")
        form = QtWidgets.QFormLayout(dmd)
        self.spin_rows = QtWidgets.QSpinBox()
        self.spin_cols = QtWidgets.QSpinBox()
        for sp, value in ((self.spin_rows, self.config.dmd_height), (self.spin_cols, self.config.dmd_width)):
            sp.setRange(8, 4096)
            sp.setSuffix(" px")
            sp.setValue(value)
            sp.valueChanged.connect(self._on_dmd)
        self.spin_dmd_px = QtWidgets.QDoubleSpinBox()
        self.spin_dmd_px.setRange(0.01, 100)
        self.spin_dmd_px.setDecimals(3)
        self.spin_dmd_px.setSuffix(" µm")
        self.spin_dmd_px.setValue(self.config.pixel_size_um)
        self.spin_dmd_px.valueChanged.connect(self._on_dmd)
        form.addRow("rows", self.spin_rows)
        form.addRow("columns", self.spin_cols)
        form.addRow("pixel size", self.spin_dmd_px)
        left.addWidget(dmd)
        self.visual_box = CalibrationBox(self.config.visual)
        self.visual_box.changed.connect(self._on_calibration)
        left.addWidget(self.visual_box)
        left.addStretch(1)
        self.btn_extract = _action_button("EXTRACT BIN")
        self.btn_extract.clicked.connect(self.extract)
        self.btn_merge = QtWidgets.QPushButton("Merge into bin…")
        self.btn_merge.clicked.connect(self.merge)
        left.addWidget(self.btn_extract)
        left.addWidget(self.btn_merge)
        cols.addWidget(_card(left, "visualCard", VISUAL_COLOUR, 290))

        # image
        mid = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        btn = QtWidgets.QPushButton("Load image…")
        btn.clicked.connect(self.load_image_dialog)
        bar.addWidget(btn)
        bar.addWidget(QtWidgets.QLabel("  Spot Ø"))
        self.spin_diam = QtWidgets.QDoubleSpinBox()
        self.spin_diam.setRange(0.5, 2000)
        self.spin_diam.setDecimals(1)
        self.spin_diam.setSuffix(" µm")
        self.spin_diam.setValue(self.config.spot_diameter_um)
        self.spin_diam.valueChanged.connect(self._on_diameter)
        bar.addWidget(self.spin_diam)
        for text, slot in (("Undo", self.undo), ("Clear", self.clear)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_count = QtWidgets.QLabel()
        bar.addWidget(self.lbl_count)
        self.btn_help = QtWidgets.QPushButton("?")
        self.btn_help.setFixedSize(28, 28)
        self.btn_help.setToolTip("Help")
        self.btn_help.setStyleSheet(f"QPushButton {{ background: {ACCENT}; color: white; border-radius: 14px;"
                                    f" padding: 0; font: bold 12pt 'Segoe UI'; }}"
                                    f" QPushButton:hover {{ background: #3D82BF; }}")
        self.btn_help.clicked.connect(self.show_help)
        bar.addWidget(self.btn_help)
        mid.addLayout(bar)
        self.view = ImageView()
        self.view.vb.clicked.connect(self._on_click)
        self.view.vb.dragged.connect(self._on_drag)
        self.view.mouseMoved.connect(self._on_mouse)
        self.view.show_placeholder()
        mid.addWidget(self.view, stretch=1)
        self.lbl_mouse = QtWidgets.QLabel(" ")
        self.lbl_mouse.setStyleSheet(f"color: {DIM};")
        mid.addWidget(self.lbl_mouse)
        cols.addWidget(_card(mid, "imageCard", IMAGE_COLOUR), stretch=1)

        # holo (SLM)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(_title("HOLO · SLM", HOLO_COLOUR))
        setup = QtWidgets.QGroupBox("Settings")
        col = QtWidgets.QVBoxLayout(setup)
        col.addWidget(QtWidgets.QLabel("Wavefront folder"))
        self.edit_wfd_root = FolderEdit(self.config.wfd_root)
        self.edit_wfd_root.setToolTip("holds script\\ and GeneratedPhasemasks\\")
        self.edit_wfd_root.changed.connect(self._on_wavefront_root)
        col.addWidget(self.edit_wfd_root)
        self.lbl_script_dir, self.btn_sweep_scripts = self._derived_folder(
            col, "script folder", "Sweep the script folder clean", self.sweep_script_folder)
        self.lbl_output_dir, self.btn_open_masks, self.btn_sweep_masks = self._derived_folder(
            col, "phase mask folder", "Sweep the phase mask folder clean (asks first)", self.sweep_mask_folder,
            self.open_mask_folder)
        self._show_wavefront_dirs()
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("shutter"))
        self.edit_device = QtWidgets.QLineEdit(self.config.nidaq_device)
        self.edit_device.editingFinished.connect(self._on_shutter_output)
        self.spin_ao = QtWidgets.QSpinBox()
        self.spin_ao.setRange(0, 31)
        self.spin_ao.setPrefix("ao")
        self.spin_ao.setValue(self.config.shutter_ao)
        self.spin_ao.valueChanged.connect(self._on_shutter_output)
        row.addWidget(self.edit_device, stretch=1)
        row.addWidget(self.spin_ao)
        col.addLayout(row)
        self.chk_slm_field = QtWidgets.QCheckBox("SLM field on the image")
        self.chk_slm_field.setToolTip("Wavefront's square, the no-go zone outside it, its 20 % and 90 % power circles "
                                      "and its centre")
        self.chk_slm_field.setChecked(self.config.show_slm_field)
        self.chk_slm_field.toggled.connect(self._on_slm_field)
        col.addWidget(self.chk_slm_field)
        right.addWidget(setup)
        self.lbl_image_warning = QtWidgets.QLabel()
        self.lbl_image_warning.setWordWrap(True)
        self.lbl_image_warning.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_image_warning.setStyleSheet("background: #B00000; color: white; font: bold 14pt 'Segoe UI';"
                                             "padding: 8px; border-radius: 4px;")
        self.lbl_image_warning.hide()
        right.addWidget(self.lbl_image_warning)
        self.holo_box = CalibrationBox(self.config.holo)
        self.holo_box.changed.connect(self._on_calibration)
        right.addWidget(self.holo_box)
        manual = QtWidgets.QGroupBox("Manual")
        grid = QtWidgets.QGridLayout(manual)
        grid.addWidget(QtWidgets.QLabel("mask"), 0, 0)
        self.combo_mask = QtWidgets.QComboBox()
        self.combo_mask.setToolTip("Pattern<n> masks found in the phase mask folder")
        grid.addWidget(self.combo_mask, 0, 1)
        self.btn_load_mask = QtWidgets.QPushButton("Load")
        self.btn_load_mask.clicked.connect(self.load_selected_mask)
        grid.addWidget(self.btn_load_mask, 0, 2)
        self.lbl_shutter = QtWidgets.QLabel()
        self.lbl_shutter.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.btn_open = QtWidgets.QPushButton("Open")
        self.btn_open.clicked.connect(self.open_shutter)
        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.clicked.connect(self.close_shutter)
        grid.addWidget(self.lbl_shutter, 1, 0, 1, 3)
        shutter_row = QtWidgets.QHBoxLayout()
        shutter_row.addWidget(self.btn_open)
        shutter_row.addWidget(self.btn_close)
        grid.addLayout(shutter_row, 2, 0, 1, 3)
        right.addWidget(manual)
        right.addStretch(1)
        self.btn_generate = _action_button("GENERATE PHASE MASKS")
        self.btn_generate.clicked.connect(lambda: self.stop_generation() if self.jobs else self.generate())
        right.addWidget(self.btn_generate)
        cols.addWidget(_card(right, "holoCard", HOLO_COLOUR, 290))
        self._show_shutter()
        self.watcher = QtCore.QFileSystemWatcher(self)        # mask list and button follow the folder
        self.watcher.directoryChanged.connect(lambda _: self.refresh_masks())
        self._watch_folders()

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFixedHeight(130)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        self.log_view.setStyleSheet("background: #0A0A0A; color: #00FF00; border: none;")
        root.addWidget(self.log_view)

    @staticmethod
    def _derived_folder(col: QtWidgets.QVBoxLayout, label: str, tip: str, sweep, open_folder=None):
        """Read-only path of a folder inside the Wavefront folder, with its broom (and 📂 to open it)."""
        col.addWidget(QtWidgets.QLabel(label))
        row = QtWidgets.QHBoxLayout()
        path = QtWidgets.QLineEdit()
        path.setReadOnly(True)
        path.setStyleSheet("color: #9A9A9A;")
        row.addWidget(path, stretch=1)
        buttons = []
        for text, slot, button_tip in (("📂", open_folder, "Open it in the file explorer"), ("🧹", sweep, tip)):
            if slot is None:
                continue
            btn = QtWidgets.QPushButton(text)
            btn.setFixedWidth(30)
            btn.setStyleSheet("padding: 2px 0; font: 12pt 'Segoe UI Emoji';")
            btn.setToolTip(button_tip)
            btn.clicked.connect(slot)
            row.addWidget(btn)
            buttons.append(btn)
        col.addLayout(row)
        return (path, *buttons)

    def open_mask_folder(self):
        folder = Path(self.config.wfd_output_dir)
        if not folder.is_dir():
            self.log(f"folder not found: {folder}", "warn")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _show_wavefront_dirs(self):
        for edit, folder in ((self.lbl_script_dir, self.config.wfd_script_dir),
                             (self.lbl_output_dir, self.config.wfd_output_dir)):
            edit.setText(folder)            # cursor at the end: the sub-folder name stays visible
            edit.setToolTip(folder)

    def show_help(self):
        if self.help_dialog is None:
            self.help_dialog = QtWidgets.QDialog(self)
            self.help_dialog.setWindowTitle("Oriole help")
            self.help_dialog.resize(620, 640)
            layout = QtWidgets.QVBoxLayout(self.help_dialog)
            text = QtWidgets.QTextBrowser()
            text.setHtml(HELP_HTML)
            text.setStyleSheet(f"background: {PANEL_BG}; color: {TEXT}; border: none; font: 10pt 'Segoe UI';")
            layout.addWidget(text)
            btn = QtWidgets.QPushButton("Close")
            btn.clicked.connect(self.help_dialog.close)
            layout.addWidget(btn)
        self.help_dialog.show()
        self.help_dialog.raise_()

    def log(self, text: str, level: str = "info"):
        big = "; font-size: 14pt; font-weight: bold" if level == "alarm" else ""
        self.log_view.appendHtml(f'<span style="color:{LOG_COLOURS[level]}{big}">'
                                 f'{_dt.datetime.now():%H:%M:%S}  {html.escape(text)}</span>')

    def save_config(self):
        self.config.save(self.config_path)

    # ------------------------------------------------------------------ image + spots
    def load_image_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Microscope image", self.config.last_dir or str(Path.home()),
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)")
        if not path:
            return
        try:
            image = load_gray(path)
        except Exception as e:  # noqa: BLE001
            self.log(f"cannot load {path}: {e}", "error")
            return
        self.config.last_dir = str(Path(path).parent)
        self.set_image(image, path)

    def set_image(self, image: np.ndarray, source: str = ""):
        """Show a camera image (from a file now; Micro-Manager snap / live later)."""
        self.image = np.asarray(image, dtype=np.float32)
        self.view.set_image(self.image)
        self.redraw_field()
        h, w = self.image.shape
        self.log(f"image {Path(source).name or 'set'}: {w} x {h} px")
        if (w, h) != EPI_SIZE:
            ew, eh = EPI_SIZE
            self.lbl_image_warning.setText(f"WRONG IMAGE SIZE\n{w} × {h} px")
            self.lbl_image_warning.show()
            if (w, h) == (2 * ew, 2 * eh):
                why = "This is the unbinned camera size: the epi image was not binned 2x2"
            else:
                why = f"The epi image was probably not binned 2x2 (unbinned it would be {2 * ew} x {2 * eh})"
            self.log(f"WRONG IMAGE SIZE: {w} x {h} px instead of {ew} x {eh}. {why}. Bin it 2x2 and load it "
                     f"again: both calibrations are made for {ew} x {eh} epi images, so spot positions, sizes "
                     f"and everything extracted or generated from this image would be wrong.", "error")
        else:
            self.lbl_image_warning.hide()

    def _on_click(self, x: float, y: float, right: bool):
        if self.image is None:
            self.log("load an image first", "warn")
            return
        if right:
            k = self._spot_at(x, y)
            if k is not None:
                self._push_undo()
                del self.spots[k]
                self.redraw_spots()
            return
        h, w = self.image.shape
        if 0 <= x < w and 0 <= y < h:
            self._push_undo()
            self.spots.append((x, y))
            self.redraw_spots()

    def _spot_at(self, x: float, y: float) -> int | None:
        """Index of the spot under (x, y), if any."""
        if not self.spots:
            return None
        d = np.hypot(*(np.asarray(self.spots) - [x, y]).T)
        k = int(d.argmin())
        return k if d[k] <= max(self.diameter_px() / 2, 5) else None

    def _on_drag(self, x: float, y: float, phase: int):
        """Drag a spot with the left button; dragging elsewhere does nothing (the view is fixed)."""
        if self.image is None:
            return
        if phase == 0:
            self.dragging = self._spot_at(x, y)
            if self.dragging is not None:
                self._push_undo()
            return
        if self.dragging is None:
            return
        h, w = self.image.shape
        self.spots[self.dragging] = (min(max(x, 0.0), w - 1e-3), min(max(y, 0.0), h - 1e-3))
        self.redraw_spots()
        if phase == 2:
            self.dragging = None

    def _push_undo(self):
        self.undo_stack.append(list(self.spots))
        del self.undo_stack[:-50]

    def undo(self):
        if self.undo_stack:
            self.spots = self.undo_stack.pop()
            self.redraw_spots()

    def clear(self):
        if self.spots:
            self._push_undo()
            self.spots = []
            self.redraw_spots()

    def diameter_px(self, diameter_um: float | None = None) -> float:
        px_um = (self.config.visual.px_um + self.config.holo.px_um) / 2
        return (self.spin_diam.value() if diameter_um is None else diameter_um) / max(px_um, 1e-6)

    def redraw_spots(self):
        self.view.set_spots(self.spots, self.diameter_px())
        s = self.slm_spot
        self.view.set_slm_spot(None if s is None else (s["x_px"], s["y_px"]),
                               0.0 if s is None else self.diameter_px(s["diameter_um"]))
        self.lbl_count.setText(f"{len(self.spots)} spot{'s' if len(self.spots) != 1 else ''}  ")

    def _on_diameter(self, value: float):
        self.config.spot_diameter_um = value
        self.redraw_spots()

    def _on_calibration(self):
        self.config.visual_cal = self.visual_box.calibration().to_dict()
        self.config.holo_cal = self.holo_box.calibration().to_dict()
        self.redraw_spots()
        self.redraw_field()

    def _on_slm_field(self, show: bool):
        self.config.show_slm_field = show
        self.save_config()
        self.redraw_field()

    def redraw_field(self):
        """Wavefront's field on the image, through the holo calibration: its square in red, the image outside it
        hatched red (no-go zone), its 20 % efficiency circle in orange dashes, its 90 % one in yellow, its centre
        cross in light grey."""
        if self.image is None or not self.config.show_slm_field:
            self.view.set_field(None)
            return
        cal, shape = self.config.holo, self.image.shape
        self.view.set_field([(kind, cal.from_um(pts, shape)) for kind, pts in holo.field_outline()])

    def _on_mouse(self, x: float, y: float):
        if self.image is None:
            return
        over_spot = self.dragging is not None or self._spot_at(x, y) is not None
        self.view.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if over_spot else QtCore.Qt.CursorShape.CrossCursor)
        vx, vy = self.config.visual.to_um([(x, y)], self.image.shape)[0]
        hx, hy = self.config.holo.to_um([(x, y)], self.image.shape)[0]
        self.lbl_mouse.setText(f"x {x:.0f}  y {y:.0f} px    |    visual {vx:.1f}, {vy:.1f} µm    |    "
                               f"holo {hx:.1f}, {hy:.1f} µm")

    def _have_spots(self) -> bool:
        if self.image is None or not self.spots:
            self.log("load an image and click at least one spot first", "warn")
            return False
        return True

    # ------------------------------------------------------------------ visual
    def extract(self):
        if not self._have_spots():
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder for the spot bin",
                                                               self.config.last_dir or str(Path.home()))
        if not directory:
            return
        cal = self.config.visual
        try:
            path = visual.write_spot_bin(directory, self.spots, self.image.shape, self.spin_diam.value(),
                                         cal, self.config)
        except Exception as e:  # noqa: BLE001
            self.log(f"extract failed: {e}", "error")
            return
        self.config.last_dir = directory
        self.last_spot_bin = path
        self.save_config()
        self.log(f"wrote {path} ({len(self.spots)} spots: frame 2i = spot i, 2i+1 = its negative)")
        h, w = self.config.dmd_shape
        centres = visual.dmd_px(cal.to_um(self.spots, self.image.shape), self.config)
        outside = [k + 1 for k, (cx, cy) in enumerate(centres) if not (0 <= cx < w and 0 <= cy < h)]
        if outside:
            self.log(f"spots outside the DMD frame: {', '.join(map(str, outside))}", "warn")

    def merge(self):
        start = self.config.linked_bin or self.config.last_dir or str(Path.home())
        linked, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Linked bin (frames kept first)", start,
                                                          "Bin files (*.bin)")
        if not linked:
            return
        spot_bin = self.last_spot_bin
        if spot_bin is None or not spot_bin.exists():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Spot bin", self.config.last_dir or start,
                                                            "Bin files (*.bin)")
            if not path:
                return
            spot_bin = Path(path)
        linked = Path(linked)
        default = linked.with_name(f"{linked.stem}_{spot_bin.stem}.bin")
        out, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Merged bin", str(default), "Bin files (*.bin)")
        if not out:
            return
        try:
            m = visual.merge_bin(linked, spot_bin, out)
        except Exception as e:  # noqa: BLE001
            self.log(f"merge failed: {e}", "error")
            return
        self.config.linked_bin = str(linked)
        self.save_config()
        first = f"; spot 1 at frames {m['spots'][0]['pos_idx']}/{m['spots'][0]['neg_idx']}" if m["spots"] else ""
        self.log(f"wrote {out}: {m['n_linked_frames']} linked + "
                 f"{m['n_total_frames'] - m['n_linked_frames']} spot frames{first} (indices in .indices.json)")

    # ------------------------------------------------------------------ holo
    def client(self) -> holo.WavefrontClient:
        return holo.WavefrontClient(self.config.wfd_script_dir)

    def _purge_scripts(self, quiet: bool = False) -> bool:
        try:
            removed = self.client().purge()
        except OSError as e:
            self.log(f"cannot empty the script folder: {e}", "error")
            return False
        if removed and not quiet:
            self.log(f"script folder purged ({', '.join(removed)} left there by Wavefront)", "warn")
        return True

    def sweep_script_folder(self):
        if self.jobs or self.cleanup is not None:
            self.log("not now: Wavefront is computing masks from that folder", "warn")
            return
        try:
            removed = self.client().purge()
        except OSError as e:
            self.log(f"cannot empty the script folder: {e}", "error")
            return
        self.log(f"🧹 script folder swept clean ({', '.join(removed)})" if removed else "🧹 script folder already clean")

    def sweep_mask_folder(self):
        if self.jobs or self.cleanup is not None:
            self.log("not now: Wavefront is saving masks in that folder", "warn")
            return
        folder = Path(self.config.wfd_output_dir)
        files = holo.files_in(folder)
        if not files:
            self.log("🧹 phase mask folder already clean")
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Sweep the phase mask folder",
            f"Delete the {len(files)} files ({len(holo.available_masks(folder))} masks, dated {_dates(files)}) in\n"
            f"{folder}?")
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            removed = holo.empty_folder(folder)
        except OSError as e:
            self.log(f"cannot empty {folder}: {e}", "error")
        else:
            self.log(f"🧹 phase mask folder swept clean ({len(removed)} files)")
        self.refresh_masks()

    def refresh_masks(self):
        """Mask numbers found in the phase mask folder; Generate is STOP while generating, locked while cleaning
        up after an abort, yellow while a Wavefront folder is not empty."""
        out_dir = self.config.wfd_output_dir
        current = self.combo_mask.currentText()
        self.combo_mask.clear()
        self.combo_mask.addItems([str(n) for n in holo.available_masks(out_dir)])
        if self.combo_mask.findText(current) >= 0:
            self.combo_mask.setCurrentText(current)
        self.btn_load_mask.setEnabled(self.combo_mask.count() > 0 and not self.jobs and self.cleanup is None)
        self.btn_generate.setEnabled(self.cleanup is None)
        if self.cleanup is not None:
            self.btn_generate.setText("CLEANING UP…")      # poll() counts the seconds down
            self.btn_generate.setToolTip("waiting for Wavefront to finish before sweeping")
            return
        if self.jobs:
            self.btn_generate.setText("■ STOP")
            self.btn_generate.setStyleSheet(STOP_STYLE)
            self.btn_generate.setToolTip("stop the generation (both Wavefront folders are emptied after it)")
            return
        blocked = bool(holo.files_in(self.config.wfd_script_dir) or holo.files_in(out_dir))
        self.btn_generate.setText("GENERATE PHASE MASKS")
        self.btn_generate.setStyleSheet(BLOCKED_STYLE if blocked else ACTION_STYLE)
        self.btn_generate.setToolTip("a Wavefront folder is not empty: Generate asks to sweep it" if blocked else "")

    def _watch_folders(self):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        for folder in (self.config.wfd_script_dir, self.config.wfd_output_dir):
            if Path(folder).is_dir():
                self.watcher.addPath(folder)

    def generate(self):
        if self.jobs or self.cleanup is not None:
            self.log("Wavefront is still computing the previous masks", "warn")
            return
        out_dir = Path(self.config.wfd_output_dir)
        if not out_dir.is_dir() or not self.client().script_dir.is_dir():
            self.log(f"Wavefront folders not found ({self.config.wfd_script_dir}, {out_dir})", "error")
            return
        if not self._have_spots() or not self._sweep_before_generating():
            return
        cal, d = self.config.holo, self.spin_diam.value()
        spots_um = cal.to_um(self.spots, self.image.shape)
        fits = [holo.field_fit(x, y, d) for x, y in spots_um]
        outside = [k for k, fit in enumerate(fits) if fit != "in"]
        off = [f"spot {k + 1}{' partly' if fits[k] == 'partly' else ''} at ({spots_um[k][0]:.1f}, "
               f"{spots_um[k][1]:.1f}) µm" for k in outside]
        if off:
            choice = self._choose(
                "Generate phase masks",
                f"Outside the SLM field (Wavefront: ±{holo.FIELD_HALF_UM} µm):\n\n" + "\n".join(f"• {s}" for s in off) +
                "\n\nTheir masks would hold no spot, or a cut one. A right click on a spot removes it; Remove spots "
                "removes them all (Undo brings them back) and generates the others.",
                {"remove": "Remove spots and generate", "anyway": "Generate anyway"})
            if choice is None:
                self.log(f"not generating: {', '.join(off)} outside the SLM field (a right click on a spot removes "
                         f"it)", "warn")
                return
            if choice == "remove":
                self._push_undo()
                self.spots = [p for k, p in enumerate(self.spots) if k not in outside]
                self.redraw_spots()
                self.log(f"removed {', '.join(f'spot {k + 1}' for k in outside)}: outside the SLM field (Undo brings "
                         f"them back)", "warn")
                if not self.spots:
                    self.log("not generating: no spot left in the SLM field", "warn")
                    return
                spots_um = cal.to_um(self.spots, self.image.shape)
                fits, off = ["in"] * len(self.spots), []
        names = [holo.mask_name(k + 1) for k in range(len(spots_um))]
        self.jobs = [(name, holo.mask_script(name, x, y, d), holo.mask_png(out_dir, name))
                     for name, (x, y) in zip(names, spots_um)]
        self.generation = {"spots_px": list(self.spots), "spots_um": spots_um, "diameter": d, "cal": cal,
                           "names": names, "fits": fits}
        if off:
            self.log(f"⚠ OUTSIDE THE SLM FIELD (Wavefront: ±{holo.FIELD_HALF_UM} µm): {', '.join(off)}. Their masks "
                     f"hold no spot or a cut one; generating anyway", "alarm")
        self.load = None                                # a Load still waiting is dropped: its script was purged
        self.slm_spot = None                            # Wavefront computes on the SLM: no longer the loaded mask
        self.redraw_spots()
        self.refresh_masks()                            # Generate becomes STOP, Load is disabled
        self.attempt = 1
        self._submit()

    def _choose(self, title: str, text: str, choices: dict[str, str]) -> str | None:
        """A warning with a button per choice ({key: label}) and Cancel, the default: the key clicked, or None."""
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Warning, title, text, parent=self)
        buttons = {box.addButton(label, QtWidgets.QMessageBox.ButtonRole.AcceptRole): key
                   for key, label in choices.items()}
        box.setDefaultButton(box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel))
        box.exec()
        return buttons.get(box.clickedButton())

    def _sweep_before_generating(self) -> bool:
        """Both Wavefront folders must be empty: a warning says what is left and sweeps it on a yes."""
        scripts, files = holo.files_in(self.config.wfd_script_dir), holo.files_in(self.config.wfd_output_dir)
        if not scripts and not files:
            return True
        left = []
        if scripts:
            left.append(f"script folder: {', '.join(p.name for p in scripts)} (not taken by Wavefront: is auto load "
                        f"on?)")
        if files:
            left.append(f"phase mask folder: {_n(len(files), 'file')} "
                        f"({_n(len(holo.available_masks(self.config.wfd_output_dir)), 'mask')}), dated {_dates(files)}")
        self.log(f"Wavefront folders not empty: {'; '.join(left)}", "warn")
        yes, cancel = QtWidgets.QMessageBox.StandardButton.Yes, QtWidgets.QMessageBox.StandardButton.Cancel
        answer = QtWidgets.QMessageBox.warning(
            self, "Generate phase masks",
            "These Wavefront folders are not empty:\n\n" + "\n".join(f"• {line}" for line in left) +
            "\n\nSweep them and generate?", yes | cancel, cancel)
        if answer != yes:
            self.log("not generating: the Wavefront folders must be empty", "warn")
            return False
        try:
            swept = holo.empty_folder(self.config.wfd_script_dir) + holo.empty_folder(self.config.wfd_output_dir)
        except OSError as e:
            self.log(f"cannot sweep the Wavefront folders: {e}", "error")
            return False
        still = holo.files_in(self.config.wfd_script_dir) + holo.files_in(self.config.wfd_output_dir)
        if still:                                       # written again at once: Wavefront is still busy
            self.log(f"not generating: {', '.join(p.name for p in still)} back in the Wavefront folders right after "
                     f"the sweep (Wavefront still saving?)", "error")
            return False
        self.log(f"🧹 Wavefront folders swept ({_n(len(swept), 'file')})")
        return True

    def _submit(self):
        name, js, _ = self.jobs[0]
        try:
            self.client().submit(js)
        except Exception as e:  # noqa: BLE001
            self._abort(f"cannot send the {name} script: {e}")
            return
        self.job_t0 = time.time()
        self.job_taken_t = None
        self.timer.start()
        names = self.generation["names"]
        again = f", attempt {self.attempt}/{holo.MAX_ATTEMPTS}" if self.attempt > 1 else ""
        self.log(f"Wavefront: computing {name} ({names.index(name) + 1} of {len(names)}{again})")

    def poll(self):
        """Next script once Wavefront took this one and saved its mask (with the right spot); else this one again."""
        if self.cleanup is not None:
            self._clean_up()
            return
        if not self.jobs:
            self.timer.stop()
            return
        name, _, png = self.jobs[0]
        now = time.time()
        if self.job_taken_t is None:
            if self.client().busy:
                if now - self.job_t0 > holo.NOT_READ_S:
                    self._retry(f"Wavefront did not take the {name} script within {holo.NOT_READ_S:.0f} s",
                                f"{AUTOLOAD_HINT}, or restart Wavefront IV")
                return
            self.job_taken_t = now
        if holo.mask_saved(png, self.job_t0):
            g = self.generation
            k = g["names"].index(name)
            (x, y), d = g["spots_um"][k], g["diameter"]
            # a spot (partly) outside the field was said at Generate: nothing to check on its mask
            drawn = holo.drawn_spot(self.config.wfd_output_dir, name, d) if g["fits"][k] == "in" else None
            if drawn is not None and math.isnan(drawn[0]):
                self.log(f"⚠ {name} HOLDS NO SPOT: Wavefront drew nothing for spot {k + 1} at ({x:.1f}, {y:.1f}) µm "
                         f"(outside its field?)", "alarm")
            elif drawn is not None and holo.off_spot(drawn, x, y, d):
                self._abort(f"{name} holds a spot drawn at ({drawn[0]:.1f}, {drawn[1]:.1f}) µm, not at ({x:.1f}, "
                            f"{y:.1f}) µm (spot {k + 1})", "restart Wavefront IV and generate again")
                return
            self._next_job()
        elif now - self.job_taken_t > holo.MASK_WAIT_S:
            self._retry(f"Wavefront took the {name} script but did not save {png.name} within "
                        f"{holo.MASK_WAIT_S:.0f} s", "restart Wavefront IV and generate again")

    def _next_job(self):
        self.jobs.pop(0)
        self.attempt = 1
        if self.jobs:
            self._submit()
            return
        g = self.generation
        rec = holo.write_spot_record(self.config.wfd_output_dir, g["spots_px"], g["spots_um"], g["diameter"], g["cal"])
        self._finish(f"{_n(len(g['names']), 'phase mask')} saved in {self.config.wfd_output_dir} "
                     f"(spot i = {holo.PREFIX}i, each checked against its drawn spot; {rec.name})")

    def _retry(self, problem: str, fix: str):
        """Erase the script and send the same one again, MAX_ATTEMPTS sends in all; then abort."""
        self.timer.stop()
        problem += f" (attempt {self.attempt}/{holo.MAX_ATTEMPTS})"
        if self._purge_scripts(quiet=True) and self.attempt < holo.MAX_ATTEMPTS:
            self.log(f"{problem}: script erased, sending it again", "warn")
            self.attempt += 1
            self._submit()
        else:
            self._abort(problem, fix)

    def stop_generation(self):
        if self.jobs:
            self._abort("STOP pressed", level="warn")

    def _abort(self, reason: str, fix: str = "", level: str = "error"):
        """Send nothing more; ABORT_WAIT_S later (Wavefront may still be computing) _clean_up sweeps."""
        self.cleanup = {"t0": time.time(), "fix": fix, "level": level, "sweeps": 0, "swept": []}
        self.jobs = []
        self.generation = None
        self.log(f"{reason}: generation aborted; cleaning up in {holo.ABORT_WAIT_S:.0f} s, Wavefront may still be "
                 f"computing", level)
        self.refresh_masks()
        self.timer.start()

    def _clean_up(self, wait: bool = True):
        """Back to the normal state: both Wavefront folders empty (they were when the generation started), Generate
        ready. Swept ABORT_WAIT_S after the abort, then every second while a file is left (Wavefront still saving,
        a file still open), MAX_SWEEPS sweeps at most."""
        c = self.cleanup
        left = c["t0"] + holo.ABORT_WAIT_S - time.time()
        if wait and left > 0:
            self.btn_generate.setText(f"CLEANING UP IN {math.ceil(left)} s")
            return
        folders = (self.config.wfd_script_dir, self.config.wfd_output_dir)
        for p in holo.files_in(folders[0]) + holo.files_in(folders[1]):
            try:
                p.unlink()
                c["swept"].append(p.name)
            except OSError:
                pass
        c["sweeps"] += 1
        still = holo.files_in(folders[0]) + holo.files_in(folders[1])
        if still and wait and c["sweeps"] < holo.MAX_SWEEPS:
            c["t0"] = time.time() + 1 - holo.ABORT_WAIT_S   # not empty yet: sweep again in 1 s
            self.btn_generate.setText("CLEANING UP…")
            return
        self.timer.stop()
        self.cleanup = None
        fix = f"; {c['fix']}" if c["fix"] else ""
        if still:
            self.log(f"cleanup after the abort incomplete: {', '.join(p.name for p in still)} still in the Wavefront "
                     f"folders after {_n(c['sweeps'], 'sweep')}: sweep them with the 🧹{fix}", "error")
        else:
            again = f" ({c['sweeps']} sweeps)" if c["sweeps"] > 1 else ""
            files = f": {', '.join(c['swept'])}" if c["swept"] else ""
            self.log(f"cleaned up after the abort{again}: both Wavefront folders emptied "
                     f"({_n(len(c['swept']), 'file')}{files}){fix}", c["level"])
        self.refresh_masks()

    def _finish(self, message: str, level: str = "info"):
        self.timer.stop()
        self.jobs = []
        self.generation = None
        self.log(message, level)
        self.refresh_masks()

    def load_selected_mask(self):
        if not self.combo_mask.currentText():
            self.log(f"no {holo.PREFIX}<n> mask in {self.config.wfd_output_dir}", "warn")
            return
        n = int(self.combo_mask.currentText())
        name = holo.mask_name(n)
        png = holo.mask_png(self.config.wfd_output_dir, name)
        if not self._purge_scripts():
            return
        self.load = {"name": name, "js": holo.load_script(png), "attempt": 1}
        if not self._send_load():
            return
        self.log(f"SLM: loading {name}")
        self.slm_spot = holo.recorded_spot(self.config.wfd_output_dir, n)
        if self.slm_spot is None:
            self.log(f"{name} is not in an up to date {holo.RECORD_NAME}: its spot cannot be shown on the image",
                     "warn")
        self.redraw_spots()

    def _send_load(self) -> bool:
        try:
            self.client().submit(self.load["js"])
        except Exception as e:  # noqa: BLE001
            self.log(f"cannot load {self.load['name']}: {e}", "error")
            self.load = None
            return False
        self._load_t0 = time.time()
        QtCore.QTimer.singleShot(int(holo.NOT_READ_S * 1000) + 100, self.check_load_taken)
        return True

    def check_load_taken(self):
        """A load script still in the folder after NOT_READ_S is erased and sent again, MAX_ATTEMPTS sends in all."""
        if self.load is None or time.time() - self._load_t0 < holo.NOT_READ_S:
            return                                      # taken, dropped, or sent again since
        if not self.client().busy:
            self.load = None                            # Wavefront took it
            return
        name, attempt = self.load["name"], self.load["attempt"]
        problem = (f"Wavefront did not take the script loading {name} within {holo.NOT_READ_S:.0f} s "
                   f"(attempt {attempt}/{holo.MAX_ATTEMPTS})")
        if self._purge_scripts(quiet=True) and attempt < holo.MAX_ATTEMPTS:
            self.log(f"{problem}: script erased, sending it again", "warn")
            self.load["attempt"] += 1
            self._send_load()
            return
        self.load = None
        self.slm_spot = None                            # not loaded: what the SLM shows is unknown
        self.redraw_spots()
        self.log(f"{problem}: {name} not loaded; {AUTOLOAD_HINT}, or restart Wavefront IV", "error")

    # ------------------------------------------------------------------ shutter
    def open_shutter(self):
        try:
            self.shutter.open()
            self.log(f"shutter OPEN ({self.shutter.channel})", "warn")
        except ShutterError as e:
            self.log(str(e), "error")
        self._show_shutter()

    def close_shutter(self):
        try:
            self.shutter.close()
            self.log("shutter closed")
        except ShutterError as e:
            self.log(str(e), "error")
        if not self.shutter.is_open:        # an output changed while open applies from now on
            self.shutter = Shutter(self.config.nidaq_device, self.config.shutter_ao)
        self._show_shutter()

    def _show_shutter(self):
        if self.shutter.is_open:
            self.lbl_shutter.setText("Shutter OPEN")
            self.lbl_shutter.setStyleSheet("background: #FF4400; color: white; font-weight: bold; padding: 4px;")
        else:
            self.lbl_shutter.setText("Shutter closed")
            self.lbl_shutter.setStyleSheet(f"background: {BTN}; color: {TEXT}; padding: 4px;")

    # ------------------------------------------------------------------ settings boxes / exit
    def _on_dmd(self, *_):
        self.config.dmd_height, self.config.dmd_width = self.spin_rows.value(), self.spin_cols.value()
        self.config.pixel_size_um = self.spin_dmd_px.value()
        self.save_config()

    def _on_wavefront_root(self, root: str):
        self.config.wfd_root = root
        self.save_config()
        self._show_wavefront_dirs()
        for d in (self.config.wfd_script_dir, self.config.wfd_output_dir):
            if not Path(d).is_dir():
                self.log(f"folder not found: {d}", "warn")
        self._watch_folders()
        self.refresh_masks()

    def _on_shutter_output(self, *_):
        self.config.nidaq_device = self.edit_device.text().strip() or "Dev1"
        self.config.shutter_ao = self.spin_ao.value()
        self.save_config()
        if self.shutter.is_open:            # an open shutter keeps its output until closed
            self.log("the new shutter output applies once the shutter is closed", "warn")
            return
        self.shutter = Shutter(self.config.nidaq_device, self.config.shutter_ao)

    def closeEvent(self, ev):
        if self.jobs:
            self._abort("window closed", level="warn")
        if self.cleanup is not None:
            self._clean_up(wait=False)                  # the window is closing: sweep now
        if self.shutter.is_open:
            self.close_shutter()
        self.timer.stop()
        self.save_config()
        super().closeEvent(ev)


def run(config: Config | None = None, config_path=DEFAULT_CONFIG_PATH) -> int:
    QtCore.QLocale.setDefault(QtCore.QLocale.c())      # decimal point in every number box, as in DHparams.m
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    win = MainWindow(config or Config.load(config_path), config_path)
    win.show()
    return app.exec()
