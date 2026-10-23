import datetime
import os
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pyqtgraph")
from PyQt6 import QtWidgets, QtCore  # noqa: E402

from oriole.binfile import BinFile, read_header  # noqa: E402
from oriole.calibration import apply_affine  # noqa: E402
from oriole.config import Config  # noqa: E402
from oriole.geometry import Circle, Polygon  # noqa: E402
from oriole.gui.app import MainWindow  # noqa: E402
from oriole.gui.hardware import ShutterPanel  # noqa: E402
from oriole.patterns import load_masks  # noqa: E402
from oriole.shutter import ShutterError  # noqa: E402
from oriole.wavefront import WavefrontBusy  # noqa: E402
from tests.conftest import EXISTING_BIN, synthetic_camera_image  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    # keep autosave / config / Wavefront folders out of the user's home and the rig
    monkeypatch.setattr("oriole.gui.select_tab.AUTOSAVE_PATH", tmp_path / "autosave.json")
    cfg = Config(wfd_script_dir=str(tmp_path / "wfd" / "script"),
                 wfd_output_dir=str(tmp_path / "wfd" / "GeneratedPhasemasks"))
    for d in (cfg.wfd_script_dir, cfg.wfd_output_dir):
        os.makedirs(d)
    w = MainWindow(cfg, config_path=tmp_path / "cfg.json")
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    w.show()
    yield w
    w.close()




