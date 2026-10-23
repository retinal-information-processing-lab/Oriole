"""Oriole window: visual (DMD) on the left, the image to click spots on in the middle, holo (SLM) on the right.

Look and dark theme taken from Plerion; the holo side follows the MATLAB DHgui.
"""
from __future__ import annotations

import datetime as _dt
import html
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
SPOT = (255, 40, 40)
LOG_COLOURS = {"info": "#00FF00", "warn": "#FFD700", "error": "#FF4444"}
ACTION_STYLE = """
    QPushButton { background: #1A4A1A; color: #00FF00; border: none; border-radius: 4px;
                  font: bold 11pt 'Consolas'; padding: 10px; }
    QPushButton:hover { background: #2A6A2A; }
    QPushButton:disabled { background: #3A3A3A; color: #666666; }
"""
AUTOLOAD_HINT = 'check that "Tools → Script engine → auto load (script folder)" is selected in Wavefront'
BLOCKED_STYLE = ACTION_STYLE.replace("#1A4A1A", "#4A4A1A").replace("#00FF00", "#FFD700").replace("#2A6A2A", "#6A6A2A")


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


def _title(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setFont(QtGui.QFont("Consolas", 12, QtGui.QFont.Weight.Bold))
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    return lbl


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
    clicked = QtCore.pyqtSignal(float, float, bool)     # x, y, right button

    def __init__(self):
        super().__init__(lockAspect=True, invertY=True, enableMenu=False)
        self.setMouseMode(pg.ViewBox.PanMode)

    def mouseClickEvent(self, ev):
        if ev.button() in (QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.RightButton):
            ev.accept()
            p = self.mapToView(ev.pos())
            self.clicked.emit(p.x(), p.y(), ev.button() == QtCore.Qt.MouseButton.RightButton)
            return
        super().mouseClickEvent(ev)


class ImageView(pg.GraphicsLayoutWidget):
    """Grey image with fixed auto contrast (no histogram: power is always 100 %) and numbered spots."""

    mouseMoved = QtCore.pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(BG)
        self.vb = _ClickViewBox()
        self.addItem(self.vb)
        self.image_item = pg.ImageItem()
        self.vb.addItem(self.image_item)
        self.spot_item = pg.ScatterPlotItem(pxMode=False, symbol="o", pen=None, brush=pg.mkBrush(SPOT))
        self.vb.addItem(self.spot_item)
        self.labels: list[pg.TextItem] = []
        self.scene().sigMouseMoved.connect(self._on_mouse)

    def set_image(self, image: np.ndarray):
        lo, hi = np.percentile(image, [1, 99.7])
        self.image_item.setImage(image, levels=(lo, hi if hi > lo else lo + 1))
        self.vb.autoRange()

    def set_spots(self, spots, diameter_px: float):
        for lbl in self.labels:
            self.vb.removeItem(lbl)
        self.labels = []
        pts = np.asarray(spots, dtype=float).reshape(-1, 2)
        self.spot_item.setData(pos=pts, size=diameter_px)
        for k, (x, y) in enumerate(pts):
            lbl = pg.TextItem(str(k + 1), color=SPOT, anchor=(0.5, 1.0))
            lbl.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold))
            lbl.setPos(x, y - diameter_px / 2)
            self.vb.addItem(lbl)
            self.labels.append(lbl)

    def _on_mouse(self, pos):
        if self.vb.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.mouseMoved.emit(p.x(), p.y())


