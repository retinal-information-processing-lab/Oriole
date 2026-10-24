"""Epi images from the running Micro-Manager, through its pycro-manager server.

Micro-Manager keeps the camera and Oriole never commands it directly: it asks Micro-Manager, over its server
(Tools → Options → "Run pycro-manager server on port 4827", ticked once), for Micro-Manager's own Snap, or switches
Micro-Manager's own Live on and reads its frames. Oriole never changes the camera settings either (changing the
binning from Oriole left the rig's Hamamatsu sending black frames until Micro-Manager was restarted): it reports them,
the epi settings being binning 2x2 and 1000 ms. Only ``pyjavaz`` (with ``pyzmq``) is needed, imported when first used.
"""
from __future__ import annotations

import socket
import threading
import time

import numpy as np
from PyQt6 import QtCore

PORT = 4827
EPI_BINNING, EPI_EXPOSURE_MS = "2x2", 1000.0  # what the epi images are taken with (set in Micro-Manager)
TIMEOUT_MS = 10000                  # for each call; a snap takes the exposure and the readout
LIVE_PERIOD_S = 0.25                # live: a frame read 4 times a second at most
MAX_FAILURES = 3                    # live: frames failing in a row before giving up
FROZEN_S = 5.0                      # live: the same frame for this long (plus 2 exposures) is said: camera stuck?
FIRST_FRAME_S = 5.0                 # live just switched on: its first frame awaited this long


class CameraError(RuntimeError):
    pass


def server_listening(port: int = PORT) -> bool:
    """Something answers on the port: says at once what the bridge would only say after its timeout."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class MicroManager:
    """Micro-Manager's core and its live manager, through its server. Every call blocks: Feed makes them in its own
    thread. Only Micro-Manager's own Snap and Live touch the camera."""

    def __init__(self, port: int = PORT):
        self.port = port
        self._core = None
        self._live = None

    def _connect(self):
        if self._core is None:
            if not server_listening(self.port):
                raise CameraError(f"Micro-Manager not found on port {self.port}: start it, with Tools → Options → "
                                  f'"Run pycro-manager server on port {self.port}" ticked')
            try:
                from pyjavaz import JavaObject
            except ImportError as e:
                raise CameraError("pyjavaz is not installed: pip install pyjavaz") from e
            self._core = JavaObject("mmcorej.CMMCore", port=self.port, timeout=TIMEOUT_MS)
            self._live = JavaObject("org.micromanager.Studio", port=self.port, timeout=TIMEOUT_MS).live()
        return self._core, self._live

    def _call(self, what):
        """``what(core, live)``, its failure a CameraError (and a new connection next time)."""
        try:
            return what(*self._connect())
        except CameraError:
            raise
        except Exception as e:  # noqa: BLE001  (bridge timeout, Micro-Manager closed or busy...)
            self._core = self._live = None
            raise CameraError(f"Micro-Manager: {e}") from e

    @staticmethod
    def _image(core, pixels) -> np.ndarray:
        return np.asarray(pixels).reshape(core.get_image_height(), core.get_image_width())

    def settings(self) -> tuple[str, float]:
        """The camera's binning and exposure (ms), as Micro-Manager has them."""
        def read(core, live):
            cam = core.get_camera_device()
            return (core.get_property(cam, "Binning") if core.has_property(cam, "Binning") else "?",
                    float(core.get_exposure()))
        return self._call(read)

    def snap(self) -> np.ndarray:
        """Micro-Manager's current frame while it streams (its live), else its own Snap."""
        def snap(core, live):
            if core.is_sequence_running():
                return self._image(core, core.get_last_image())
            live.snap(True)                         # as its Snap button: shown in Micro-Manager too
            return self._image(core, core.get_image())
        return self._call(snap)

    def shown(self) -> np.ndarray:
        """The image Micro-Manager's Snap/Live window shows (its last Snap, or its live frame): nothing taken."""
        def shown(core, live):
            display = live.get_display()
            images = display.get_displayed_images() if display is not None else None
            if images is None or images.size() == 0:
                raise CameraError("Micro-Manager shows no image yet: press Snap (or Live) there first")
            image = images.get(0)
            return np.asarray(image.get_raw_pixels()).reshape(image.get_height(), image.get_width())
        return self._call(shown)

    def live_on(self) -> bool:
        return bool(self._call(lambda core, live: live.is_live_mode_on()))

    def set_live(self, on: bool):
        self._call(lambda core, live: live.set_live_mode_on(on))

    def last_frame(self) -> np.ndarray | None:
        """The latest frame of Micro-Manager's live; None while it has none yet (live just switched on)."""
        def last(core, live):
            if not core.is_sequence_running():
                return None
            try:
                pixels = core.get_last_image()
            except Exception as e:  # noqa: BLE001
                if "empty" in str(e).lower():       # "Circular buffer is empty": no frame yet
                    return None
                raise
            return self._image(core, pixels)
        return self._call(last)