def test_full_workflow(window, tmp_path, template_matrix, monkeypatch):
    img, truth = synthetic_camera_image(template_matrix, shape=(1000, 1200))
    import tifffile
    tif = tmp_path / "target.tif"
    tifffile.imwrite(str(tif), img.astype(np.uint16))

    # --- calibration tab: load image, place the template roughly, refine, save
    sel = window.select_tab
    assert window.tabs.currentIndex() == 0 and "No calibration" in sel.lbl_cal.text()
    cal_tab = window.calibrate_tab
    cal_tab.load_image(str(tif))
    assert cal_tab.roi.isVisible() and cal_tab.spin_cx.value() == pytest.approx(600)   # default: image centre
    cal_tab.spin_cx.setValue(truth["center_camera"][0] + 8)
    cal_tab.spin_cy.setValue(truth["center_camera"][1] - 6)
    cal_tab.spin_scale.setValue(truth["scale"] * 1.03)
    cal_tab.spin_angle.setValue(truth["angle_deg"] + 1.5)
    cal_tab.chk_mirror.setChecked(True)
    roi_p = cal_tab.roi.params()                    # ROI follows the boxes
    assert roi_p["center_camera"][0] == pytest.approx(truth["center_camera"][0] + 8, abs=0.1)
    assert roi_p["mirror"] is True
    cal_tab.refine()
    p = cal_tab.params()
    assert abs(p["angle_deg"] - truth["angle_deg"]) < 0.15
    assert np.hypot(*(np.subtract(p["center_camera"], truth["center_camera"]))) < 1.0
    # moving the ROI updates the boxes
    cal_tab.roi.setPos([cal_tab.roi.pos()[0] + 3, cal_tab.roi.pos()[1]], finish=True)
    assert cal_tab.spin_cx.value() == pytest.approx(p["center_camera"][0] + 3, abs=0.1)
    cal_tab.roi.setPos([cal_tab.roi.pos()[0] - 3, cal_tab.roi.pos()[1]], finish=True)

    cal_path = tmp_path / "cal.json"
    cal_tab.save_calibration(cal_tab.current_fit(), cal_path)
    assert window.config.last_calibration == str(cal_path)
    assert sel.calibration is not None and sel.calibration.path == str(cal_path)
    assert "Last calibration" in sel.lbl_cal.text() and "today" in sel.lbl_cal.text()
    corners = [(0, 0), (672, 0), (672, 672), (0, 672)]
    err = np.linalg.norm(sel.calibration.dmd_to_camera(corners) -
                         apply_affine(np.linalg.inv(template_matrix), corners), axis=1)
    assert err.max() < 1.5
    # link in the label opens the calibration tab
    sel.lbl_cal.linkActivated.emit("#calibrate")
    assert window.tabs.currentWidget() is cal_tab
    window.tabs.setCurrentIndex(0)
    # reload from file
    cal_tab.load_calibration(str(cal_path))
    assert cal_tab.calibration.fit["mirror"] is True

    # --- select: load a microscope image, drop circles, draw a freehand outline
    cells = tmp_path / "cells.tif"
    tifffile.imwrite(str(cells), np.full((480, 640), 100, np.uint16))
    sel.load_image(str(cells))
    sel.set_mode("circle")
    sel.spin_diam.setValue(20.0)                   # um
    sel.canvas.vb.clicked.emit(300.0, 240.0)
    sel.canvas.vb.clicked.emit(350.0, 200.0)
    assert len(sel.pattern.shapes) == 2
    c0 = sel.pattern.shapes[0]
    assert isinstance(c0, Circle)
    assert c0.r == pytest.approx(sel.calibration.um_to_camera_px(10.0))
    sel.set_mode("freehand")
    t = np.linspace(0, 2 * np.pi, 60, endpoint=False)
    stroke = [(250 + 15 * np.cos(a), 300 + 15 * np.sin(a)) for a in t]
    sel.canvas.vb.strokeFinished.emit(stroke)
    assert len(sel.pattern.shapes) == 3 and isinstance(sel.pattern.shapes[2], Polygon)
    assert [s.name for s in sel.pattern.shapes] == ["S1", "S2", "S3"]
    assert sel.list.count() == 3

    # undo removes the freehand, redo it by re-emitting
    sel.undo()
    assert len(sel.pattern.shapes) == 2
    sel.canvas.vb.strokeFinished.emit(stroke)
    assert len(sel.pattern.shapes) == 3

    # rename + disable through the list
    li = sel.list.item(1)
    li.setText("cellA")
    li.setCheckState(QtCore.Qt.CheckState.Unchecked)
    assert sel.pattern.shapes[1].name == "cellA" and not sel.pattern.shapes[1].enabled

    # move a circle ROI and check the shape follows
    item0 = sel.items[0]
    item0.roi.setPos([item0.roi.pos()[0] + 5, item0.roi.pos()[1]], finish=True)
    assert sel.pattern.shapes[0].cx == pytest.approx(305.0)

    # preview reflects the combined mask, autosave written
    mask = sel.pattern.combined_mask()
    assert mask.sum() > 0
    assert (tmp_path / "autosave.json").exists()

    # --- export (dialog replaced by a fixed directory)
    sel.edit_name.setText("test set")
    sel.chk_combined.setChecked(True)
    sel.chk_per_shape.setChecked(True)
    out_dir = tmp_path / "export"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)))
    sel.export_dialog()
    npz = out_dir / "test_set.patterns.npz"
    names, masks, meta = load_masks(npz)
    assert names == ["test_set_all", "test_set_S1", "test_set_S3"]   # cellA disabled
    np.testing.assert_array_equal(masks[0], masks[1] | masks[2])

    # --- reload the pattern set into a fresh select tab: same masks
    sel.clear_all()
    assert len(sel.pattern.shapes) == 0
    sel.load_pattern(str(out_dir / "test_set.patterns.json"))
    assert [s.name for s in sel.pattern.shapes] == ["S1", "cellA", "S3"]
    np.testing.assert_array_equal(sel.pattern.combined_mask(), masks[0].astype(bool))

    # --- merge tab
    mt = window.merge_tab
    src = tmp_path / "orig.bin"
    shutil.copy(EXISTING_BIN, src)
    mt.edit_bin.setText(str(src))
    mt.add_patterns([str(npz)])
    assert mt.table.rowCount() == 3
    assert mt.table.item(0, 1).text() == "15" and mt.table.item(2, 2).text() == "20"
    assert mt.edit_out.text().endswith("orig_test_set.bin")
    mt.merge()
    out = tmp_path / "orig_test_set.bin"
    assert read_header(out)["nb_images"] == 21
    with BinFile(str(out), rig_id=3, mode="r") as bf:
        np.testing.assert_array_equal(bf.read_frame_uint8(15), masks[0] * 255)
        np.testing.assert_array_equal(bf.read_frame_uint8(16), (1 - masks[0]) * 255)
    with open(str(out) + ".indices.json") as f:
        assert json.load(f)["frames"]["test_set_S3"] == {
            "pos_idx": 19, "neg_idx": 20, "n_pixels": int(masks[2].sum()), "pattern_file": str(npz)}

    # the export also wrote the small dated bin (3 patterns -> 6 frames); merging it gives the same bin
    small = next(out_dir.glob("*_3Frames.bin"))
    assert read_header(small)["nb_images"] == 6
    mt.list.clear()
    mt.edit_out.clear()
    mt.add_patterns([str(small)])
    assert mt.table.item(2, 0).text() == "test_set_S3" and mt.table.item(2, 2).text() == "20"
    assert mt.edit_out.text().endswith(f"orig_{small.stem}.bin")
    mt.merge()
    assert (tmp_path / f"orig_{small.stem}.bin").read_bytes() == out.read_bytes()


def test_identity_calibration_and_settings(window, tmp_path):
    window.calibrate_tab.use_identity()
    sel = window.select_tab
    assert sel.calibration is not None and sel.calibration.scale_dmd_per_camera == 1.0
    assert "identity" in sel.lbl_cal.text()
    sel.load_image.__func__  # exists
    img = np.zeros((672, 672), np.float32)
    sel.channels, sel.channel_names = [img], ["image"]
    sel.canvas.set_image(img)
    sel._refresh_warp()
    sel.set_mode("circle")
    sel.spin_diam.setValue(16.0)
    sel.canvas.vb.clicked.emit(100.0, 100.0)
    m = sel.pattern.combined_mask()
    assert m[100, 100] and abs(m.sum() - np.pi * (8 / 2.5) ** 2) < 10