class FolderEdit(QtWidgets.QWidget):
    """Folder path (typed or picked with …) and a broom to empty it; ``changed`` when it is edited."""

    changed = QtCore.pyqtSignal(str)
    purgeRequested = QtCore.pyqtSignal()

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
        self.btn_purge = QtWidgets.QPushButton("🧹")
        self.btn_purge.setFixedWidth(30)
        self.btn_purge.setStyleSheet("padding: 2px 0; font: 12pt 'Segoe UI Emoji';")
        self.btn_purge.setToolTip("Sweep this folder clean (deletes its files)")
        self.btn_purge.clicked.connect(self.purgeRequested)
        row.addWidget(self.edit, stretch=1)
        row.addWidget(btn)
        row.addWidget(self.btn_purge)

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
        self.last_spot_bin: Path | None = None
        self.shutter = Shutter(config.nidaq_device, config.shutter_ao)
        self.jobs: list[tuple[str, str, Path]] = []      # phase masks still to compute
        self.generation: dict | None = None
        self._load_t0 = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.setWindowTitle("Oriole")
        self.resize(1500, 900)
        self._build()
        self.redraw_spots()
        self.refresh_masks()
        self.log("Oriole ready: load an image, click spots (right-click removes one).")

    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        cols = QtWidgets.QHBoxLayout()
        root.addLayout(cols, stretch=1)

        # visual (DMD)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(_title("VISUAL · DMD"))
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
        cols.addWidget(self._panel(left))

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
        mid.addLayout(bar)
        self.view = ImageView()
        self.view.vb.clicked.connect(self._on_click)
        self.view.mouseMoved.connect(self._on_mouse)
        mid.addWidget(self.view, stretch=1)
        self.lbl_mouse = QtWidgets.QLabel(" ")
        self.lbl_mouse.setStyleSheet(f"color: {DIM};")
        mid.addWidget(self.lbl_mouse)
        cols.addLayout(mid, stretch=1)

        # holo (SLM)
        right = QtWidgets.QVBoxLayout()
        right.addWidget(_title("HOLO · SLM"))
        setup = QtWidgets.QGroupBox("Settings")
        col = QtWidgets.QVBoxLayout(setup)
        col.addWidget(QtWidgets.QLabel("script folder"))
        self.edit_script_dir = FolderEdit(self.config.wfd_script_dir)
        self.edit_script_dir.changed.connect(self._on_wavefront_dirs)
        self.edit_script_dir.purgeRequested.connect(self.sweep_script_folder)
        col.addWidget(self.edit_script_dir)
        col.addWidget(QtWidgets.QLabel("phase mask folder"))
        self.edit_output_dir = FolderEdit(self.config.wfd_output_dir)
        self.edit_output_dir.changed.connect(self._on_wavefront_dirs)
        self.edit_output_dir.purgeRequested.connect(self.sweep_mask_folder)
        col.addWidget(self.edit_output_dir)
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
        right.addWidget(setup)
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
        self.btn_generate.clicked.connect(self.generate)
        right.addWidget(self.btn_generate)
        cols.addWidget(self._panel(right))
        self._show_shutter()
        self.watcher = QtCore.QFileSystemWatcher(self)        # mask list and button follow the folder
        self.watcher.directoryChanged.connect(lambda _: self.refresh_masks())
        self._watch_output_dir()

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFixedHeight(130)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        self.log_view.setStyleSheet("background: #0A0A0A; color: #00FF00; border: none;")
        root.addWidget(self.log_view)

    @staticmethod
    def _panel(layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        w.setFixedWidth(270)
        return w

    def log(self, text: str, level: str = "info"):
        self.log_view.appendHtml(f'<span style="color:{LOG_COLOURS[level]}">'
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
        self.log(f"image {Path(source).name or 'set'}: {self.image.shape[1]} x {self.image.shape[0]} px")

    def _on_click(self, x: float, y: float, right: bool):
        if self.image is None:
            self.log("load an image first", "warn")
            return
        if right:
            if self.spots:
                d = np.hypot(*(np.asarray(self.spots) - [x, y]).T)
                k = int(d.argmin())
                if d[k] <= max(self.diameter_px() / 2, 5):
                    self._push_undo()
                    del self.spots[k]
                    self.redraw_spots()
            return
        h, w = self.image.shape
        if 0 <= x < w and 0 <= y < h:
            self._push_undo()
            self.spots.append((x, y))
            self.redraw_spots()

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

    def diameter_px(self) -> float:
        px_um = (self.config.visual.px_um + self.config.holo.px_um) / 2
        return self.spin_diam.value() / max(px_um, 1e-6)

    def redraw_spots(self):
        self.view.set_spots(self.spots, self.diameter_px())
        self.lbl_count.setText(f"{len(self.spots)} spot{'s' if len(self.spots) != 1 else ''}  ")

    def _on_diameter(self, value: float):
        self.config.spot_diameter_um = value
        self.redraw_spots()

    def _on_calibration(self):
        self.config.visual_cal = self.visual_box.calibration().to_dict()
        self.config.holo_cal = self.holo_box.calibration().to_dict()
        self.redraw_spots()

    def _on_mouse(self, x: float, y: float):
        if self.image is None:
            return
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

    def _purge_scripts(self) -> bool:
        try:
            removed = self.client().purge()
        except OSError as e:
            self.log(f"cannot empty the script folder: {e}", "error")
            return False
        if removed:
            self.log(f"script folder purged ({', '.join(removed)} left there by Wavefront)", "warn")
        return True

    def sweep_script_folder(self):
        if self.jobs:
            self.log("not now: Wavefront is computing masks from that folder", "warn")
            return
        try:
            removed = self.client().purge()
        except OSError as e:
            self.log(f"cannot empty the script folder: {e}", "error")
            return
        self.log(f"🧹 script folder swept clean ({', '.join(removed)})" if removed else "🧹 script folder already clean")

    def sweep_mask_folder(self):
        if self.jobs:
            self.log("not now: Wavefront is saving masks in that folder", "warn")
            return
        folder = Path(self.config.wfd_output_dir)
        files = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
        if not files:
            self.log("🧹 phase mask folder already clean")
            return
        first, last = holo.date_range(files)
        dates = f"{first:%Y %m %d}" if first == last else f"{first:%Y %m %d} – {last:%Y %m %d}"
        answer = QtWidgets.QMessageBox.question(
            self, "Sweep the phase mask folder",
            f"Delete the {len(files)} files ({len(holo.available_masks(folder))} masks, dated {dates}) in\n"
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
        """Mask numbers found in the phase mask folder; Generate turns yellow while the folder holds masks."""
        out_dir = self.config.wfd_output_dir
        current = self.combo_mask.currentText()
        self.combo_mask.clear()
        self.combo_mask.addItems([str(n) for n in holo.available_masks(out_dir)])
        if self.combo_mask.findText(current) >= 0:
            self.combo_mask.setCurrentText(current)
        self.btn_load_mask.setEnabled(self.combo_mask.count() > 0 and not self.jobs)
        blocked = bool(holo.old_masks(out_dir))
        self.btn_generate.setStyleSheet(BLOCKED_STYLE if blocked else ACTION_STYLE)
        self.btn_generate.setToolTip("the phase mask folder is not empty" if blocked else "")

    def _watch_output_dir(self):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        if Path(self.config.wfd_output_dir).is_dir():
            self.watcher.addPath(self.config.wfd_output_dir)

    def generate(self):
        if self.jobs:
            self.log("Wavefront is still computing the previous masks", "warn")
            return
        out_dir = Path(self.config.wfd_output_dir)
        if not out_dir.is_dir() or not self.client().script_dir.is_dir():
            self.log(f"Wavefront folders not found ({self.config.wfd_script_dir}, {out_dir})", "error")
            return
        if not self._purge_scripts():
            return
        old = holo.old_masks(out_dir)
        if old:
            first, last = holo.date_range(old)
            dates = f"{first:%Y %m %d}" if first == last else f"{first:%Y %m %d} – {last:%Y %m %d}"
            names = sorted({p.name.split("_000.")[0] for p in old}, key=holo.natural_key)
            self.log(f"not generating: the phase mask folder is not empty ({len(names)} masks, {names[0]} … "
                     f"{names[-1]}, dated {dates}); empty {out_dir} first", "error")
            return
        if not self._have_spots():
            return
        cal, d = self.config.holo, self.spin_diam.value()
        spots_um = cal.to_um(self.spots, self.image.shape)
        names = [holo.mask_name(k + 1) for k in range(len(spots_um))]
        self.jobs = [(name, holo.mask_script(name, x, y, d), holo.mask_png(out_dir, name))
                     for name, (x, y) in zip(names, spots_um)]
        self.generation = {"spots_px": list(self.spots), "spots_um": spots_um, "diameter": d, "cal": cal,
                           "total": len(self.jobs)}
        self.btn_generate.setEnabled(False)
        self.btn_load_mask.setEnabled(False)
        self._submit()

    def _submit(self):
        name, js, _ = self.jobs[0]
        try:
            self.client().submit(js)
        except Exception as e:  # noqa: BLE001
            self._finish(f"cannot send {name}: {e}", "error")
            return
        self.job_t0 = time.time()
        self.job_taken = False
        self.job_warned = False
        self.timer.start()
        self.log(f"Wavefront: computing {name} ({self.generation['total'] - len(self.jobs) + 1}/"
                 f"{self.generation['total']})")

    def poll(self):
        """Next mask once Wavefront took the script and (re)wrote the png of the current one."""
        if not self.jobs:
            self.timer.stop()
            return
        name, _, png = self.jobs[0]
        waited = time.time() - self.job_t0
        if not self.job_taken:
            if self.client().busy:
                if waited > holo.TIMEOUT_S:
                    self._finish(f"Wavefront did not take the script within {waited:.0f} s; {AUTOLOAD_HINT}",
                                 "error")
                elif waited > holo.NOT_READ_S and not self.job_warned:
                    self.job_warned = True
                    self.log(f"the script is not being used by Wavefront: {AUTOLOAD_HINT}", "warn")
                return
            self.job_taken = True
        if png.exists() and png.stat().st_mtime >= self.job_t0 - 1:
            self.jobs.pop(0)
            if self.jobs:
                self._submit()
            else:
                g = self.generation
                rec = holo.write_spot_record(self.config.wfd_output_dir, g["spots_px"], g["spots_um"],
                                             g["diameter"], g["cal"])
                self._finish(f"{g['total']} phase masks saved in {self.config.wfd_output_dir} "
                             f"(spot i = {holo.PREFIX}i; {rec.name})")
        elif waited > holo.TIMEOUT_S:
            self._finish(f"{png.name} did not appear within {waited:.0f} s: is {png.parent} the folder "
                         f"Wavefront saves to?", "error")

    def _finish(self, message: str, level: str = "info"):
        self.timer.stop()
        self.jobs = []
        self.generation = None
        self.btn_generate.setEnabled(True)
        self.log(message, level)
        self.refresh_masks()

    def load_selected_mask(self):
        if not self.combo_mask.currentText():
            self.log(f"no {holo.PREFIX}<n> mask in {self.config.wfd_output_dir}", "warn")
            return
        name = holo.mask_name(int(self.combo_mask.currentText()))
        png = holo.mask_png(self.config.wfd_output_dir, name)
        if not self._purge_scripts():
            return
        try:
            self.client().submit(holo.load_script(png))
        except Exception as e:  # noqa: BLE001
            self.log(f"cannot load {name}: {e}", "error")
            return
        self.log(f"SLM: loading {name}")
        self._load_t0 = time.time()
        QtCore.QTimer.singleShot(int(holo.NOT_READ_S * 1000) + 100, self.check_load_taken)

    def check_load_taken(self):
        """A load script still in the folder after NOT_READ_S: Wavefront is not reading the folder."""
        if self.client().busy and time.time() - self._load_t0 >= holo.NOT_READ_S:
            self.log(f"the script is not being used by Wavefront: {AUTOLOAD_HINT}", "warn")

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

    def _on_wavefront_dirs(self, *_):
        self.config.wfd_script_dir = self.edit_script_dir.edit.text()
        self.config.wfd_output_dir = self.edit_output_dir.edit.text()
        self.save_config()
        for d in (self.config.wfd_script_dir, self.config.wfd_output_dir):
            if not Path(d).is_dir():
                self.log(f"folder not found: {d}", "warn")
        self._watch_output_dir()
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
