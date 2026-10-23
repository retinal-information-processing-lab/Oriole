import sys

import pytest

from oriole import shutter as shutter_mod
from oriole.shutter import Shutter, ShutterError


class FakeTask:
    def __init__(self, log, fail_write=False):
        self.log, self.fail_write = log, fail_write
        log.append("new")

    def write(self, v, auto_start=True):
        if self.fail_write:
            raise RuntimeError("resource reserved")
        self.log.append(v)

    def close(self):
        self.log.append("close")


@pytest.fixture
def log(monkeypatch):
    log = []
    monkeypatch.setattr(shutter_mod, "_new_task", lambda device, ao: FakeTask(log))
    return log


def test_open_holds_task_close_releases_it(log):
    s = Shutter("Dev1", 0, open_v=10.0)
    s.open()
    assert s.is_open and log == ["new", 10.0]
    s.open()                                    # already open: same task
    assert log == ["new", 10.0, 10.0]
    s.close()
    assert not s.is_open and log == ["new", 10.0, 10.0, 0.0, "close"]
    s.close()                                   # closing again still drives 0 V, on a fresh task
    assert log[-3:] == ["new", 0.0, "close"]


def test_write_failure_releases_task(monkeypatch):
    log = []
    monkeypatch.setattr(shutter_mod, "_new_task", lambda device, ao: FakeTask(log, fail_write=True))
    s = Shutter()
    with pytest.raises(ShutterError, match="Dev1/ao0"):
        s.open()
    assert not s.is_open and log == ["new", "close"]


def test_missing_nidaqmx_raises_shutter_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "nidaqmx", None)      # import nidaqmx -> ImportError
    with pytest.raises(ShutterError, match="nidaqmx"):
        Shutter().open()
