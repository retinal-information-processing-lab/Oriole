import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets  # noqa: E402

from oriole import camera  # noqa: E402
from oriole.camera import CameraError, Feed, MicroManager  # noqa: E402


class FakeVector:
    """A Micro-Manager StrVector, as the bridge shows it."""

    def __init__(self, values):
        self.values = values

    def size(self):
        return len(self.values)

    def get(self, i):
        return self.values[i]


class FakeCore:
    """mmcorej.CMMCore as pyjavaz shows it (snake_case names): a 1344 x 1024 camera."""

    def __init__(self, live=False, binnings=("1x1", "2x2", "4x4")):
        self.live, self.binnings = live, binnings
        self.exposure, self.binning, self.calls = 10.0, binnings[0], []

    def is_sequence_running(self):
        return self.live

    def get_exposure(self):
        return self.exposure

    def set_exposure(self, ms):
        self.calls.append(("exposure", ms))
        self.exposure = ms

    def get_camera_device(self):
        return "HamamatsuHam_DCAM"

    def has_property(self, cam, name):
        return name == "Binning"

    def get_allowed_property_values(self, cam, name):
        self.calls.append("allowed")
        return FakeVector(list(self.binnings))

    def get_property(self, cam, name):
        return self.binning

    def set_property(self, cam, name, value):
        self.calls.append(("binning", value))
        self.binning = value

    def get_image_height(self):
        return 1024 // int(self.binning[0])

    def get_image_width(self):
        return 1344 // int(self.binning[0])

    def snap_image(self):
        self.calls.append("snap")

    def get_image(self):
        return np.arange(self.get_image_height() * self.get_image_width(), dtype=np.uint16)

    def get_last_image(self):
        self.calls.append("last")
        return np.zeros(self.get_image_height() * self.get_image_width(), np.uint16)


def _mm(core) -> MicroManager:
    mm = MicroManager()
    mm._core = core
    return mm


def test_snap_at_the_epi_settings():
    core = FakeCore()
    mm = _mm(core)
    image, live = mm.frame()
    assert image.shape == (512, 672) and image.dtype == np.uint16 and not live and image[1, 0] == 672
    assert core.calls == [("exposure", 1000.0), "allowed", ("binning", "2x2"), "snap"]
    core.calls.clear()
    mm.frame()
    assert core.calls == ["snap"]                       # already at the epi settings: only the snap
    core = FakeCore(binnings=("1", "2", "4"))           # a camera naming its binning "2"
    _mm(core).frame()
    assert ("binning", "2") in core.calls


def test_live_mode_of_micro_manager_is_only_read():
    core = FakeCore(live=True)
    image, live = _mm(core).frame()
    assert live and core.calls == ["last"] and image.shape == (1024, 1344)     # its settings left alone


def test_errors(monkeypatch):
    monkeypatch.setattr(camera, "server_listening", lambda port=camera.PORT: False)
    with pytest.raises(CameraError, match='"Run pycro-manager server on port 4827" ticked'):
        MicroManager().frame()
    monkeypatch.setattr(camera, "server_listening", lambda port=camera.PORT: True)
    monkeypatch.setitem(sys.modules, "pyjavaz", None)   # import pyjavaz -> ImportError
    with pytest.raises(CameraError, match="pip install pyjavaz"):
        MicroManager().frame()
    core = FakeCore()

    def busy():
        raise RuntimeError("Camera busy")
    core.snap_image = busy
    mm = _mm(core)
    with pytest.raises(CameraError, match="Micro-Manager: Camera busy"):
        mm.frame()
    assert mm._core is None                             # connected again next time


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_for(condition, timeout=5.0):
    """Process Qt events (the frames come from another thread) until ``condition()``."""
    t0 = time.monotonic()
    while not condition():
        QtWidgets.QApplication.processEvents()
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.002)


class FakeSource:
    def __init__(self):
        self.n, self.error = 0, None

    def frame(self):
        if self.error:
            raise CameraError(self.error)
        self.n += 1
        return np.full((4, 6), self.n, np.uint16), False


def test_feed_snap_live_and_failure(qapp, monkeypatch):
    monkeypatch.setattr(camera, "LIVE_PERIOD_S", 0.005)
    src = FakeSource()
    feed = Feed(src)
    frames, errors = [], []
    feed.frame.connect(lambda image, live: frames.append(int(image[0, 0])))
    feed.failed.connect(errors.append)
    feed.snap()
    feed.snap()                                         # a snap still running: not another one
    wait_for(lambda: not feed.busy)
    wait_for(lambda: frames == [1])
    feed.set_live(True)
    wait_for(lambda: len(frames) >= 4)
    feed.set_live(False)
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    n = len(frames)
    assert frames == list(range(1, n + 1))              # every frame, in order
    time.sleep(0.05)
    QtWidgets.QApplication.processEvents()
    assert len(frames) == n                             # stopped
    src.error = "Micro-Manager: gone"
    feed.set_live(True)
    wait_for(lambda: errors == ["Micro-Manager: gone"])
    assert not feed.live and not feed.busy
