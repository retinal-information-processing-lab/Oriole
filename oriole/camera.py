"""Epi images from the running Micro-Manager, through its pycro-manager server.

Micro-Manager keeps the camera; Oriole asks it, over its server (Tools → Options → "Run pycro-manager server on port
4827", ticked once), for a snap at the rig's epi settings, 1000 ms exposure and binning 2x2, or, while Micro-Manager's
own live mode runs, for its latest frame. Only ``pyjavaz`` (with ``pyzmq``) is needed, imported when first used.
"""
from __future__ import annotations

import socket
import threading
import time

import numpy as np
from PyQt6 import QtCore

PORT = 4827
EXPOSURE_MS = 1000.0
BINNING = ("2x2", "2")              # the binning value, as the camera names it
TIMEOUT_MS = 10000                  # for each call; a snap takes the exposure and the readout
LIVE_PERIOD_S = 0.25                # live: a new frame 4 times a second at most (Micro-Manager live; a snap is 1 s)


class CameraError(RuntimeError):
    pass


def server_listening(port: int = PORT) -> bool:
    """Something answers on the port: says at once what the bridge would only say after its timeout."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class MicroManager:
    """Micro-Manager's core through its server. Every call blocks: Feed makes them in its own thread."""

    def __init__(self, port: int = PORT):
        self.port = port
        self._core = None
        self._binning: str | None = None

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
            self._binning = None
        return self._core

    def frame(self) -> tuple[np.ndarray, bool]:
        """(image, live): the latest frame of Micro-Manager's live mode while it runs, else a snap at the epi
        settings."""
        try:
            core = self._connect()
            live = core.is_sequence_running()
            if live:
                pixels = core.get_last_image()
            else:
                self._epi_settings(core)
                core.snap_image()
                pixels = core.get_image()
            return np.asarray(pixels).reshape(core.get_image_height(), core.get_image_width()), live
        except CameraError:
            raise
        except Exception as e:  # noqa: BLE001  (bridge timeout, Micro-Manager closed or busy...)
            self._core = None                       # connect again next time
            raise CameraError(f"Micro-Manager: {e}") from e

    def _epi_settings(self, core):
        if abs(core.get_exposure() - EXPOSURE_MS) > 0.5:
            core.set_exposure(EXPOSURE_MS)
        cam = core.get_camera_device()
        if self._binning is None and core.has_property(cam, "Binning"):
            allowed = core.get_allowed_property_values(cam, "Binning")
            values = [allowed.get(i) for i in range(allowed.size())]
            self._binning = next((v for v in BINNING if v in values), "")
        if self._binning and core.get_property(cam, "Binning") != self._binning:
            core.set_property(cam, "Binning", self._binning)


class Feed(QtCore.QObject):
    """Frames from Micro-Manager made in a thread of their own: one (snap), or one after the other while ``live``."""

    frame = QtCore.pyqtSignal(object, bool)         # image, from Micro-Manager's live mode
    failed = QtCore.pyqtSignal(str)

    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self.source = source or MicroManager()
        self.live = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def snap(self):
        self._start()

    def set_live(self, on: bool):
        self.live = on
        if on:
            self._start()

    def _start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="Micro-Manager", daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            t0 = time.monotonic()
            try:
                image, mm_live = self.source.frame()
            except CameraError as e:
                with self._lock:
                    self.live, self._thread = False, None
                self.failed.emit(str(e))
                return
            self.frame.emit(image, mm_live)
            if self.live:
                time.sleep(max(0.0, LIVE_PERIOD_S - (time.monotonic() - t0)))
            with self._lock:                        # decided under the lock: set_live(True) never finds a dying thread
                if not self.live:
                    self._thread = None
                    return