def test_stale_calibration_warning(window, tmp_path):
    from oriole.calibration import Calibration
    cal = Calibration.identity(window.config)
    cal.method = "template"
    cal.created = "2026-01-01T09:00:00"
    p = cal.save(tmp_path / "old.json")
    window.calibrate_tab.load_calibration(str(p))
    txt = window.select_tab.lbl_cal.text()
    assert "2026-01-01 09:00" in txt and "consider recalibrating" in txt
    window.config.calibration_max_age_days = 3650
    window.select_tab.set_config(window.config)
    assert "consider recalibrating" not in window.select_tab.lbl_cal.text()


# ----------------------------------------------------------------------------- holo mode
class FakeTask:
    def __init__(self, log):
        self.log = log
        log.append("new")

    def write(self, v, auto_start=True):
        self.log.append(v)

    def close(self):
        self.log.append("close")


@pytest.fixture
def daq_log(monkeypatch):
    log = []
    monkeypatch.setattr("oriole.shutter._new_task", lambda device, ao: FakeTask(log))
    return log


def _simulate_wavefront(runner, out_dir: Path) -> str:
    """What Wavefront IV does with a dropped script: run it (saving the mask if asked), remove it."""
    script = Path(runner.config.wfd_script_dir) / "test.js"
    js = script.read_text()
    m = re.search(r'setProfileName\("(\w+)"\);', js)
    if m and "saveProfile" in js:
        (out_dir / f"{m.group(1)}_000.algoPhp.png").write_bytes(b"png")
    script.unlink()
    (script.parent / "zdone.js").unlink()
    runner.poll()
    return js


