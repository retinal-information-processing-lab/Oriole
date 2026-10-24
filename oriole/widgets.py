"""Widgets of the window: the image with its spots and Wavefront's field, the calibration box, small factories."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from .calibration import EPI_SIZE, Calibration
from .theme import ACCENT, ACTION_STYLE, CROSS, INNER_CIRCLE, NO_GO, OUTER_CIRCLE, PANEL_BG, SLM_RING, SPOT

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

FIELD_PENS = {"square": pg.mkPen(NO_GO, width=2),       # Wavefront's field outline; dashed: its 20 % (outer) and
              **{kind: pg.mkPen(colour, width=1.5, style=QtCore.Qt.PenStyle.DashLine)     # 90 % (inner) circles
                 for kind, colour in (("outer", OUTER_CIRCLE), ("inner", INNER_CIRCLE), ("cross", CROSS))}}
NO_MOUSE = QtCore.Qt.MouseButton.NoButton


# ----------------------------------------------------------------------------- factories
def title_label(text: str, colour: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setFont(QtGui.QFont("Consolas", 12, QtGui.QFont.Weight.Bold))
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background: {colour}; color: white; padding: 6px; border-radius: 4px;")
    return lbl


def card(layout: QtWidgets.QLayout, name: str, colour: str, width: int | None = None) -> QtWidgets.QFrame:
    """One of the three parts: a framed card with a coloured top edge."""
    frame = QtWidgets.QFrame()
    frame.setObjectName(name)
    frame.setStyleSheet(f"QFrame#{name} {{ background: {PANEL_BG}; border: 1px solid #2E2E2E;"
                        f" border-top: 3px solid {colour}; border-radius: 6px; }}")
    frame.setLayout(layout)
    if width:
        frame.setFixedWidth(width)
    return frame


def button(text: str, slot, tip: str = "", style: str = "") -> QtWidgets.QPushButton:
    btn = QtWidgets.QPushButton(text)
    btn.clicked.connect(slot)
    btn.setToolTip(tip)
    if style:
        btn.setStyleSheet(style)
    return btn


def action_button(text: str, slot) -> QtWidgets.QPushButton:
    btn = button(text, slot, style=ACTION_STYLE)
    btn.setMinimumHeight(44)
    return btn


def help_button(slot, tip: str, size: int = 28) -> QtWidgets.QPushButton:
    """A round blue "?" opening a help page."""
    btn = button("?", slot, tip, f"QPushButton {{ background: {ACCENT}; color: white; border-radius: {size // 2}px;"
                                 f" padding: 0; font: bold {size * 3 // 7}pt 'Segoe UI'; }}"
                                 f" QPushButton:hover {{ background: #3D82BF; }}")
    btn.setFixedSize(size, size)
    return btn


def icon_button(icon: str, slot, tip: str) -> QtWidgets.QPushButton:
    btn = button(icon, slot, tip, "padding: 2px 0; font: 12pt 'Segoe UI Emoji';")
    btn.setFixedWidth(30)
    return btn


def spin_box(value, lo, hi, suffix: str = "", decimals: int | None = None, step: float | None = None,
             slot=None) -> QtWidgets.QAbstractSpinBox:
    """A QSpinBox, or a QDoubleSpinBox with ``decimals``; ``slot`` gets every new value."""
    sp = QtWidgets.QSpinBox() if decimals is None else QtWidgets.QDoubleSpinBox()
    sp.setRange(lo, hi)
    if decimals is not None:
        sp.setDecimals(decimals)
    if step:
        sp.setSingleStep(step)
    sp.setSuffix(suffix)
    sp.setValue(value)
    if slot is not None:
        sp.valueChanged.connect(slot)
    return sp


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
            self.spins[key] = spin_box(getattr(cal, key), -100000, 100000, suffix, decimals, step, self.changed)
            form.addRow(label, self.spins[key])
        for key in ("stretch_x", "stretch_y"):
            self.spins[key].setToolTip("µm per camera pixel; negative = mirror")

    def calibration(self) -> Calibration:
        return Calibration(**{k: sp.value() for k, sp in self.spins.items()})


class ClickViewBox(pg.ViewBox):
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
    """Grey image with fixed auto contrast (no histogram: power is always 100 %), numbered spots, the ring of the
    spot on the SLM and Wavefront's field."""

    mouseMoved = QtCore.pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(PANEL_BG)
        self.vb = ClickViewBox()
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
        self.spot_item.setAcceptedMouseButtons(NO_MOUSE)                # clicks go to the view
        self.vb.addItem(self.spot_item)
        self.slm_item = pg.ScatterPlotItem(pxMode=False, symbol="o", pen=pg.mkPen(SLM_RING, width=3),
                                           brush=pg.mkBrush(None))
        self.slm_label = pg.TextItem("SLM", color=SLM_RING, anchor=(0.5, 0.0))
        self.slm_label.setFont(QtGui.QFont("Segoe UI", 10, QtGui.QFont.Weight.Bold))
        for item in (self.slm_item, self.slm_label):
            item.setAcceptedMouseButtons(NO_MOUSE)
            item.setZValue(10)                          # above the spots, below their numbers
            self.vb.addItem(item)
        self.slm_label.hide()
        self.labels: list[pg.TextItem] = []
        self.scene().sigMouseMoved.connect(self._on_mouse)

    def set_image(self, image: np.ndarray, fit: bool = True):
        """Show ``image`` with contrast from its own 1-99.7 % percentiles; ``fit`` the view to it (a new size)."""
        lo, hi = np.percentile(image, [1, 99.7])
        self.image_item.setImage(image, levels=(lo, hi if hi > lo else lo + 1))
        if fit:
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
        """Red discs numbered from 1 (the number labels are kept from one call to the next: a drag redraws often)."""
        pts = np.asarray(spots, dtype=float).reshape(-1, 2)
        self.spot_item.setData(pos=pts, size=diameter_px)
        while len(self.labels) > len(pts):
            self.vb.removeItem(self.labels.pop())
        while len(self.labels) < len(pts):
            lbl = pg.TextItem(color=SPOT, anchor=(0.5, 1.0))
            lbl.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold))
            lbl.setAcceptedMouseButtons(NO_MOUSE)
            lbl.setZValue(20)
            self.vb.addItem(lbl)
            self.labels.append(lbl)
        for k, (lbl, (x, y)) in enumerate(zip(self.labels, pts)):
            lbl.setText(str(k + 1))
            lbl.setPos(x, y - diameter_px / 2)

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
        browse = button("…", self._browse, style="padding: 4px 0;")
        browse.setFixedWidth(30)
        row.addWidget(self.edit, stretch=1)
        row.addWidget(browse)

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder", self.edit.text())
        if folder:
            self._set(str(Path(folder)))

    def _set(self, folder: str):
        folder = folder.strip()
        self.edit.setText(folder)
        self.edit.setToolTip(folder)
        self.changed.emit(folder)
