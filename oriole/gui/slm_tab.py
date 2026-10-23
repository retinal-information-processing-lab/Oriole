"""Phase masks & shutter tab: show any saved phase mask on the SLM and drive the shutter by hand."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ..config import Config
from ..wavefront import list_masks, load_script
from .hardware import ShutterController, ShutterPanel, WavefrontRunner


class SlmTab(QtWidgets.QWidget):
    status = QtCore.pyqtSignal(str)

    def __init__(self, config: Config, runner: WavefrontRunner, shutter: ShutterController, parent=None):
        super().__init__(parent)
        self.config = config
        self.runner = runner

        col = QtWidgets.QVBoxLayout()
        info = QtWidgets.QLabel(
            "Load a phase mask saved by Wavefront onto the SLM, then open the shutter to stimulate or "
            "calibrate by hand. Protocols run in Plerion: close the shutter here first (Oriole only holds "
            "the NI-DAQ output while the shutter is open).")
        info.setWordWrap(True)
        col.addWidget(info)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Phase masks"))
        self.edit_folder = QtWidgets.QLineEdit(config.wfd_output_dir)
        self.edit_folder.editingFinished.connect(self._on_folder_edited)
        row.addWidget(self.edit_folder, stretch=1)
        b = QtWidgets.QPushButton("Browse…")
        b.clicked.connect(self.browse_folder)
        row.addWidget(b)
        b = QtWidgets.QPushButton("Refresh")
        b.clicked.connect(self.refresh)
        row.addWidget(b)
        col.addLayout(row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["phase mask", "modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(lambda *_: self.load_selected())
        col.addWidget(self.tree, stretch=1)

        row = QtWidgets.QHBoxLayout()
        self.btn_load = QtWidgets.QPushButton("Load to SLM")
        self.btn_load.setStyleSheet("font-weight: bold")
        self.btn_load.clicked.connect(self.load_selected)
        self.btn_file = QtWidgets.QPushButton("Load file…")
        self.btn_file.clicked.connect(self.load_file_dialog)
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_file)
        col.addLayout(row)
        self.lbl_slm = QtWidgets.QLabel("On SLM: unknown (not set by Oriole yet)")
        col.addWidget(self.lbl_slm)
        col.addWidget(ShutterPanel(shutter))
        col.addWidget(QtWidgets.QLabel("Log"))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMaximumHeight(140)
        col.addWidget(self.log)

        panel = QtWidgets.QWidget()
        panel.setLayout(col)
        panel.setFixedWidth(680)
        layout = QtWidgets.QHBoxLayout(self)
        layout.addWidget(panel)
        layout.addStretch(1)

        runner.log.connect(self.add_log)
        runner.slmChanged.connect(lambda label: self.lbl_slm.setText(f"On SLM: <b>{label}</b>"))
        runner.busyChanged.connect(lambda busy: (self.btn_load.setEnabled(not busy), self.btn_file.setEnabled(not busy)))
        shutter.stateChanged.connect(lambda is_open: self.add_log(f"shutter {'OPEN' if is_open else 'closed'}"))
        shutter.error.connect(lambda msg: self.add_log(f"shutter error: {msg}"))
        self.refresh()

    def add_log(self, text: str):
        self.log.appendPlainText(f"{_dt.datetime.now():%H:%M:%S}  {text}")

    # ---------------------------------------------------------------- folder
    def _on_folder_edited(self):
        folder = self.edit_folder.text().strip()
        if folder != self.config.wfd_output_dir:
            self.config.wfd_output_dir = folder
            self.config.save()
        self.refresh()

    def browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Phase mask folder", self.config.wfd_output_dir)
        if folder:
            self.edit_folder.setText(folder)
            self._on_folder_edited()

    def set_config(self, config: Config):
        self.config = config
        self.edit_folder.setText(config.wfd_output_dir)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        for p in list_masks(self.config.wfd_output_dir):
            when = _dt.datetime.fromtimestamp(p.stat().st_mtime)
            it = QtWidgets.QTreeWidgetItem([p.name, f"{when:%Y %m %d  %H:%M}"])
            it.setData(0, QtCore.Qt.ItemDataRole.UserRole, str(p))
            self.tree.addTopLevelItem(it)

    # ------------------------------------------------------------------ load
    def load_selected(self):
        it = self.tree.currentItem()
        if it is None:
            QtWidgets.QMessageBox.warning(self, "No phase mask", "Select a phase mask in the list.")
            return
        self.load_mask(it.data(0, QtCore.Qt.ItemDataRole.UserRole))

    def load_file_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Phase mask", self.config.wfd_output_dir,
                                                        "Phase masks (*.algoPhp.png);;PNG (*.png)")
        if path:
            self.load_mask(path)

    def load_mask(self, path) -> bool:
        try:
            self.runner.run([(Path(path).name, load_script(Path(path)), None)])
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Wavefront", str(e))
            return False
        self.status.emit(f"Loading {Path(path).name} onto the SLM")
        return True
