"""Holo calibration tab: typed camera -> Wavefront transform (pixel size, shift, rotation, mirror)."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ..config import Config
from ..holo import HoloCalibration

INSTRUCTIONS = """\
<p>Spot positions on the camera image are sent to Wavefront IV in µm:
centred on the image, mirrored, rotated by θ, scaled by the camera pixel size, then shifted
by (ΔX, ΔY), as in DHgui / DHparams.m.</p>
<ol>
<li>Holo mode, <i>1. Select spots</i>: drop a few spots spread over the image and press
<b>Send spots to SLM (live)</b>.</li>
<li>Open the shutter and image the spots with the camera.</li>
<li>Compare with the spots you clicked and adjust here: ΔX / ΔY move the pattern, θ rotates
it, the pixel size scales it. Changes apply immediately: send again to check.</li>
<li><b>Save</b> once they match; the last saved calibration is reloaded at start-up.</li>
</ol>"""


class HoloCalibrateTab(QtWidgets.QWidget):
    calibrationChanged = QtCore.pyqtSignal(object)   # HoloCalibration
    status = QtCore.pyqtSignal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.calibration = HoloCalibration()        # DHparams.m values until one is loaded
        self.modified = False
        self._syncing = False

        col = QtWidgets.QVBoxLayout()
        info = QtWidgets.QLabel(INSTRUCTIONS)
        info.setWordWrap(True)
        col.addWidget(info)

        grp = QtWidgets.QGroupBox("Camera -> Wavefront")
        form = QtWidgets.QFormLayout(grp)
        self.spin_px = QtWidgets.QDoubleSpinBox(); self.spin_px.setRange(0.001, 100); self.spin_px.setDecimals(4)
        self.spin_px.setSingleStep(0.01); self.spin_px.setSuffix(" µm / camera px")
        self.spin_dx = QtWidgets.QDoubleSpinBox(); self.spin_dx.setRange(-10000, 10000); self.spin_dx.setDecimals(2)
        self.spin_dx.setSuffix(" µm")
        self.spin_dy = QtWidgets.QDoubleSpinBox(); self.spin_dy.setRange(-10000, 10000); self.spin_dy.setDecimals(2)
        self.spin_dy.setSuffix(" µm")
        self.spin_theta = QtWidgets.QDoubleSpinBox(); self.spin_theta.setRange(-360, 360); self.spin_theta.setDecimals(2)
        self.spin_theta.setSingleStep(0.5); self.spin_theta.setSuffix(" °")
        self.chk_flip_y = QtWidgets.QCheckBox("mirror y (camera y points down, Wavefront y up)")
        self.chk_flip_x = QtWidgets.QCheckBox("mirror x")
        form.addRow("pixel size", self.spin_px)
        form.addRow("ΔX", self.spin_dx)
        form.addRow("ΔY", self.spin_dy)
        form.addRow("θ", self.spin_theta)
        form.addRow(self.chk_flip_y)
        form.addRow(self.chk_flip_x)
        col.addWidget(grp)

        btns = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Save calibration…")
        self.btn_save.setStyleSheet("font-weight: bold")
        self.btn_load = QtWidgets.QPushButton("Load calibration…")
        self.btn_defaults = QtWidgets.QPushButton("DHparams.m defaults")
        for b in (self.btn_save, self.btn_load, self.btn_defaults):
            btns.addWidget(b)
        col.addLayout(btns)
        self.lbl_result = QtWidgets.QLabel()
        self.lbl_result.setWordWrap(True)
        col.addWidget(self.lbl_result)
        col.addStretch(1)

        panel = QtWidgets.QWidget()
        panel.setLayout(col)
        panel.setFixedWidth(560)
        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(panel)
        layout.addStretch(1)

        for sp in (self.spin_px, self.spin_dx, self.spin_dy, self.spin_theta):
            sp.valueChanged.connect(self._on_edit)
        for chk in (self.chk_flip_x, self.chk_flip_y):
            chk.toggled.connect(self._on_edit)
        self.btn_save.clicked.connect(self.save_dialog)
        self.btn_load.clicked.connect(self.load_dialog)
        self.btn_defaults.clicked.connect(self.use_defaults)
        self._show(self.calibration)

    # ------------------------------------------------------------------ state
    def _show(self, cal: HoloCalibration):
        self._syncing = True
        self.spin_px.setValue(cal.pixel_size_um)
        self.spin_dx.setValue(cal.delta_x_um)
        self.spin_dy.setValue(cal.delta_y_um)
        self.spin_theta.setValue(cal.theta_deg)
        self.chk_flip_y.setChecked(cal.flip_y)
        self.chk_flip_x.setChecked(cal.flip_x)
        self._syncing = False
        self._refresh_label()

    def _refresh_label(self):
        cal = self.calibration
        if cal.path:
            src = f"{Path(cal.path).name}, {cal.created_text()}"
        else:
            src = "DHparams.m defaults" if not cal.created else "unsaved"
        if self.modified:
            src += " — <b>modified, not saved</b>"
        self.lbl_result.setText(f"<b>Current holo calibration</b> ({src})<br>{cal.summary()}")

    def _on_edit(self, *_):
        if self._syncing:
            return
        cal = self.calibration
        cal.pixel_size_um = self.spin_px.value()
        cal.delta_x_um = self.spin_dx.value()
        cal.delta_y_um = self.spin_dy.value()
        cal.theta_deg = self.spin_theta.value()
        cal.flip_y = self.chk_flip_y.isChecked()
        cal.flip_x = self.chk_flip_x.isChecked()
        self.modified = True
        self._refresh_label()
        self.calibrationChanged.emit(cal)

    def _set_calibration(self, cal: HoloCalibration):
        self.calibration = cal
        self.modified = False
        self._show(cal)
        self.calibrationChanged.emit(cal)

    def use_defaults(self):
        self._set_calibration(HoloCalibration())
        self.status.emit("Holo calibration: DHparams.m defaults")

    # ------------------------------------------------------------- persistence
    def save_dialog(self):
        day = _dt.date.today().isoformat()
        start_dir = Path(self.config.last_holo_calibration).parent if self.config.last_holo_calibration else \
            Path(self.config.last_dir or Path.home())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save holo calibration", str(start_dir / f"holo_calibration_{day}.json"), "JSON (*.json)")
        if path:
            self.save_calibration(path)

    def save_calibration(self, path) -> HoloCalibration:
        cal = self.calibration
        cal.created = _dt.datetime.now().isoformat(timespec="seconds")
        cal.save(path)
        self.config.last_holo_calibration = str(path)
        self.config.save()
        self.modified = False
        self._refresh_label()
        self.calibrationChanged.emit(cal)
        self.status.emit(f"Holo calibration saved to {path}")
        return cal

    def load_dialog(self):
        start = str(Path(self.config.last_holo_calibration).parent) if self.config.last_holo_calibration else \
            (self.config.last_dir or str(Path.home()))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load holo calibration", start, "JSON (*.json)")
        if path:
            self.load_calibration(path)

    def load_calibration(self, path) -> bool:
        try:
            cal = HoloCalibration.load(path)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Cannot load holo calibration", f"{path}\n{e}")
            return False
        self.config.last_holo_calibration = str(path)
        self.config.save()
        self._set_calibration(cal)
        self.status.emit(f"Holo calibration loaded from {path}")
        return True
