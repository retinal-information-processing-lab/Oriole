"""Main window: Visual / Holo mode, Select | Merge or Phase masks | Calibration tabs, settings dialog."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtGui, QtWidgets

from ..config import Config, DEFAULT_CONFIG_PATH
from .calibrate_tab import CalibrateTab
from .hardware import ShutterController, WavefrontRunner
from .holo_calibrate_tab import HoloCalibrateTab
from .merge_tab import MergeTab
from .select_tab import SelectTab
from .slm_tab import SlmTab

MODES = {"visual": "Visual (DMD)", "holo": "Holo (SLM)"}


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rig settings")
        form = QtWidgets.QFormLayout(self)
        form.addRow(QtWidgets.QLabel("<b>DMD (visual)</b>"))
        self.rig = QtWidgets.QComboBox()
        self.rig.addItems(["2", "3"])
        self.rig.setCurrentText(str(config.rig_id))
        self.h = QtWidgets.QSpinBox(); self.h.setRange(8, 4096); self.h.setValue(config.dmd_height)
        self.w = QtWidgets.QSpinBox(); self.w.setRange(8, 4096); self.w.setValue(config.dmd_width)
        self.px = QtWidgets.QDoubleSpinBox(); self.px.setRange(0.01, 100); self.px.setDecimals(3)
        self.px.setValue(config.pixel_size_um)
        form.addRow("rig_id", self.rig)
        form.addRow("DMD frame height (rows)", self.h)
        form.addRow("DMD frame width (cols)", self.w)
        form.addRow("pixel size (µm)", self.px)
        self.age = QtWidgets.QSpinBox(); self.age.setRange(1, 3650); self.age.setSuffix(" days")
        self.age.setValue(config.calibration_max_age_days)
        form.addRow("warn if calibration older than", self.age)

        form.addRow(QtWidgets.QLabel("<b>Holography (Wavefront IV, shutter)</b>"))
        self.script_dir = self._folder_row(form, "Wavefront script folder", config.wfd_script_dir)
        self.output_dir = self._folder_row(form, "Wavefront phase mask folder", config.wfd_output_dir)
        self.prefix = QtWidgets.QLineEdit(config.wfd_profile_prefix)
        form.addRow("phase mask name prefix", self.prefix)
        self.timeout = QtWidgets.QDoubleSpinBox(); self.timeout.setRange(1, 3600); self.timeout.setSuffix(" s")
        self.timeout.setValue(config.wfd_timeout_s)
        form.addRow("wait for Wavefront at most", self.timeout)
        self.device = QtWidgets.QLineEdit(config.nidaq_device)
        form.addRow("NI-DAQ device", self.device)
        self.ao = QtWidgets.QSpinBox(); self.ao.setRange(0, 31); self.ao.setPrefix("ao"); self.ao.setValue(config.shutter_ao)
        form.addRow("shutter output", self.ao)
        self.open_v = QtWidgets.QDoubleSpinBox(); self.open_v.setRange(0.1, 10); self.open_v.setSuffix(" V")
        self.open_v.setValue(config.shutter_open_v)
        form.addRow("shutter open voltage", self.open_v)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _folder_row(self, form: QtWidgets.QFormLayout, label: str, value: str) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit(value)
        edit.setMinimumWidth(320)
        btn = QtWidgets.QPushButton("…")
        btn.setFixedWidth(30)
        btn.clicked.connect(lambda: edit.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, label, edit.text()) or edit.text()))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(btn)
        form.addRow(label, row)
        return edit

    def apply(self, config: Config):
        config.rig_id = int(self.rig.currentText())
        config.dmd_height = self.h.value()
        config.dmd_width = self.w.value()
        config.pixel_size_um = self.px.value()
        config.calibration_max_age_days = self.age.value()
        config.wfd_script_dir = self.script_dir.text().strip()
        config.wfd_output_dir = self.output_dir.text().strip()
        config.wfd_profile_prefix = self.prefix.text().strip() or "Pattern"
        config.wfd_timeout_s = self.timeout.value()
        config.nidaq_device = self.device.text().strip() or "Dev1"
        config.shutter_ao = self.ao.value()
        config.shutter_open_v = self.open_v.value()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: Config, config_path=DEFAULT_CONFIG_PATH):
        super().__init__()
        self.config = config
        self.config_path = config_path
        if not config.path:
            config.path = str(config_path)
        self.setWindowTitle("Oriole - visual & holographic pattern generation")
        self.resize(1400, 900)

        self.runner = WavefrontRunner(config, self)
        self.shutter = ShutterController(config, self)

        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.calibrate_tab = CalibrateTab(config)
        self.holo_calibrate_tab = HoloCalibrateTab(config)
        self.select_tab = SelectTab(config, self.runner, self.shutter)
        self.merge_tab = MergeTab(config)
        self.slm_tab = SlmTab(config, self.runner, self.shutter)
        self.tabs.addTab(self.select_tab, "1. Select spots")
        self.tabs.addTab(self.merge_tab, "2. Merge into bin")
        self.tabs.addTab(self.slm_tab, "2. Phase masks && shutter")
        self.tabs.addTab(self.calibrate_tab, "Calibration")
        self.tabs.addTab(self.holo_calibrate_tab, "Calibration")

        self.calibrate_tab.calibrationChanged.connect(self.select_tab.set_calibration)
        self.holo_calibrate_tab.calibrationChanged.connect(self.select_tab.set_holo_calibration)
        self.select_tab.calibrationTabRequested.connect(lambda: self.tabs.setCurrentWidget(self.calibration_tab()))
        for t in (self.calibrate_tab, self.holo_calibrate_tab, self.select_tab, self.merge_tab, self.slm_tab):
            t.status.connect(self.statusBar().showMessage)
        self.runner.finished.connect(self._on_runner_finished)
        self.shutter.error.connect(self._on_shutter_error)

        tb = self.addToolBar("Mode")
        tb.setMovable(False)
        tb.addWidget(QtWidgets.QLabel(" Mode: "))
        group = QtGui.QActionGroup(self)
        self.mode_actions = {}
        for mode, label in MODES.items():
            act = tb.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            group.addAction(act)
            self.mode_actions[mode] = act

        menu = self.menuBar().addMenu("&Settings")
        act = menu.addAction("Rig settings…")
        act.triggered.connect(self.open_settings)

        if config.last_calibration and Path(config.last_calibration).exists():
            self.calibrate_tab.load_calibration(config.last_calibration)
        if not (config.last_holo_calibration and Path(config.last_holo_calibration).exists()
                and self.holo_calibrate_tab.load_calibration(config.last_holo_calibration)):
            self.select_tab.set_holo_calibration(self.holo_calibrate_tab.calibration)   # DHparams.m defaults
        self.set_mode(config.mode if config.mode in MODES else "visual")
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ mode
    def calibration_tab(self) -> QtWidgets.QWidget:
        return self.holo_calibrate_tab if self.config.mode == "holo" else self.calibrate_tab

    def set_mode(self, mode: str):
        self.config.mode = mode
        self.mode_actions[mode].setChecked(True)
        holo = mode == "holo"
        for tab, visible in ((self.merge_tab, not holo), (self.calibrate_tab, not holo),
                             (self.slm_tab, holo), (self.holo_calibrate_tab, holo)):
            self.tabs.setTabVisible(self.tabs.indexOf(tab), visible)
        self.select_tab.set_stim_mode(mode)
        self.statusBar().showMessage(self._mode_summary())

    def _mode_summary(self) -> str:
        c = self.config
        if c.mode == "holo":
            return f"{MODES['holo']}: Wavefront {c.wfd_script_dir}, shutter {c.nidaq_device}/ao{c.shutter_ao}"
        return f"{MODES['visual']}: rig {c.rig_id}, DMD {c.dmd_width}x{c.dmd_height} px, {c.pixel_size_um} µm/px"

    # -------------------------------------------------------------- hardware
    def _on_runner_finished(self, ok: bool, message: str):
        self.statusBar().showMessage(f"Wavefront: {message}")
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Wavefront", message)

    def _on_shutter_error(self, message: str):
        self.statusBar().showMessage(f"Shutter: {message}")
        QtWidgets.QMessageBox.warning(self, "Shutter", message)

    # -------------------------------------------------------------- settings
    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            dlg.apply(self.config)
            self.config.save(self.config_path)
            self.calibrate_tab.set_config(self.config)
            self.select_tab.set_config(self.config)
            self.slm_tab.set_config(self.config)
            self.shutter.set_config(self.config)
            self.statusBar().showMessage(self._mode_summary())

    def closeEvent(self, ev):
        self.shutter.shutdown()
        self.config.save(self.config_path)
        super().closeEvent(ev)


def run(config: Config | None = None, config_path=DEFAULT_CONFIG_PATH) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    config = config or Config.load(config_path)
    win = MainWindow(config, config_path)
    win.show()
    return app.exec()
