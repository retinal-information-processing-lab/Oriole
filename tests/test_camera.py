import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtWidgets  # noqa: E402

from oriole import camera  # noqa: E402
from oriole.camera import CameraError, Feed, MicroManager  # noqa: E402


class FakeList:
    """A Java List, as the bridge shows it."""

    def __init__(self, items):
        self.items = items

    def size(self):
        return len(self.items)

    def get(self, i):
        return self.items[i]


class FakeImage:
    def __init__(self, value):
        self.value = value

    def get_raw_pixels(self):
        return np.full(512 * 672, self.value, np.uint16)

    def get_width(self):
        return 672

    def get_height(self):
        return 512


class FakeDisplay:
    """Micro-Manager's Snap/Live window."""

    def __init__(self, images):
        self.images = images

    def get_displayed_images(self):
        return FakeList(self.images)


class FakeLive:
    """Micro-Manager's live manager (studio.live()), as the bridge shows it."""

    def __init__(self, core):
        self.core, self.calls, self.display = core, [], None

    def get_display(self):
        return self.display

    def is_live_mode_on(self):
        return self.core.sequence

    def set_live_mode_on(self, on):
        self.calls.append(("live", on))
        self.core.sequence = on

    def snap(self, display):
        self.calls.append(("snap", display))
        self.core.snapped = np.arange(512 * 672, dtype=np.uint16)


class FakeCore:
    """mmcorej.CMMCore as the bridge shows it (snake_case), the rig's camera binned 2x2. It has no setter at all:
    Oriole must never change the camera settings."""

    def __init__(self, sequence=False, binning="2x2", exposure=100.0):
        self.sequence, self.binning, self.exposure = sequence, binning, exposure
        self.snapped, self.frames = None, 0

    def get_camera_device(self):
        return "HamamatsuHam_DCAM"

    def has_property(self, cam, name):
        return name == "Binning"

    def get_property(self, cam, name):
        return self.binning

    def get_exposure(self):
        return self.exposure

    def is_sequence_running(self):
        return self.sequence

    def get_image_height(self):
        return 512

    def get_image_width(self):
        return 672

    def get_image(self):
        return self.snapped

    def get_last_image(self):
        if not self.frames:
            raise RuntimeError("Circular buffer is empty.")
        return np.full(512 * 672, self.frames, np.uint16)


def _mm(core) -> MicroManager:
    mm = MicroManager()
    mm._core, mm._live = core, FakeLive(core)
    return mm


def test_snap_is_micro_managers_and_settings_are_only_read():
    core = FakeCore(binning="1x1", exposure=50.0)
    mm = _mm(core)
    assert mm.settings() == ("1x1", 50.0)
    image = mm.snap()                                   # its own Snap, shown in Micro-Manager too
    assert image.shape == (512, 672) and image[1, 0] == 672 and mm._live.calls == [("snap", True)]
    core.sequence, core.frames = True, 7                # its live running: its current frame, nothing commanded
    assert mm.snap()[0, 0] == 7 and mm._live.calls == [("snap", True)]


def test_shown_is_what_micro_managers_window_shows():
    mm = _mm(FakeCore())
    with pytest.raises(CameraError, match="Micro-Manager shows no image yet: press Snap"):
        mm.shown()                                      # no Snap/Live window yet
    mm._live.display = FakeDisplay([])
    with pytest.raises(CameraError, match="shows no image yet"):
        mm.shown()
    assert mm._core is not None                         # not a broken connection
    mm._live.display = FakeDisplay([FakeImage(9)])
    image = mm.shown()
    assert image.shape == (512, 672) and image[0, 0] == 9 and mm._live.calls == []     # nothing taken


def test_live_frames():
    core = FakeCore()
    mm = _mm(core)
    assert not mm.live_on() and mm.last_frame() is None  # no live: no frame
    mm.set_live(True)
    assert mm.live_on() and mm.last_frame() is None     # live just switched on: no frame yet, not an error
    core.frames = 3
    assert mm.last_frame()[5, 5] == 3


