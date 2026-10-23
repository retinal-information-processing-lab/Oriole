"""Qt glue for the holography hardware: Wavefront IV script queue and the stimulation shutter.

One WavefrontRunner and one ShutterController are owned by the main window and shared by the tabs.
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ..config import Config
from ..shutter import Shutter, ShutterError
from ..wavefront import SCRIPT_NAME, WavefrontClient


# ----------------------------------------------------------------------------- Wavefront
class WavefrontRunner(QtCore.QObject):
    """Feed scripts to Wavefront IV one at a time without blocking the GUI.

    A job is ``(label, js, expected_png or None)``. The next script is dropped once Wavefront
    has taken the previous one and, for a saved mask, once its png has been (re)written.
    """

    progress = QtCore.pyqtSignal(int, int, str)     # jobs done, total, label of the next one
    finished = QtCore.pyqtSignal(bool, str)         # ok, message
    busyChanged = QtCore.pyqtSignal(bool)
    slmChanged = QtCore.pyqtSignal(str)             # what the SLM now shows
    log = QtCore.pyqtSignal(str)

    POLL_MS = 100

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.jobs: list[tuple[str, str, Path | None]] = []
        self.results: list[dict] = []               # {"label", "png", "ok"} per finished job
        self._client: WavefrontClient | None = None
        self._state = "idle"                        # idle | script (wait for wfdIV to take it) | png
        self._t_submit = 0.0
        self._cancel = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.poll)

    @property
    def busy(self) -> bool:
        return self._state != "idle"

    def run(self, jobs):
        """Start a batch. Raises (nothing started) if Wavefront cannot take scripts right now."""
        if self.busy:
            raise RuntimeError("Wavefront is still busy with the previous request.")
        client = WavefrontClient(self.config.wfd_script_dir)
        if not client.available:
            raise FileNotFoundError(f"Wavefront script folder not found:\n{client.script_dir}\n\n"
                                    f"Set it in Settings > Rig settings.")
        self._client = client
        self.jobs = [(label, js, None if png is None else Path(png)) for label, js, png in jobs]
        self.results = []
        self._cancel = False
        self._submit()          # raises WavefrontBusy if an old script is still pending
        self.busyChanged.emit(True)
        self.timer.start()

    def cancel(self):
        self._cancel = True

    def _submit(self):
        label, js, _ = self.jobs[len(self.results)]
        self._client.submit(js)
        self._t_submit = time.time()
        self._state = "script"
        self.progress.emit(len(self.results), len(self.jobs), label)
        self.log.emit(f"sent {label}")

    def poll(self):
        if not self.busy:
            return
        label, _, png = self.jobs[len(self.results)]
        waited = time.time() - self._t_submit
        if self._state == "script":
            if self._client.busy:
                if waited > self.config.wfd_timeout_s:
                    self._finish(False, f"Wavefront did not take {SCRIPT_NAME} within {waited:.0f} s: is "
                                        f"Wavefront IV running with script auto-load on?")
                return
            if png is None:
                self._job_done(label, None)
                return
            self._state = "png"
        # the png must be (re)written after the submission, not left from an older run
        if png.exists() and png.stat().st_mtime >= self._t_submit - 1:
            self._job_done(label, png)
        elif waited > self.config.wfd_timeout_s:
            self._finish(False, f"{png.name} did not appear within {waited:.0f} s.\nCheck that the "
                                f"phase mask folder in Settings ({png.parent}) is where Wavefront saves.")

    def _job_done(self, label: str, png: Path | None):
        self.results.append({"label": label, "png": str(png) if png else "", "ok": True})
        self.log.emit(f"done {label}" + (f" -> {png.name}" if png else ""))
        self.slmChanged.emit(label)
        n = len(self.results)
        if n == len(self.jobs):
            self._finish(True, f"{n} script{'s' if n > 1 else ''} done.")
        elif self._cancel:
            self._finish(False, f"Cancelled after {n} of {len(self.jobs)}.")
        else:
            try:
                self._submit()
            except Exception as e:  # noqa: BLE001
                self._finish(False, str(e))

    def _finish(self, ok: bool, message: str):
        self.timer.stop()
        self._state = "idle"
        self.log.emit(message)
        self.busyChanged.emit(False)
        self.finished.emit(ok, message)


# ----------------------------------------------------------------------------- shutter
class ShutterController(QtCore.QObject):
    stateChanged = QtCore.pyqtSignal(bool)          # open?
    error = QtCore.pyqtSignal(str)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.shutter = Shutter(config.nidaq_device, config.shutter_ao, config.shutter_open_v)
        self.pulse_timer = QtCore.QTimer(self)
        self.pulse_timer.setSingleShot(True)
        self.pulse_timer.timeout.connect(self.close)

    def set_config(self, config: Config):
        self.config = config
        if not self.shutter.is_open:        # an open shutter keeps its channel until closed
            self.shutter = Shutter(config.nidaq_device, config.shutter_ao, config.shutter_open_v)

    @property
    def is_open(self) -> bool:
        return self.shutter.is_open

    def open(self) -> bool:
        try:
            self.shutter.open()
        except ShutterError as e:
            self.error.emit(str(e))
            return False
        self.stateChanged.emit(True)
        return True

    def close(self) -> bool:
        self.pulse_timer.stop()
        try:
            self.shutter.close()
        except ShutterError as e:
            self.error.emit(str(e))
            return False
        finally:
            self.stateChanged.emit(self.shutter.is_open)
        return True

    def pulse(self, ms: int):
        if self.open():
            self.pulse_timer.start(int(ms))

    def shutdown(self):
        """On exit: close only if Oriole opened it (no hardware access otherwise)."""
        if self.is_open:
            self.close()


class ShutterPanel(QtWidgets.QGroupBox):
    """Open / Close / Pulse buttons with the state; several panels can share one controller."""

    def __init__(self, controller: ShutterController, parent=None):
        super().__init__("Shutter", parent)
        self.controller = controller
        grid = QtWidgets.QGridLayout(self)
        self.lbl_state = QtWidgets.QLabel()
        self.lbl_state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.btn_open = QtWidgets.QPushButton("Open")
        self.btn_close = QtWidgets.QPushButton("Close")
        self.spin_pulse = QtWidgets.QSpinBox()
        self.spin_pulse.setRange(1, 600000)
        self.spin_pulse.setValue(500)
        self.spin_pulse.setSuffix(" ms")
        self.btn_pulse = QtWidgets.QPushButton("Pulse")
        grid.addWidget(self.lbl_state, 0, 0, 1, 2)
        grid.addWidget(self.btn_open, 0, 2)
        grid.addWidget(self.btn_close, 0, 3)
        grid.addWidget(QtWidgets.QLabel("open for"), 1, 0)
        grid.addWidget(self.spin_pulse, 1, 1, 1, 2)
        grid.addWidget(self.btn_pulse, 1, 3)
        self.btn_open.clicked.connect(controller.open)
        self.btn_close.clicked.connect(controller.close)
        self.btn_pulse.clicked.connect(lambda: controller.pulse(self.spin_pulse.value()))
        controller.stateChanged.connect(self.show_state)
        self.show_state(controller.is_open)

    def show_state(self, is_open: bool):
        ch = self.controller.shutter.channel
        if is_open:
            self.lbl_state.setText(f"<b>OPEN</b> ({ch})")
            self.lbl_state.setStyleSheet("QLabel { background: #e65100; color: white; padding: 3px; }")
        else:
            self.lbl_state.setText(f"closed ({ch})")
            self.lbl_state.setStyleSheet("QLabel { background: #555555; color: white; padding: 3px; }")
