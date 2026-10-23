import datetime
import json
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pg = pytest.importorskip("pyqtgraph")
from PyQt6 import QtGui, QtWidgets  # noqa: E402

from oriole.binfile import read_header  # noqa: E402
from oriole.config import Config  # noqa: E402
from oriole.gui import MainWindow, apply_dark_theme  # noqa: E402
from oriole.shutter import ShutterError  # noqa: E402
from tests.conftest import EXISTING_BIN  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_dark_theme(app)
    return app


class FakeTask:
    def __init__(self, log):
        self.log = log
        log.append("new")

    def write(self, v, auto_start=True):
        self.log.append(v)

    def close(self):
        self.log.append("close")


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    daq_log = []
    monkeypatch.setattr("oriole.shutter._new_task", lambda device, ao: FakeTask(daq_log))
    cfg = Config(wfd_script_dir=str(tmp_path / "wfd" / "script"), wfd_output_dir=str(tmp_path / "wfd" / "masks"))
    for d in (cfg.wfd_script_dir, cfg.wfd_output_dir):
        os.makedirs(d)
    w = MainWindow(cfg, config_path=tmp_path / "cfg.json")
    w.daq_log = daq_log
    w.show()
    yield w
    w.close()


def _simulate_wavefront(w) -> str:
    """What Wavefront IV does with a dropped script: run it (saving the mask if asked), remove it."""
    script = Path(w.config.wfd_script_dir) / "test.js"
    js = script.read_text()
    m = re.search(r'setProfileName\("(\w+)"\);', js)
    if m and "saveProfile" in js:
        (Path(w.config.wfd_output_dir) / f"{m.group(1)}_000.algoPhp.png").write_bytes(b"png")
    script.unlink()
    (script.parent / "zdone.js").unlink()
    w.poll()
    return js


def test_end_to_end(window, tmp_path, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    assert not w.findChildren(pg.HistogramLUTWidget)                   # no power / contrast scale
    assert not [b for b in w.findChildren(QtWidgets.QPushButton) if b.text() == "Reset"]
    assert QtWidgets.QApplication.instance().palette().color(QtGui.QPalette.ColorRole.Window).name() == "#121212"

    # --- spots: left click adds, right click on a spot removes, undo
    w._on_click(10, 10, False)
    assert "load an image first" in log()
    w.set_image(np.zeros((100, 100), np.float32), "cells.tif")
    for x, y in ((69.5, 29.5), (30.0, 50.0), (80.0, 80.0)):
        w._on_click(x, y, False)
    w._on_click(80.5, 80.5, True)
    assert w.spots == [(69.5, 29.5), (30.0, 50.0)] and "2 spots" in w.lbl_count.text()
    w.undo()
    assert len(w.spots) == 3
    w._on_click(80.0, 80.0, True)
    assert len(w.view.labels) == 2

    # --- calibrations are edited in place and kept in the config
    w.holo_box.spins["x_um"].setValue(6.0)
    assert w.config.holo.x_um == 6.0

    # --- visual: extract the spot bin, merge it into a linked bin
    (tmp_path / "bins").mkdir()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path / "bins")))
    w.extract()
    spot_bin = w.last_spot_bin
    assert spot_bin.name == f"{datetime.date.today():%Y_%m_%d}_2Frames.bin"
    assert read_header(spot_bin)["nb_images"] == 4
    info = json.loads(Path(str(spot_bin) + ".json").read_text())
    assert (info["spots"][0]["x_um"], info["spots"][0]["y_um"]) == pytest.approx((13.4, -13.4))   # no mirror
    linked = tmp_path / "linked.bin"
    shutil.copy(EXISTING_BIN, linked)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(linked), "")))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (a[2], "")))
    w.merge()
    merged = tmp_path / f"linked_{spot_bin.stem}.bin"
    assert read_header(merged)["nb_images"] == 15 + 4 and w.config.linked_bin == str(linked)
    assert "spot 1 at frames 15/16" in log()

    # --- holo: old masks reported and deleted, then one mask per spot
    out_dir, script_dir = Path(w.config.wfd_output_dir), Path(w.config.wfd_script_dir)
    old = out_dir / "Pattern7_000.algoPhp.png"
    old.write_bytes(b"old")
    t = datetime.datetime(2026, 10, 20, 9).timestamp()
    os.utime(old, (t, t))
    asked = []
    monkeypatch.setattr(w, "ask_old_masks", lambda files: asked.append([p.name for p in files]) or "delete")
    w.generate()
    assert asked == [["Pattern7_000.algoPhp.png"]] and not old.exists() and not w.btn_generate.isEnabled()
    js1 = _simulate_wavefront(w)
    assert 'setProfileName("Pattern1");' in js1 and "drawSpot(19.4000,5.4000,10.0000);" in js1
    js2 = _simulate_wavefront(w)
    assert 'setProfileName("Pattern2");' in js2 and "saveProfile(0);" in js2
    assert w.btn_generate.isEnabled() and "2 phase masks saved" in log()
    rec = json.loads((out_dir / "Pattern_spots.json").read_text())
    assert [m["mask"] for m in rec["masks"]] == ["Pattern1_000.algoPhp.png", "Pattern2_000.algoPhp.png"]

    # --- manual: load a mask, shutter open / close; closing the window closes the shutter
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(out_dir / "Pattern2_000.algoPhp.png"), "")))
    w.btn_load_mask.click()
    escaped = str(out_dir / "Pattern2_000.algoPhp.png").replace("\\", "\\\\")
    assert (script_dir / "test.js").read_text().endswith(f'loadProfile("{escaped}", 2);\n')
    w.btn_open.click()
    assert w.shutter.is_open and w.daq_log == ["new", 10.0] and "OPEN" in w.lbl_shutter.text()
    w.btn_close.click()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"] and "closed" in w.lbl_shutter.text()
    w.btn_open.click()
    w.close()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"]
    assert json.loads((tmp_path / "cfg.json").read_text())["holo_cal"]["x_um"] == 6.0


def test_failures_are_logged(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    w.extract()
    assert "click at least one spot" in log()
    w.set_image(np.zeros((100, 100), np.float32))
    w._on_click(50.0, 50.0, False)

    # Wavefront never takes the script: timeout, then the stale test.js blocks the next request
    w.config.wfd_timeout_s = -1
    w.generate()
    w.poll()
    assert "auto-load" in log() and w.btn_generate.isEnabled()
    w.generate()
    assert "cannot send Pattern1" in log() and w.btn_generate.isEnabled()

    # no NI-DAQ: the error is logged and the shutter stays closed
    def no_daq(device, ao):
        raise ShutterError("nidaqmx is not installed")
    monkeypatch.setattr("oriole.shutter._new_task", no_daq)
    w.btn_open.click()
    assert not w.shutter.is_open and "nidaqmx is not installed" in log()