def test_errors(monkeypatch):
    monkeypatch.setattr(camera, "server_listening", lambda port=camera.PORT: False)
    with pytest.raises(CameraError, match='"Run pycro-manager server on port 4827" ticked'):
        MicroManager().snap()
    monkeypatch.setattr(camera, "server_listening", lambda port=camera.PORT: True)
    monkeypatch.setitem(sys.modules, "pyjavaz", None)   # import pyjavaz -> ImportError
    with pytest.raises(CameraError, match="pip install pyjavaz"):
        MicroManager().snap()
    core = FakeCore(sequence=True, exposure=1000.0)
    core.frames = 1

    def broken():
        raise RuntimeError("Camera image buffer read failed.")
    core.get_last_image = broken
    mm = _mm(core)
    with pytest.raises(CameraError, match="Micro-Manager: Camera image buffer read failed"):
        mm.last_frame()
    assert mm._core is None and mm._live is None        # connected again next time


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
    """MicroManager as Feed uses it: a live mode, frames counted, failures and a frozen camera on demand."""

    def __init__(self, live=False, settings=("2x2", 1000.0)):
        self.mm_live, self.settings_, self.n = live, settings, 0
        self.calls, self.fail, self.frozen = [], 0, False

    def shown(self):
        return np.full((4, 6), 100, np.uint16)

    def settings(self):
        return self.settings_

    def snap(self):
        self.n += 1
        return np.full((4, 6), self.n, np.uint16)

    def live_on(self):
        return self.mm_live

    def set_live(self, on):
        self.calls.append(on)
        self.mm_live = on

    def last_frame(self):
        if self.fail:
            self.fail -= 1
            raise CameraError("Micro-Manager: Camera image buffer read failed.")
        if not self.frozen:
            self.n += 1
        return np.full((4, 6), self.n, np.uint16)


class Recorder:
    def __init__(self, feed):
        self.frames, self.kinds, self.notes, self.ended = [], [], [], []
        feed.frame.connect(lambda image, kind: (self.frames.append(int(image[0, 0])), self.kinds.append(kind)))
        feed.note.connect(lambda text, level: self.notes.append((text, level)))
        feed.ended.connect(lambda text, level: self.ended.append((text, level)))


def test_feed_snap_and_live_leave_micro_manager_as_found(qapp, monkeypatch):
    monkeypatch.setattr(camera, "LIVE_PERIOD_S", 0.002)
    src = FakeSource(settings=("1x1", 100.0))
    feed = Feed(src)
    rec = Recorder(feed)
    feed.snap()
    feed.snap()                                         # a double click: one Snap (one request waits at most)
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert rec.frames == [1] and rec.kinds == ["snap"] and rec.notes == [
        ("Micro-Manager camera: binning 1x1, 100 ms (epi images: 2x2, 1000 ms; set them in Micro-Manager)", "warn")]
    feed.show()                                         # the image Micro-Manager shows: nothing taken
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert rec.frames == [1, 100] and rec.kinds == ["snap", "shown"] and src.n == 1
    rec.frames.clear()

    # live: Micro-Manager's own live switched on, its frames shown in order, switched off again after
    feed.set_live(True)
    wait_for(lambda: len(rec.frames) >= 5)
    feed.set_live(False)
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert src.calls == [True, False] and not src.mm_live and rec.frames == sorted(set(rec.frames))
    assert set(rec.kinds[2:]) == {"live"}
    assert len(rec.notes) == 1                          # settings unchanged: said once

    # its live already on (switched on in Micro-Manager): left on
    src.mm_live = True
    feed.set_live(True)
    wait_for(lambda: len(rec.frames) >= 8)
    feed.close()
    assert not feed.busy and src.mm_live and src.calls == [True, False] and not rec.ended


