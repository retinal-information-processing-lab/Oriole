"""Holo page of the Select tab: spots in Wavefront µm, live send, one phase mask per spot, shutter."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ..config import Config
from ..holo import HoloCalibration, HoloSpot, write_holo_record
from ..wavefront import date_range, live_script, mask_png, mask_script, natural_key, old_masks
from .hardware import ShutterController, ShutterPanel, WavefrontRunner


class HoloPanel(QtWidgets.QWidget):
    status = QtCore.pyqtSignal(str)

    def __init__(self, config: Config, runner: WavefrontRunner, shutter: ShutterController, parent=None):
        super().__init__(parent)
        self.config = config
        self.runner = runner
        self.spots: list[HoloSpot] = []
        self.ignored: list[str] = []
        self.calibration: HoloCalibration | None = None
        self.image_path = ""
        self.image_shape: tuple = ()
        self._record: dict | None = None        # generation in progress, written when the runner finishes

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QtWidgets.QLabel("<b>Spots in Wavefront coordinates</b>"))
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["spot", "X (µm)", "Y (µm)", "Ø (µm)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)
        self.lbl_ignored = QtWidgets.QLabel("")
        self.lbl_ignored.setWordWrap(True)
        layout.addWidget(self.lbl_ignored)

        self.btn_live = QtWidgets.QPushButton("Send spots to SLM (live)")
        self.btn_live.setToolTip("Compute and show one pattern with all enabled spots, without saving it "
                                 "(calibration, manual stimulation).")
        layout.addWidget(self.btn_live)

        grp = QtWidgets.QGroupBox("Phase masks (one per spot)")
        g = QtWidgets.QGridLayout(grp)
        g.addWidget(QtWidgets.QLabel("Name"), 0, 0)
        self.edit_name = QtWidgets.QLineEdit("holo")
        self.edit_name.setToolTip("Name of the record (<name>.holo.json) of the spots and masks")
        g.addWidget(self.edit_name, 0, 1, 1, 2)
        self.lbl_target = QtWidgets.QLabel("")
        self.lbl_target.setWordWrap(True)
        g.addWidget(self.lbl_target, 1, 0, 1, 3)
        self.btn_generate = QtWidgets.QPushButton("Generate phase masks…")
        g.addWidget(self.btn_generate, 2, 0, 1, 3)
        self.progress = QtWidgets.QProgressBar()
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        g.addWidget(self.progress, 3, 0, 1, 2)
        g.addWidget(self.btn_cancel, 3, 2)
        self.progress.hide()
        self.btn_cancel.hide()
        layout.addWidget(grp)
        layout.addWidget(ShutterPanel(shutter))

        self.btn_live.clicked.connect(self.send_live)
        self.btn_generate.clicked.connect(self.generate_dialog)
        self.btn_cancel.clicked.connect(runner.cancel)
        runner.busyChanged.connect(self._on_busy)
        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)

    # ------------------------------------------------------------------ spots
    def update_spots(self, spots, ignored, cal: HoloCalibration | None, image_path, image_shape):
        self.spots, self.ignored, self.calibration = list(spots), list(ignored), cal
        self.image_path, self.image_shape = str(image_path), tuple(image_shape)
        self.table.setRowCount(len(self.spots))
        for r, s in enumerate(self.spots):
            for c, v in enumerate([s.name, f"{s.x_um:.2f}", f"{s.y_um:.2f}", f"{s.d_um:.2f}"]):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(v))
        self.lbl_ignored.setText(f"<span style='color:#e65100'>Not used in holo (freehand): "
                                 f"{', '.join(self.ignored)}</span>" if self.ignored else "")
        prefix, n = self.config.wfd_profile_prefix, len(self.spots)
        names = f"{prefix}1" if n == 1 else f"{prefix}1 … {prefix}{n}"
        self.lbl_target.setText(f"→ {names} in {self.config.wfd_output_dir}" if n else "")

    def _check(self) -> bool:
        if self.calibration is None or not self.image_shape:
            QtWidgets.QMessageBox.warning(self, "No image", "Load an image first.")
            return False
        if not self.spots:
            QtWidgets.QMessageBox.warning(self, "No spot", "Drop at least one spot (circle tool).")
            return False
        return True

    def _run(self, jobs) -> bool:
        try:
            self.runner.run(jobs)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "Wavefront", str(e))
            return False
        return True

    def send_live(self):
        if self._check() and self._run([(f"live pattern ({len(self.spots)} spots)", live_script(self.spots), None)]):
            self.status.emit(f"Sent {len(self.spots)} spots to Wavefront (live, not saved)")

    # ------------------------------------------------------------ phase masks
    def generate_dialog(self):
        if not self._check():
            return
        out_dir = Path(self.config.wfd_output_dir)
        if not out_dir.is_dir():
            QtWidgets.QMessageBox.warning(self, "Phase mask folder not found",
                                          f"{out_dir}\n\nSet the folder where Wavefront saves the phase masks "
                                          f"in Settings > Rig settings.")
            return
        prefix = self.config.wfd_profile_prefix
        old = old_masks(out_dir, prefix)
        if old:
            choice = self._ask_old_masks(old, out_dir)
            if choice == "cancel":
                return
            if choice == "delete":
                try:
                    for p in old:
                        p.unlink()
                except OSError as e:
                    QtWidgets.QMessageBox.critical(self, "Cannot delete old phase masks", str(e))
                    return
                self.status.emit(f"Deleted {len(old)} old phase-mask files from {out_dir}")
        start = self.config.last_dir or str(Path.home())
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder for the record (.holo.json)", start)
        if not directory:
            return
        jobs, masks = [], []
        for i, spot in enumerate(self.spots, 1):
            profile = f"{prefix}{i}"
            png = mask_png(out_dir, profile)
            jobs.append((profile, mask_script(profile, spot), png))
            masks.append({"profile": profile, "spot": spot.name, "png": str(png)})
        self._record = {"directory": directory, "name": self.edit_name.text().strip() or "holo",
                        "spots": list(self.spots), "calibration": dataclasses.replace(self.calibration),
                        "image_path": self.image_path, "image_shape": self.image_shape, "masks": masks}
        if not self._run(jobs):
            self._record = None
            return
        self.config.last_dir = directory
        self.config.save()

    def _ask_old_masks(self, old: list[Path], out_dir: Path) -> str:
        """'delete' | 'keep' | 'cancel'."""
        first, last = date_range(old)
        dates = f"{first:%Y %m %d}" if first == last else f"{first:%Y %m %d} – {last:%Y %m %d}"
        profiles = sorted({p.name.split("_000.")[0] for p in old}, key=natural_key)
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Warning, "Old phase masks",
            f"{len(profiles)} old phase masks ({profiles[0]} … {profiles[-1]}, {len(old)} files) "
            f"dated {dates} in\n{out_dir}\n\nPlerion counts every {self.config.wfd_profile_prefix}<n> "
            f"there as a spot. Delete them before generating?", parent=self)
        b_delete = box.addButton("Delete them", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        b_keep = box.addButton("Keep them", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        return "delete" if clicked is b_delete else "keep" if clicked is b_keep else "cancel"

    # ------------------------------------------------------------------ runner
    def _on_busy(self, busy: bool):
        self.btn_live.setEnabled(not busy)
        self.btn_generate.setEnabled(not busy)
        generating = busy and self._record is not None
        self.progress.setVisible(generating)
        self.btn_cancel.setVisible(generating)

    def _on_progress(self, done: int, total: int, label: str):
        if self._record is not None:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f"{label}  %v / %m")

    def _on_finished(self, ok: bool, message: str):
        rec, self._record = self._record, None
        if rec is None:
            return
        made = {r["label"] for r in self.runner.results}
        for m in rec["masks"]:
            m["generated"] = m["profile"] in made
        path = write_holo_record(rec["directory"], rec["name"], rec["spots"], rec["calibration"],
                                 rec["image_path"], rec["image_shape"], rec["masks"])
        n = sum(m["generated"] for m in rec["masks"])
        self.status.emit(f"{n} / {len(rec['masks'])} phase masks generated; record {path}")
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Phase masks generated",
                f"{n} phase masks in {self.config.wfd_output_dir}\n"
                f"({rec['masks'][0]['profile']} … {rec['masks'][-1]['profile']})\n\nRecord: {path}")