class Feed(QtCore.QObject):
    """Images from Micro-Manager, in a thread of their own: one (``snap``: its Snap; ``show``: the image its window
    shows, nothing taken), or one after the other while ``live``. A Snap or Update asked while the thread is busy is
    done right after (one waits at most): nothing asked is ever lost.

    Live switches Micro-Manager's own live mode on (when it is not already), then reads its frames: stopping it in
    Micro-Manager stops it here too, and stopping it here stops Micro-Manager's only if Oriole had switched it on.
    A frame that fails is skipped, MAX_FAILURES in a row end the live; a frame unchanged for FROZEN_S is said.
    """

    frame = QtCore.pyqtSignal(object, str)          # image, how it came: "snap", "shown" or "live"
    note = QtCore.pyqtSignal(str, str)              # text, log level: settings, skipped frame, frozen camera
    ended = QtCore.pyqtSignal(str, str)             # text, log level: a snap or live failed, or live was stopped

    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self.source = source or MicroManager()
        self.live = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: str | None = None            # "snap" or "shown", done once the thread is free
        self._settings: tuple[str, float] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def snap(self):
        self._ask("snap")

    def show(self):
        self._ask("shown")

    def set_live(self, on: bool):
        self.live = on
        if on:
            self._ask(None)

    def close(self, wait_s: float = 2.0):
        """Live off (Micro-Manager's too if Oriole switched it on), nothing more asked, the thread waited for a
        moment."""
        with self._lock:
            self.live, self._pending = False, None
            thread = self._thread
        if thread is not None:
            thread.join(wait_s)

    def _ask(self, job: str | None):
        with self._lock:
            if job is not None:
                self._pending = job
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="Micro-Manager", daemon=True)
                self._thread.start()

    def _next_job(self) -> str | None:
        """Live while it is on, else what was asked; None ends the thread (decided under the lock: a request never
        finds a dying thread)."""
        with self._lock:
            if self.live:
                return "live"
            job, self._pending = self._pending, None
            if job is None:
                self._thread = None
            return job

    def _report_settings(self) -> float:
        """Binning and exposure said when they change; the exposure (s)."""
        binning, exposure = self.source.settings()
        if (binning, exposure) != self._settings:
            self._settings = (binning, exposure)
            epi = binning == EPI_BINNING and abs(exposure - EPI_EXPOSURE_MS) < 1
            self.note.emit(f"Micro-Manager camera: binning {binning}, {exposure:.0f} ms" +
                           ("" if epi else f" (epi images: {EPI_BINNING}, {EPI_EXPOSURE_MS:.0f} ms; set them in "
                                           f"Micro-Manager)"), "info" if epi else "warn")
        return exposure / 1000

    def _run(self):
        try:
            while (job := self._next_job()) is not None:
                exposure_s = self._report_settings()
                if job != "live":
                    image = self.source.snap() if job == "snap" else self.source.shown()
                    self.frame.emit(image, job)
                elif reason := self._run_live(exposure_s):
                    self.live = False
                    self.ended.emit(*reason)
        except CameraError as e:
            with self._lock:
                self.live, self._pending, self._thread = False, None, None
            self.ended.emit(str(e), "error")

    def _run_live(self, exposure_s: float) -> tuple[str, str] | None:
        """Frames of Micro-Manager's live while ``live``; why it must end, if it must."""
        mine = not self.source.live_on()            # Micro-Manager's live switched on by Oriole: off again after
        if mine:
            self.source.set_live(True)
        failures, last, t_start = 0, None, time.monotonic()
        same_since, said = t_start, False
        try:
            while self.live:
                t0 = time.monotonic()
                try:
                    if not self.source.live_on():
                        mine = False
                        return "Micro-Manager's live was stopped: Oriole's Live too", "warn"
                    image = self.source.last_frame()
                    failures = 0
                except CameraError as e:
                    failures += 1
                    if failures >= MAX_FAILURES:
                        return f"{e} ({failures} frames in a row): Live off", "error"
                    self.note.emit(f"{e}: frame skipped", "warn")
                    image = None
                if image is None:
                    if last is None and t0 - t_start > FIRST_FRAME_S:
                        return f"no frame from Micro-Manager's live within {FIRST_FRAME_S:.0f} s: Live off", "error"
                elif last is not None and np.array_equal(image, last):
                    if not said and t0 - same_since > FROZEN_S + 2 * exposure_s:
                        said = True
                        black = " and black" if not image.any() else ""
                        self.note.emit(f"Micro-Manager's live frame unchanged{black} for {t0 - same_since:.0f} s: "
                                       f"camera stuck? (see Micro-Manager; restarting it frees the camera)", "warn")
                else:
                    last, same_since, said = image, t0, False
                    self.frame.emit(image, "live")
                time.sleep(max(0.0, LIVE_PERIOD_S - (time.monotonic() - t0)))
            return None
        finally:
            if mine:                                # leave Micro-Manager as found
                try:
                    self.source.set_live(False)
                except CameraError:
                    pass