def test_feed_live_survives_a_bad_frame_and_says_what_ends_it(qapp, monkeypatch):
    monkeypatch.setattr(camera, "LIVE_PERIOD_S", 0.002)
    src = FakeSource()
    feed = Feed(src)
    rec = Recorder(feed)
    feed.set_live(True)
    wait_for(lambda: len(rec.frames) >= 2)
    src.fail = 2                                        # two bad frames: skipped, live goes on
    n = len(rec.frames)
    wait_for(lambda: len(rec.frames) >= n + 3)
    assert [lvl for _, lvl in rec.notes].count("warn") == 2 and "frame skipped" in rec.notes[-1][0]
    assert feed.live and not rec.ended
    src.fail = 3                                        # three in a row: live off, Micro-Manager's too
    wait_for(lambda: rec.ended)
    assert rec.ended == [("Micro-Manager: Camera image buffer read failed. (3 frames in a row): Live off", "error")]
    assert not feed.live and not feed.busy and src.calls == [True, False]

    # stopped in Micro-Manager: stopped here too
    rec.ended.clear()
    feed.set_live(True)
    wait_for(lambda: src.mm_live and len(rec.frames) > 0)
    src.mm_live = False
    wait_for(lambda: rec.ended)
    assert rec.ended == [("Micro-Manager's live was stopped: Oriole's Live too", "warn")] and not feed.busy


def test_feed_says_a_frozen_or_silent_camera(qapp, monkeypatch):
    monkeypatch.setattr(camera, "LIVE_PERIOD_S", 0.002)
    monkeypatch.setattr(camera, "FROZEN_S", 0.05)
    src = FakeSource(settings=("2x2", 0.0))
    feed = Feed(src)
    rec = Recorder(feed)
    feed.set_live(True)
    wait_for(lambda: rec.frames)
    src.frozen = True                                   # the same frame again and again: said once
    wait_for(lambda: any("unchanged" in text for text, _ in rec.notes))
    time.sleep(0.1)
    QtWidgets.QApplication.processEvents()
    assert sum("unchanged for" in text for text, _ in rec.notes) == 1 and feed.live
    feed.close()

    # live switched on, but no frame ever comes: said, live off
    monkeypatch.setattr(camera, "FIRST_FRAME_S", 0.05)
    src = FakeSource()
    src.last_frame = lambda: None
    feed = Feed(src)
    rec = Recorder(feed)
    feed.set_live(True)
    wait_for(lambda: rec.ended)
    assert rec.ended == [("no frame from Micro-Manager's live within 0 s: Live off", "error")] and src.calls == [
        True, False]


def test_feed_request_during_a_snap_comes_right_after(qapp):
    import threading
    src = FakeSource()
    release = threading.Event()
    snap = src.snap

    def slow_snap():                                    # a 1000 ms Snap, held until released
        release.wait(5)
        return snap()
    src.snap = slow_snap
    feed = Feed(src)
    rec = Recorder(feed)
    feed.snap()
    wait_for(lambda: feed._pending is None and feed.busy)  # the first Snap is running
    feed.snap()                                         # pressed meanwhile: not lost
    release.set()
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert rec.frames == [1, 2] and rec.kinds == ["snap", "snap"]


def test_feed_snap_asked_during_live_comes_once_live_is_off(qapp, monkeypatch):
    monkeypatch.setattr(camera, "LIVE_PERIOD_S", 0.002)
    src = FakeSource()
    feed = Feed(src)
    rec = Recorder(feed)
    feed.set_live(True)
    wait_for(lambda: len(rec.frames) >= 2)
    feed.set_live(False)                                # what the window does on Snap: Live off, then the Snap
    feed.snap()
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert rec.kinds[-1] == "snap" and set(rec.kinds[:-1]) == {"live"} and src.calls == [True, False]
    feed.set_live(True)                                 # a request never finds a dying thread
    feed.set_live(False)
    feed.show()
    wait_for(lambda: not feed.busy)
    QtWidgets.QApplication.processEvents()
    assert rec.kinds[-1] == "shown" and not rec.ended
