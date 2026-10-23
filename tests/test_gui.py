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
    cfg = Config(wfd_root=str(tmp_path / "wfd"))
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

    # --- no image yet: the camera target placeholder (expected size) is shown, clicks only warn
    assert w.image is None and w.view.image_item.image.shape == (512, 672, 3)

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

    # --- holo: masks already in the phase mask folder turn Generate yellow and block it
    out_dir, script_dir = Path(w.config.wfd_output_dir), Path(w.config.wfd_script_dir)
    old = out_dir / "Pattern7_000.algoPhp.png"
    old.write_bytes(b"old")
    t = datetime.datetime(2026, 10, 20, 9).timestamp()
    os.utime(old, (t, t))
    w.refresh_masks()                                   # the folder watcher does this in the application
    assert "#FFD700" in w.btn_generate.styleSheet() and w.combo_mask.currentText() == "7"
    w.generate()
    assert "phase mask folder is not empty" in log() and "2026 10 20" in log()
    assert old.exists() and not (script_dir / "test.js").exists()
    old.unlink()
    w.refresh_masks()
    assert "#00FF00" in w.btn_generate.styleSheet() and w.combo_mask.count() == 0

    # --- a script left behind by Wavefront is purged first, then one mask per spot
    (script_dir / "test.js").write_text("stale")
    w.generate()
    assert "script folder purged (test.js" in log() and not w.btn_generate.isEnabled()
    js1 = _simulate_wavefront(w)
    assert 'setProfileName("Pattern1");' in js1 and "drawSpot(19.4000,5.4000,10.0000);" in js1
    js2 = _simulate_wavefront(w)
    assert 'setProfileName("Pattern2");' in js2 and "saveProfile(0);" in js2
    assert w.btn_generate.isEnabled() and "2 phase masks saved" in log()
    rec = json.loads((out_dir / "Pattern_spots.json").read_text())
    assert [m["mask"] for m in rec["masks"]] == ["Pattern1_000.algoPhp.png", "Pattern2_000.algoPhp.png"]
    assert [w.combo_mask.itemText(i) for i in range(w.combo_mask.count())] == ["1", "2"]
    assert "#FFD700" in w.btn_generate.styleSheet()     # the folder now holds masks

    # --- manual: pick a mask number and load it; a warning if Wavefront does not take the script
    w.combo_mask.setCurrentText("2")
    w.btn_load_mask.click()
    escaped = str(out_dir / "Pattern2_000.algoPhp.png").replace("\\", "\\\\")
    assert (script_dir / "test.js").read_text().endswith(f'loadProfile("{escaped}", 2);\n')
    monkeypatch.setattr("oriole.holo.NOT_READ_S", 0)
    w.check_load_taken()
    assert "Tools → Script engine → auto load (script folder)" in log()

    # --- brooms: the script folder at once, the mask folder after a yes
    w.btn_sweep_scripts.click()
    assert "script folder swept clean" in log() and not any(script_dir.iterdir())
    w.btn_sweep_scripts.click()
    assert "script folder already clean" in log()
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No)
    w.btn_sweep_masks.click()
    assert w.combo_mask.count() == 2
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    w.btn_sweep_masks.click()
    assert not any(out_dir.iterdir()) and w.combo_mask.count() == 0 and "#00FF00" in w.btn_generate.styleSheet()
    assert "phase mask folder swept clean (3 files)" in log()
    w.btn_open.click()
    assert w.shutter.is_open and w.daq_log == ["new", 10.0] and "OPEN" in w.lbl_shutter.text()
    w.btn_close.click()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"] and "closed" in w.lbl_shutter.text()
    w.btn_open.click()
    w.close()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"]
    assert json.loads((tmp_path / "cfg.json").read_text())["holo_cal"]["x_um"] == 6.0


def test_wrong_image_size_warns(window):
    w = window
    w.set_image(np.zeros((672, 512), np.float32))          # rotated: 512 wide, 672 high
    assert not w.lbl_image_warning.isHidden() and "512 × 672" in w.lbl_image_warning.text()
    assert "WRONG IMAGE SIZE: 512 x 672 px instead of 672 x 512" in w.log_view.toPlainText()
    w.set_image(np.zeros((512, 672), np.float32))          # 672 wide, 512 high: fine
    assert w.lbl_image_warning.isHidden()


def test_settings_boxes_apply_and_save(window, tmp_path):
    w = window
    assert not [b for b in w.findChildren(QtWidgets.QPushButton) if "Settings" in b.text()]
    w.spin_rows.setValue(768)
    w.spin_cols.setValue(1024)
    w.spin_dmd_px.setValue(2.0)
    assert w.config.dmd_shape == (768, 1024) and w.config.pixel_size_um == 2.0
    other = tmp_path / "wfdIV_other"
    (other / "script").mkdir(parents=True)
    w.edit_wfd_root._set(str(other))
    assert w.config.wfd_script_dir == str(other / "script") == w.lbl_script_dir.text()
    assert w.config.wfd_output_dir == str(other / "GeneratedPhasemasks") == w.lbl_output_dir.text()
    assert "folder not found: " + str(other / "GeneratedPhasemasks") in w.log_view.toPlainText()
    w.edit_device.setText("Dev2")
    w.edit_device.editingFinished.emit()
    w.spin_ao.setValue(1)
    assert w.shutter.channel == "Dev2/ao1"
    saved = json.loads((tmp_path / "cfg.json").read_text())
    assert (saved["dmd_width"], saved["shutter_ao"], saved["wfd_root"]) == (1024, 1, str(other))


def test_failures_are_logged(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    w.extract()
    assert "click at least one spot" in log()
    w.set_image(np.zeros((100, 100), np.float32))
    w._on_click(50.0, 50.0, False)

    # Wavefront never takes the script: a warning after NOT_READ_S, an error at the timeout
    monkeypatch.setattr("oriole.holo.NOT_READ_S", -1)
    w.generate()
    w.poll()
    assert "not being used by Wavefront" in log() and "auto load (script folder)" in log()
    assert not w.btn_generate.isEnabled()
    monkeypatch.setattr("oriole.holo.TIMEOUT_S", -1)
    w.poll()
    assert "did not take the script" in log() and w.btn_generate.isEnabled()
    # the next Generate purges the script left behind and starts again
    w.generate()
    assert "script folder purged (test.js, zdone.js" in log() and not w.btn_generate.isEnabled()

    # no NI-DAQ: the error is logged and the shutter stays closed
    def no_daq(device, ao):
        raise ShutterError("nidaqmx is not installed")
    monkeypatch.setattr("oriole.shutter._new_task", no_daq)
    w.btn_open.click()
    assert not w.shutter.is_open and "nidaqmx is not installed" in log()