def test_holo_workflow(window, tmp_path, monkeypatch, daq_log):
    sel, cfg, hp = window.select_tab, window.config, window.select_tab.holo_panel
    script_dir, out_dir = Path(cfg.wfd_script_dir), Path(cfg.wfd_output_dir)

    # --- mode switch: holo tabs, freehand off, the calibration link opens the holo calibration
    window.mode_actions["holo"].trigger()
    assert cfg.mode == "holo" and sel.stim_mode == "holo" and sel.stack.currentWidget() is hp
    visible = lambda w: window.tabs.isTabVisible(window.tabs.indexOf(w))  # noqa: E731
    assert visible(window.slm_tab) and visible(window.holo_calibrate_tab)
    assert not visible(window.merge_tab) and not visible(window.calibrate_tab)
    assert not sel.act_free.isEnabled() and not sel.spin_margin.isEnabled()
    sel.lbl_cal.linkActivated.emit("#calibrate")
    assert window.tabs.currentWidget() is window.holo_calibrate_tab
    window.tabs.setCurrentIndex(0)
    assert sel.holo_calibration.pixel_size_um == 0.67 and "unsaved" in sel.lbl_cal.text()   # DHparams.m defaults

    # --- spots on a 100 x 100 image: Wavefront coordinates as DHgui computes them
    sel.set_channels([np.zeros((100, 100), np.float32)], ["image"], "cells.tif")
    sel.spin_diam.setValue(10.0)
    sel.canvas.vb.clicked.emit(69.5, 29.5)
    sel.canvas.vb.clicked.emit(30.0, 50.0)
    sel._add_shape(Polygon([[5, 5], [20, 5], [10, 20]]))
    assert sel.pattern.shapes[0].r == pytest.approx(5 / 0.67)
    assert hp.table.rowCount() == 2
    assert [hp.table.item(0, c).text() for c in range(4)] == ["S1", "18.40", "5.40", "10.00"]
    assert "S3" in hp.lbl_ignored.text() and "Pattern1 … Pattern2" in hp.lbl_target.text()
    x, y = sel.canvas.origin_item.getData()             # cross where Wavefront (0, 0) lands
    np.testing.assert_allclose(sel.holo_calibration.camera_to_slm([(x[0], y[0])], (100, 100)), [[0, 0]], atol=1e-9)
    window.holo_calibrate_tab.spin_dx.setValue(6.0)     # edits apply live
    assert hp.table.item(0, 1).text() == "19.40"

    # --- live send
    hp.send_live()
    assert "drawSpot(19.4000,5.4000,10.0000);" in (script_dir / "test.js").read_text()
    assert window.runner.busy and not hp.btn_live.isEnabled()
    js = _simulate_wavefront(window.runner, out_dir)
    assert "saveProfile" not in js and not window.runner.busy and hp.btn_live.isEnabled()
    assert "live pattern (2 spots)" in window.slm_tab.lbl_slm.text()

    # --- generate: the old masks are reported with their files, deleted, then one mask per spot
    old = out_dir / "Pattern7_000.algoPhp.png"
    old.write_bytes(b"old")
    t = datetime.datetime(2026, 10, 20, 9).timestamp()
    os.utime(old, (t, t))
    asked = []
    monkeypatch.setattr(hp, "_ask_old_masks", lambda files, d: asked.append([p.name for p in files]) or "delete")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path / "rec")))
    hp.edit_name.setText("cells")
    hp.generate_dialog()
    assert asked == [["Pattern7_000.algoPhp.png"]] and not old.exists()
    assert not hp.progress.isHidden() and not hp.btn_cancel.isHidden()
    assert 'setProfileName("Pattern1");' in _simulate_wavefront(window.runner, out_dir)
    assert window.runner.busy and hp.progress.value() == 1
    js2 = _simulate_wavefront(window.runner, out_dir)
    assert 'setProfileName("Pattern2");' in js2 and "drawSpot(" in js2 and "saveProfile(0);" in js2
    assert not window.runner.busy and hp.progress.isHidden()
    rec = json.load(open(tmp_path / "rec" / "cells.holo.json"))
    assert [(m["profile"], m["spot"], m["generated"]) for m in rec["masks"]] == [("Pattern1", "S1", True),
                                                                                ("Pattern2", "S2", True)]
    assert rec["spots"][0]["x_um"] == pytest.approx(19.4) and rec["calibration"]["delta_x_um"] == 6.0

    # --- phase masks tab: list, load one onto the SLM
    st = window.slm_tab
    st.refresh()
    assert [st.tree.topLevelItem(i).text(0) for i in range(st.tree.topLevelItemCount())] == \
        ["Pattern1_000.algoPhp.png", "Pattern2_000.algoPhp.png"]
    st.tree.setCurrentItem(st.tree.topLevelItem(1))
    st.load_selected()
    escaped = str(out_dir / "Pattern2_000.algoPhp.png").replace("\\", "\\\\")
    assert (script_dir / "test.js").read_text().endswith(f'loadProfile("{escaped}", 2);\n')
    _simulate_wavefront(window.runner, out_dir)
    assert "Pattern2_000.algoPhp.png" in st.lbl_slm.text()

    # --- shutter: both panels follow the shared controller; closing the window closes it
    hp_shutter, st_shutter = hp.findChild(ShutterPanel), st.findChild(ShutterPanel)
    hp_shutter.btn_open.click()
    assert window.shutter.is_open and daq_log == ["new", 10.0] and "OPEN" in st_shutter.lbl_state.text()
    st_shutter.btn_close.click()
    assert not window.shutter.is_open and daq_log[-2:] == [0.0, "close"] and "closed" in hp_shutter.lbl_state.text()
    hp_shutter.btn_open.click()
    window.close()
    assert not window.shutter.is_open and daq_log[-2:] == [0.0, "close"]

    # --- back to visual: freehand available again, merge tab back
    window.set_mode("visual")
    assert sel.act_free.isEnabled() and visible(window.merge_tab) and not visible(window.slm_tab)


def test_wavefront_and_shutter_failures(window, tmp_path, monkeypatch):
    runner, sel = window.runner, window.select_tab
    hp = sel.holo_panel
    window.set_mode("holo")
    sel.set_channels([np.zeros((100, 100), np.float32)], ["image"], "cells.tif")
    sel.canvas.vb.clicked.emit(50.0, 50.0)

    # Wavefront never takes the script: timeout, and the stale test.js blocks the next request
    window.config.wfd_timeout_s = -1
    hp.send_live()
    with pytest.raises(RuntimeError, match="busy"):
        runner.run([("x", "", None)])
    runner.poll()
    assert not runner.busy and hp.btn_live.isEnabled()
    with pytest.raises(WavefrontBusy):
        runner.run([("x", "", None)])

    # a mask that never appears stops the batch; the record says which masks exist
    (Path(window.config.wfd_script_dir) / "test.js").unlink()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path / "rec")))
    hp.generate_dialog()
    (Path(window.config.wfd_script_dir) / "test.js").unlink()     # taken, but no png written
    runner.poll()
    assert not runner.busy
    rec = json.load(open(tmp_path / "rec" / "holo.holo.json"))
    assert [m["generated"] for m in rec["masks"]] == [False]

    # no NI-DAQ: the error is reported and the shutter stays closed
    def no_daq(device, ao):
        raise ShutterError("nidaqmx is not installed")
    monkeypatch.setattr("oriole.shutter._new_task", no_daq)
    hp.findChild(ShutterPanel).btn_open.click()
    assert not window.shutter.is_open and "nidaqmx" in window.statusBar().currentMessage()
