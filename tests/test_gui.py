import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pg = pytest.importorskip("pyqtgraph")
from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

from oriole.binfile import read_header  # noqa: E402
from oriole.camera import CameraError  # noqa: E402
from oriole.config import Config  # noqa: E402
from oriole.gui import MainWindow, apply_dark_theme  # noqa: E402
from oriole import holo, tcplink, wavefront  # noqa: E402
from oriole.shutter import ShutterError  # noqa: E402
from oriole.tcplink import LinkError  # noqa: E402
from tests.conftest import EXISTING_BIN  # noqa: E402
from tests.test_camera import wait_for  # noqa: E402


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


class FakeLink:
    """Wavefront IV's TCP server as Oriole sees it (tcplink.Wavefront4Link): the scripts sent, in order (unwrapped),
    and their ids. A ping or a Load answers at once while ``runs`` (Tcp server ticked); a mask's script answers once the
    test has Wavefront save its mask (_simulate_wavefront: answer()). ``down``: not reachable."""
    PORT = 59454

    def __init__(self, port_file=None):
        self.sent, self.sids, self.queue = [], [], []
        self.down, self.runs, self.port, self.connections, self.next_id = False, True, None, 0, 100

    def script(self, js: str) -> tuple[int, str]:
        self.next_id += 1
        return self.next_id, tcplink.wrap(js, self.next_id)

    def send(self, js: str) -> int:
        if self.down:
            raise LinkError(f"Wavefront IV not reachable over TCP (port {self.PORT}): refused")
        if self.port is None:
            self.port, self.connections = self.PORT, self.connections + 1
        sid, _ = self.script(js)
        self.sent.append(js)
        self.sids.append(sid)
        if self.runs and "saveProfile" not in js:
            self.answer(sid)
        return sid

    def answer(self, sid: int | None = None, error: str = ""):
        """Wavefront ran script ``sid`` (the last one sent): DONE, or ERROR with ``error``."""
        if self.runs:
            self.queue.append(("ERROR" if error else "DONE", self.sids[-1] if sid is None else sid, error))

    def answers(self):
        out, self.queue = self.queue, []
        return out

    def close(self):
        self.port = None


@pytest.fixture(autouse=True)
def fake_link(monkeypatch, tmp_path):
    """No test reaches the Wavefront of this PC, nor writes in the journal of this PC."""
    monkeypatch.setattr("oriole.tcplink.Wavefront4Link", FakeLink)
    monkeypatch.setattr("oriole.gui.JOURNAL_PATH", tmp_path / "journal.log")


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    daq_log = []
    monkeypatch.setattr("oriole.shutter._new_task", lambda device, ao: FakeTask(daq_log))
    asked, answers = [], {}                             # the dialogs, answered by title: w.answers[title] = key

    def choose(self, title, text, choices):
        if title not in answers:                        # a dialog no test answers would hang: fail instead
            pytest.fail(f"dialog: {title}")
        asked.append((title, text, choices))
        return answers[title]
    monkeypatch.setattr(MainWindow, "_choose", choose)
    monkeypatch.setattr("oriole.wavefront.SUPPORTED", False)       # the Wavefront of this PC is never touched
    cfg = Config(wfd_root=str(tmp_path / "wfd"), start_wavefront=False)
    os.makedirs(cfg.wfd_output_dir)
    (tmp_path / "wfd" / "wfdIV.exe").write_bytes(b"")   # a Wavefront install: its folders may be swept
    experiment = tmp_path / "experiment"                # the session's phase mask folder is made in it
    experiment.mkdir()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(experiment)))
    monkeypatch.setattr("oriole.holo.STORE_SETTLE_S", 0)                 # masks stored as soon as the last is saved
    w = MainWindow(cfg, config_path=tmp_path / "cfg.json")
    w.set_mask_folder(experiment)                       # as chosen at the first Generate
    w.daq_log, w.asked, w.answers = daq_log, asked, answers
    w.show()
    yield w
    w.close()


def _raw_src(path, x_um, y_um, diameter_um, um_px=0.652, size=576):
    """Wavefront's rawSrc.png: the drawn spot, white on black, (0, 0) µm at the centre, y up."""
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy = size / 2 + x_um / um_px - 0.5, size / 2 - y_um / um_px - 0.5
    imageio.imwrite(path, ((np.hypot(xx - cx, yy - cy) <= diameter_um / um_px / 2) * 255).astype(np.uint8))


def _simulate_wavefront(w, drawn_at=None, out=None, save=True) -> str:
    """What Wavefront IV does with the last script sent over TCP: run it (saving the mask and the spot it drew if
    asked, in ``out``, the folder it started with, by default the Wavefront folder's; the spot elsewhere with
    ``drawn_at``; nothing saved without ``save``), then answer DONE."""
    js = w.link.sent[-1]
    m = re.search(r'setProfileName\("(\w+)"\);', js)
    if m and "saveProfile" in js and save:
        out = Path(out or w.config.wfd_output_dir)
        x, y, d = map(float, re.search(r"drawSpot\(([-\d.]+),([-\d.]+),([-\d.]+)\);", js).groups())
        _raw_src(out / f"{m.group(1)}_000.rawSrc.png", *(drawn_at or (x, y)), d)
        (out / f"{m.group(1)}_000.algoPhp.png").write_bytes(b"png")
    w.link.answer()
    w.poll()
    return js


def _generate(w):
    w.generate()
    assert w.link.sent[-1].startswith(holo.EMPTY_LIST + "\n")


def _load(w):
    """Load the mask picked, Wavefront answering at once."""
    w.btn_load_mask.click()
    w.poll()


def test_end_to_end(window, tmp_path, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    assert not w.findChildren(pg.HistogramLUTWidget)                   # no power / contrast scale
    assert not [b for box in (w.visual_box, w.holo_box) for b in box.findChildren(QtWidgets.QPushButton)
                if b.text() == "Reset"]                                 # calibrations: typed by hand only
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

    # --- holo: a mask in a phase mask folder turns Generate yellow; Generate then warns, sweeps only on a yes
    out_dir, masks = Path(w.config.wfd_output_dir), w.mask_dir
    assert masks == tmp_path / "experiment" / "Phasemasks" and masks.is_dir() and w.lbl_output_dir.text() == str(masks)
    old = masks / "Pattern7_000.algoPhp.png"
    old.write_bytes(b"old")
    t = datetime.datetime(2026, 10, 20, 9).timestamp()
    os.utime(old, (t, t))
    w.refresh_masks()                                   # the folder watcher does this in the application
    assert "#FFD700" in w.btn_generate.styleSheet() and w.combo_mask.currentText() == "7"
    w.answers["Phase mask folders not empty"] = None    # Cancel
    w.generate()
    assert "phase mask folder of this session: 1 file (1 mask), dated 2026 10 20" in w.asked[-1][1]
    assert "not generating: the phase mask folders must be empty" in log() and old.exists() and not w.link.sent
    (out_dir / "Pattern4_000.algoPhp.png").write_bytes(b"left")     # a mask left in Wavefront's own folder
    w.answers["Phase mask folders not empty"] = "sweep"
    w.generate()
    assert "• Wavefront's phase mask folder: 1 file (1 mask), dated " in w.asked[-1][1]
    assert "phase mask folders swept (2 files)" in log() and not old.exists()

    # --- Wavefront first empties its pattern list, then one mask per spot, each checked against its drawn spot
    assert w.btn_generate.text() == "■ STOP" and "#FF6060" in w.btn_generate.styleSheet()
    assert not w.btn_load_mask.isEnabled()
    js1 = _simulate_wavefront(w)
    assert js1.startswith("for (var id = 0; id <= 500; id++) { deleteProfile(id); }\ncleanAllBlob();\n")
    assert 'setProfileName("Pattern1");' in js1 and "drawSpot(19.4000,5.4000,10.0000);" in js1
    assert w.btn_generate.text() == "■ STOP"          # the folder watcher's refresh keeps STOP
    js2 = _simulate_wavefront(w)
    assert 'setProfileName("Pattern2");' in js2 and "saveProfile(0);" in js2 and len(w.link.sent) == 2
    assert w.btn_generate.text() == "GENERATE PHASE MASKS" and f"2 phase masks saved in {masks}" in log()
    assert w.wf_port == FakeLink.PORT                   # the port the scripts went to
    assert not holo.oriole_files(out_dir)               # moved from Wavefront's folder into the session's
    assert [p.name for p in holo.oriole_files(masks)] == ["Pattern1_000.algoPhp.png", "Pattern1_000.rawSrc.png",
                                                          "Pattern2_000.algoPhp.png", "Pattern2_000.rawSrc.png",
                                                          "Pattern_spots.json"]
    rec = json.loads((masks / "Pattern_spots.json").read_text())
    assert [m["mask"] for m in rec["masks"]] == ["Pattern1_000.algoPhp.png", "Pattern2_000.algoPhp.png"]
    assert [w.combo_mask.itemText(i) for i in range(w.combo_mask.count())] == ["1", "2"]
    assert "#FFD700" in w.btn_generate.styleSheet()     # the folder now holds masks

    # --- manual: pick a mask number and load it, over TCP: loaded once Wavefront answered; not reachable: not loaded
    w.combo_mask.setCurrentText("2")
    w.btn_load_mask.click()
    escaped = str(masks / "Pattern2_000.algoPhp.png").replace("\\", "\\\\")
    assert w.link.sent[-1] == f'{holo.EMPTY_LIST}\ncleanAllBlob();\nloadProfile("{escaped}", 2);\n'
    assert log().splitlines()[-1].endswith("SLM: loading Pattern2") and w.slm_spot is None and w.script_running
    w.poll()                                            # its answer
    assert log().splitlines()[-1].endswith("SLM: Pattern2 loaded") and w.slm_spot is not None and not w.jobs
    w.link.down = True
    w.btn_load_mask.click()
    assert f"Pattern2 not loaded: Wavefront IV not reachable over TCP (port {FakeLink.PORT})" in log()
    assert w.slm_spot is None and w.wf_port is False and len(w.link.sent) == 3
    w.link.down = False

    # --- the mask folder's broom, after a yes
    w.answers["Sweep the phase mask folder"] = None
    w.btn_sweep_masks.click()
    assert w.combo_mask.count() == 2 and "Delete the 5 files (2 masks, dated " in w.asked[-1][1]
    w.answers["Sweep the phase mask folder"] = "delete"
    w.btn_sweep_masks.click()
    assert not any(masks.iterdir()) and w.combo_mask.count() == 0 and "#00FF00" in w.btn_generate.styleSheet()
    assert "phase mask folder swept clean (5 files)" in log()           # 2 masks, their 2 drawn spots, the record
    w.btn_open.click()
    assert w.shutter.is_open and w.daq_log == ["new", 10.0] and "OPEN" in w.lbl_shutter.text()
    w.btn_close.click()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"] and "closed" in w.lbl_shutter.text()
    w.btn_open.click()
    w.close()
    assert not w.shutter.is_open and w.daq_log[-2:] == [0.0, "close"]
    assert json.loads((tmp_path / "cfg.json").read_text())["holo_cal"]["x_um"] == 6.0


def test_fixed_view_drag_spots_and_help(window):
    w = window
    assert w.view.vb.state["mouseEnabled"] == [False, False]           # no pan, no zoom
    w.set_image(np.zeros((512, 672), np.float32))
    w._on_click(100.0, 100.0, False)
    w._on_click(300.0, 200.0, False)
    w._on_drag(101.0, 99.0, 0)                                          # press on spot 1, drag it
    w._on_drag(150.0, 120.0, 1)
    w._on_drag(160.0, 130.0, 2)
    assert w.spots == [(160.0, 130.0), (300.0, 200.0)] and w.dragging is None
    w.undo()
    assert w.spots[0] == (100.0, 100.0)
    for phase, (x, y) in enumerate(((500.0, 400.0), (510.0, 410.0), (520.0, 420.0))):
        w._on_drag(x, y, phase)                                         # not on a spot: nothing moves
    assert w.spots == [(100.0, 100.0), (300.0, 200.0)]
    w._on_drag(300.0, 200.0, 0)
    w._on_drag(-50.0, 900.0, 2)                                         # kept inside the image
    assert w.spots[1][0] == 0.0 and w.spots[1][1] < 512
    w.btn_help.click()
    help_page = w.dialogs["Oriole help"]
    assert help_page.isVisible() and "Pattern1" in help_page.findChild(QtWidgets.QTextBrowser).toPlainText()

    # the ? next to the Wavefront folder: how to install Wavefront again, today's folder name in it
    w.btn_reinstall_help.click()
    page = w.dialogs["Installing Wavefront IV again"].findChild(QtWidgets.QTextBrowser).toPlainText()
    new = Path(w.config.wfd_root).parent / f"wfdIV_{datetime.date.today():%Y%m%d}"
    for step in ("Extract the zip", "Make the folders script and GeneratedPhasemasks", "double-click log.bat",
                 "press Ctrl, A and R together", "pick the new folder as the Wavefront folder", str(new)):
        assert step in page
    w.show_help()                                       # each page in its own window
    assert len(w.dialogs) == 2


def test_unanswered_scripts_are_sent_again_then_abort(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    last = lambda: log().splitlines()[-1]                           # noqa: E731
    out_dir = Path(w.config.wfd_output_dir)
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0)):
        w._on_click(x, y, False)
    _generate(w)
    monkeypatch.setattr("oriole.holo.MASK_WAIT_S", -1)
    w.poll()                                                        # no answer in time
    assert "Wavefront did not run the Pattern1 script within -1 s (attempt 1/3): sending it again" in log()
    assert len(w.link.sent) == 2 and w.link.sent[1] == w.link.sent[0]
    assert w.link.sids[1] != w.link.sids[0]                         # the same mask, a new script: Wavefront runs it
    monkeypatch.setattr("oriole.holo.MASK_WAIT_S", 10.0)
    w.link.answer(w.link.sids[0])                                   # the first send answers late: nothing to do
    w.poll()
    assert w.jobs[0].answered is None and len(w.link.sent) == 2
    _simulate_wavefront(w)                                          # the second one runs
    assert "computing Pattern2 (2 of 2)" in last()                  # attempts start again for the next mask
    monkeypatch.setattr("oriole.holo.MASK_WAIT_S", -1)
    for k in (1, 2):
        w.poll()
        assert f"did not run the Pattern2 script within -1 s (attempt {k}/3): sending it again" in log()
    w.poll()
    assert "Pattern2 script within -1 s (attempt 3/3): generation aborted; cleaning up in 2 s" in log()
    assert len(w.link.sent) == 5 and w.wf_answers is False          # no answer at all: pinged until it runs them
    (out_dir / "Pattern2_000.algoPhp.png").write_bytes(b"png")     # Wavefront was still computing: saved late
    w.cleanup["at"] -= holo.ABORT_WAIT_S                               # 2 s later
    w.poll()
    assert ("cleaned up after the abort: Wavefront's phase mask folder emptied (3 files: Pattern1_000.algoPhp.png, "
            'Pattern1_000.rawSrc.png, Pattern2_000.algoPhp.png); Wavefront IV did not answer: tick "Tools → Script '
            'engine → Tcp server" in Wavefront IV if it is not') in last()
    assert not any(out_dir.iterdir()) and not w.jobs
    assert w.btn_generate.text() == "GENERATE PHASE MASKS" and "#00FF00" in w.btn_generate.styleSheet()

    # it does not answer: Generate says so at once, until a ping is answered
    w.generate()
    assert "Wavefront IV does not run Oriole's scripts: tick" in last() and not w.jobs
    w.ping()
    w.poll()
    assert w.wf_answers is True and w.lbl_wavefront.text().startswith("Wavefront")

    # answered, but its mask not saved (an empty file is not a saved mask): aborted
    monkeypatch.setattr("oriole.holo.SAVE_WAIT_S", -1)
    monkeypatch.setattr("oriole.holo.MASK_WAIT_S", 10.0)
    _generate(w)
    (out_dir / "Pattern1_000.algoPhp.png").write_bytes(b"")
    _simulate_wavefront(w, save=False)
    assert ("Wavefront ran the Pattern1 script but did not save Pattern1_000.algoPhp.png: generation aborted"
            in log()) and not w.jobs
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()

    # a script error: aborted, Wavefront's message said
    _generate(w)
    w.link.answer(error="ReferenceError: Can't find variable: drawSpot")
    w.poll()
    assert ("Wavefront: the Pattern1 script failed (ReferenceError: Can't find variable: drawSpot): generation "
            "aborted") in log() and not w.jobs


def test_stop_waits_for_wavefront_then_sweeps(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    out_dir = Path(w.config.wfd_output_dir)
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0), (400.0, 300.0)):
        w._on_click(x, y, False)
    w.btn_generate.click()
    for _ in range(2):                                              # Pattern1, Pattern2
        _simulate_wavefront(w)
    assert w.btn_generate.text() == "■ STOP" and len(w.link.sent) == 3
    w.btn_generate.click()
    assert "STOP pressed: generation aborted; cleaning up in 2 s, Wavefront may still be computing" in log()
    assert not w.jobs and not w.btn_generate.isEnabled() and not w.btn_load_mask.isEnabled()
    w.poll()
    assert w.btn_generate.text() == "CLEANING UP IN 2 s"
    assert len(list(out_dir.iterdir())) == 4                    # nothing swept yet
    w.generate()
    w.btn_sweep_masks.click()
    assert "still computing the previous masks" in log() and "not now" in log() and not w.jobs
    w.cleanup["at"] -= holo.ABORT_WAIT_S                               # 2 s later
    w.poll()
    assert ("cleaned up after the abort: Wavefront's phase mask folder emptied (4 files: Pattern1_000.algoPhp.png, "
            "Pattern1_000.rawSrc.png, Pattern2_000.algoPhp.png, Pattern2_000.rawSrc.png)") in log().splitlines()[-1]
    assert not any(out_dir.iterdir())
    assert w.btn_generate.isEnabled() and w.btn_generate.text() == "GENERATE PHASE MASKS" and w.combo_mask.count() == 0
    w.poll()                                                        # a late tick of the timer does nothing
    w.btn_generate.click()
    assert w.jobs and "computing Pattern1 (1 of 3)" in log().splitlines()[-1]
    w.close()                                                       # closing sweeps at once
    assert not w.jobs and "cleaned up after the abort" in log().splitlines()[-1]


def test_one_script_at_a_time(window, monkeypatch):
    """A script never reaches Wavefront before the last one answered (one coming while another runs can crash it in
    QtScript4.dll): the next mask once this one's script answered, a Load or a Generate once the last Load did."""
    w = window
    log = w.log_view.toPlainText
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0)):
        w._on_click(x, y, False)
    _generate(w)
    out = Path(w.config.wfd_output_dir)                 # Pattern1 saved, its script not ended yet
    _raw_src(out / "Pattern1_000.rawSrc.png", *w.config.holo.to_um(w.spots[:1], (512, 672))[0], 10.0)
    (out / "Pattern1_000.algoPhp.png").write_bytes(b"png")
    w.poll()
    assert len(w.link.sent) == 1 and w.jobs[0].name == "Pattern1"
    w.link.answer()                                     # it ended: the next one at once
    w.poll()
    assert len(w.link.sent) == 2 and 'setProfileName("Pattern2");' in w.link.sent[-1]
    _simulate_wavefront(w)
    assert "2 phase masks saved" in log()

    # a Load not answered yet: no other Load, no Generate
    w.link.runs = False
    w.combo_mask.setCurrentText("1")
    w.btn_load_mask.click()
    assert len(w.link.sent) == 3 and w.script_running and log().splitlines()[-1].endswith("SLM: loading Pattern1")
    w.btn_load_mask.click()
    w.generate()
    assert len(w.link.sent) == 3 and not w.jobs and log().count("not now: Wavefront is running Oriole's last script") == 2

    # ... and none in time: not loaded (what the SLM shows is unknown), Wavefront does not run the scripts
    w.waiting["sent"] -= tcplink.LOAD_WAIT_S
    w.poll()
    assert "Pattern1 not loaded: Wavefront did not answer within 5 s" in log() and w.slm_spot is None
    assert w.wf_answers is False and not w.script_running


def test_a_generation_keeps_its_folders(window, tmp_path):
    w = window
    log = w.log_view.toPlainText
    out_dir, masks = Path(w.config.wfd_output_dir), w.mask_dir
    other = tmp_path / "wfdIV_other"                    # another install, picked as the Wavefront folder meanwhile
    (other / "GeneratedPhasemasks").mkdir(parents=True)
    (other / "wfdIV.exe").write_bytes(b"")
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0)):
        w._on_click(x, y, False)

    # the Wavefront folder changed during a generation: its masks still moved from the folder Wavefront saves in
    _generate(w)
    _simulate_wavefront(w)
    w.edit_wfd_root._set(str(other))
    _simulate_wavefront(w, out=out_dir)
    assert f"2 phase masks saved in {masks}" in log() and not holo.oriole_files(out_dir)
    assert len(holo.available_masks(masks)) == 2 and (masks / "Pattern_spots.json").exists()

    # ... its masks checked there, its abort sweeping only there
    w.answers["Phase mask folders not empty"] = "sweep"
    _generate(w)                                        # in the other install's folder
    js1 = _simulate_wavefront(w, out=other / "GeneratedPhasemasks")
    w.edit_wfd_root._set(str(tmp_path / "wfd"))
    kept = out_dir / "Pattern9_000.algoPhp.png"         # not this generation's folder: not swept
    kept.write_bytes(b"png")
    x1, y1 = map(float, re.search(r"drawSpot\(([-\d.]+),([-\d.]+),", js1).groups())
    _simulate_wavefront(w, drawn_at=(x1, y1), out=other / "GeneratedPhasemasks")
    assert "Pattern2 holds a spot drawn at" in log()
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()
    assert not any((other / "GeneratedPhasemasks").iterdir()) and kept.exists()


def test_mask_holding_another_spot_aborts(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    out_dir = Path(w.config.wfd_output_dir)
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0), (400.0, 300.0)):
        w._on_click(x, y, False)
    _generate(w)
    js1 = _simulate_wavefront(w)                                    # Pattern1: its own spot
    x1, y1 = map(float, re.search(r"drawSpot\(([-\d.]+),([-\d.]+),", js1).groups())
    _simulate_wavefront(w, drawn_at=(x1, y1))                       # Pattern2 saved with spot 1
    assert re.search(r"Pattern2 holds a spot drawn at \(-15\d\.\d, 9\d\.\d\) µm, not at \(-18\.8, 29\.2\) µm \(spot 2\): "
                     r"generation aborted", log()) and abs(x1 + 151.8) < 1 and abs(y1 - 96.2) < 1
    w.cleanup["at"] -= holo.ABORT_WAIT_S                               # 2 s later
    w.poll()
    assert "restart Wavefront IV and generate again" in log().splitlines()[-1] and not any(out_dir.iterdir())

    # nothing drawn for a spot inside the field: a big red warning, the generation goes on
    _generate(w)
    _simulate_wavefront(w, drawn_at=(1000.0, 1000.0))
    assert re.search(r"⚠ Pattern1 HOLDS NO SPOT: Wavefront drew nothing for spot 1 at \(-15\d\.\d, 9\d\.\d\) µm "
                     r"\(outside its field\?\)", log())
    assert w.jobs and "computing Pattern2 (2 of 3)" in log().splitlines()[-1]


def test_spots_outside_the_slm_field(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    last = lambda: log().splitlines()[-1]                           # noqa: E731
    w.set_image(np.zeros((512, 672), np.float32))
    for x in (336.0, 606.0, 660.0):                                 # in, cut by the field's edge, out
        w._on_click(x, 256.0, False)
    um = w.config.holo.to_um(w.spots, (512, 672))
    assert [holo.field_fit(x, y, 10.0) for x, y in um] == ["in", "partly", "out"]
    off = f"spot 2 partly at ({um[1][0]:.1f}, {um[1][1]:.1f}) µm, spot 3 at ({um[2][0]:.1f}, {um[2][1]:.1f}) µm"
    title = "Spots outside the SLM field"

    # a pop-up lists them; Cancel: nothing sent, nothing swept (asked before the sweep)
    old = w.mask_dir / "Pattern5_000.algoPhp.png"
    old.write_bytes(b"png")
    w.answers[title] = None
    w.generate()
    assert w.asked[-1] == (title, "Outside the SLM field (Wavefront: ±187.8 µm):\n\n• " +
                           off.replace(", spot", "\n• spot") + "\n\nTheir masks would hold no spot, or a cut one. A "
                           "right click on a spot removes it; Remove spots removes them all (Undo brings them back) "
                           "and generates the others.",
                           {"remove": "Remove spots and generate", "anyway": "Generate anyway"})
    assert not w.jobs and not w.link.sent and len(w.spots) == 3 and old.exists()
    assert [a[0] for a in w.asked] == [title]           # no sweep asked
    old.unlink()
    assert f"not generating: {off} outside the SLM field (a right click on a spot removes it)" in log()

    # Generate anyway: generated, with a big red warning; those masks are not checked
    w.answers[title] = "anyway"
    _generate(w)
    alarm = f"⚠ OUTSIDE THE SLM FIELD (Wavefront: ±187.8 µm): {off}. Their masks hold no spot or a cut one; generating anyway"
    assert alarm in log()
    assert re.search(r'font-size:\s*14pt[^>]*>[^<]*⚠ OUTSIDE THE SLM FIELD', w.log_view.document().toHtml())   # big
    for _ in range(3):                                              # spot 2 drawn cut, spot 3 not drawn: not checked
        _simulate_wavefront(w)
    assert "3 phase masks saved" in last() and "HOLDS NO SPOT" not in log()

    # Remove spots and generate: both go (one Undo brings them back), the other one is generated
    w.answers.update({title: "remove", "Phase mask folders not empty": "sweep"})
    w.generate()                                                    # (after sweeping the masks just made)
    assert w.spots == [(336.0, 256.0)] and "removed spot 2, spot 3: outside the SLM field (Undo brings them back)" in log()
    assert len(w.jobs) == 1 and "computing Pattern1 (1 of 1)" in last() and log().count("⚠ OUTSIDE") == 1
    _simulate_wavefront(w)
    assert "1 phase mask saved" in last()
    w.undo()
    assert len(w.spots) == 3

    # nothing left in the field: nothing generated
    w.clear()
    w._on_click(660.0, 256.0, False)
    w.generate()
    assert not w.spots and not w.jobs and "not generating: no spot left in the SLM field" in last()


def test_cleanup_sweeps_until_the_folders_are_empty(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    out_dir = Path(w.config.wfd_output_dir)
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0)):
        w._on_click(x, y, False)
    in_use = {"n": 2}                                               # Wavefront still holds Pattern1 for 2 sweeps
    unlink = Path.unlink

    def unlink_unless_in_use(self, *a, **k):
        if self.name == "Pattern1_000.algoPhp.png" and in_use["n"]:
            in_use["n"] -= 1
            raise PermissionError("in use")
        return unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink_unless_in_use)
    monkeypatch.setattr("oriole.holo.ABORT_WAIT_S", 0)
    _generate(w)
    _simulate_wavefront(w)
    w.btn_generate.click()                                          # STOP
    w.poll()
    assert (out_dir / "Pattern1_000.algoPhp.png").exists() and w.btn_generate.text() == "CLEANING UP…"
    w.poll()
    assert w.cleanup["sweeps"] == 1 and w.btn_generate.text() == "CLEANING UP IN 1 s"     # again 1 s later
    w.cleanup["at"] -= 1
    w.poll()
    assert w.cleanup["sweeps"] == 2
    w.cleanup["at"] -= 1
    w.poll()
    assert w.cleanup is None and not any(out_dir.iterdir())
    assert ("cleaned up after the abort (3 sweeps): Wavefront's phase mask folder emptied (2 files: "
            "Pattern1_000.rawSrc.png, Pattern1_000.algoPhp.png)") in log()

    # still there after MAX_SWEEPS sweeps: said so
    monkeypatch.setattr("oriole.holo.MAX_SWEEPS", 2)
    in_use["n"] = 99
    _generate(w)
    _simulate_wavefront(w)
    w.btn_generate.click()
    for _ in range(2):
        w.cleanup["at"] -= 1
        w.poll()
    assert w.cleanup is None and ("cleanup after the abort incomplete: Pattern1_000.algoPhp.png still in "
                                  "Wavefront's phase mask folder after 2 sweeps: the next Generate asks to sweep "
                                  "them") in log()
    assert "#FFD700" in w.btn_generate.styleSheet()
    in_use["n"] = 0


def test_loaded_mask_spot_is_ringed(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    w.set_image(np.zeros((512, 672), np.float32))
    for x, y in ((100.0, 100.0), (300.0, 200.0)):
        w._on_click(x, y, False)
    _generate(w)
    _simulate_wavefront(w)
    _simulate_wavefront(w)
    assert len(w.view.slm_item.points()) == 0 and not w.view.slm_label.isVisible()
    w.combo_mask.setCurrentText("2")
    w.btn_load_mask.click()
    assert len(w.view.slm_item.points()) == 0           # ringed once Wavefront answered it loaded the mask
    w.poll()
    ring = w.view.slm_item.points()
    assert [tuple(p.pos()) for p in ring] == [(300.0, 200.0)] and ring[0].size() > w.diameter_px()
    assert w.view.slm_label.isVisible()
    w.clear()                                           # spots edited: the ring stays where Pattern2 was clicked
    assert [tuple(p.pos()) for p in w.view.slm_item.points()] == [(300.0, 200.0)]

    # a mask made again since the record (e.g. by DHgui): loaded, but no ring
    t = (w.mask_dir / "Pattern_spots.json").stat().st_mtime + 60
    os.utime(w.mask_dir / "Pattern2_000.algoPhp.png", (t, t))
    _load(w)
    assert len(w.view.slm_item.points()) == 0 and "Pattern2 is not in an up to date Pattern_spots.json" in log()

    # loaded twice in a row: sent twice, as two scripts (Wavefront ignores one identical to the last)
    _load(w)
    assert w.link.sent[-1] == w.link.sent[-2] and w.link.sids[-1] != w.link.sids[-2]
    assert log().count("SLM: Pattern2 loaded") == 3

    # a Load failing in Wavefront: said, no ring
    w.link.runs = True
    w.btn_load_mask.click()
    w.link.queue = [("ERROR", w.link.sids[-1], "Error: no such file")]
    w.poll()
    assert "Pattern2 not loaded: Wavefront's script failed (Error: no such file)" in log() and w.slm_spot is None

    # its file deleted since the list was made: nothing sent, the list made again
    sent = len(w.link.sent)
    (w.mask_dir / "Pattern2_000.algoPhp.png").unlink()
    w.btn_load_mask.click()
    assert f"Pattern2 not loaded: {w.mask_dir / 'Pattern2_000.algoPhp.png'} not found" in log()
    assert len(w.link.sent) == sent and [w.combo_mask.itemText(i) for i in range(w.combo_mask.count())] == ["1"]

    # a new Generate computes on the SLM: the ring goes
    w.combo_mask.setCurrentText("1")
    _load(w)
    assert [tuple(p.pos()) for p in w.view.slm_item.points()] == [(100.0, 100.0)]
    w.answers["Phase mask folders not empty"] = "sweep"
    w._on_click(200.0, 200.0, False)
    w.generate()                                        # after sweeping the masks
    assert w.jobs and len(w.view.slm_item.points()) == 0 and not w.view.slm_label.isVisible()


def test_wavefront_field_on_the_image_and_open_folder(window, monkeypatch):
    w = window
    no_go = w.view.no_go_items
    assert not any(item.isVisible() for item in w.view.field_items + no_go)          # no image: no field
    w.set_image(np.zeros((512, 672), np.float32))
    items = w.view.field_items
    assert len(items) == 5 and all(i.isVisible() and i.parentItem() is w.view.image_item for i in items)
    pens = [(i.opts["pen"].color().name(), i.opts["pen"].style()) for i in items]
    Pen = QtCore.Qt.PenStyle
    assert pens == [("#ff4040", Pen.SolidLine), ("#ff9a3c", Pen.DashLine), ("#ffe14d", Pen.DashLine)] + [
        ("#c8c8c8", Pen.DashLine)] * 2                    # square red, 20 % circle orange, 90 % yellow, cross grey
    centre = w.config.holo.from_um([(0, 0)], (512, 672))[0]
    cross_x, cross_y = items[3].getData()
    assert np.allclose((cross_x.mean(), cross_y.mean()), centre)                     # (0, 0) µm through the calibration
    x90, y90 = items[2].getData()
    assert np.allclose(np.hypot(x90 - centre[0], y90 - centre[1]), 47.0 / 0.67)      # 90 %: r 47 µm
    x_sq, _ = items[0].getData()
    assert np.isclose(x_sq.max() - x_sq.min(), 2 * 187.8 / 0.67)                     # the 576 px canvas
    # outside the square: a red veil and red hatching, over the image only
    assert all(i.isVisible() and i.parentItem() is w.view.image_item for i in no_go)
    assert [i.brush().style() for i in no_go] == [QtCore.Qt.BrushStyle.SolidPattern, QtCore.Qt.BrushStyle.BDiagPattern]
    inside, outside = QtCore.QPointF(*centre), QtCore.QPointF(x_sq.min() - 5, centre[1])
    assert not no_go[1].path().contains(inside) and no_go[1].path().contains(outside)
    w.holo_box.spins["x_um"].setValue(25.0)                                          # the field follows the calibration
    assert np.isclose(items[3].getData()[0].mean(), centre[0] - 20.0 / 0.67)
    assert no_go[1].path().contains(QtCore.QPointF(x_sq.max() - 5, centre[1]))       # now outside on the right
    w.chk_slm_field.setChecked(False)                                                # hidden, and kept so
    assert not any(i.isVisible() for i in items + no_go) and not w.config.show_slm_field
    assert json.loads(Path(w.config.path).read_text())["show_slm_field"] is False
    w.set_image(np.zeros((512, 672), np.float32))
    assert not any(i.isVisible() for i in items + no_go)
    w.chk_slm_field.setChecked(True)
    assert all(i.isVisible() for i in items + no_go)

    opened = []
    monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    w.btn_open_masks.click()
    assert [Path(p) for p in opened] == [w.mask_dir]
    w.mask_dir.rmdir()                                  # the session folder gone meanwhile
    w.btn_open_masks.click()
    assert len(opened) == 1 and "folder not found" in w.log_view.toPlainText().splitlines()[-1]


def _mouse(w, kind, x, y, button, buttons=None):
    """Send a real mouse event at image coordinates (x, y) to the image widget."""
    view = w.view
    pt = view.mapFromScene(view.vb.mapViewToScene(QtCore.QPointF(x, y)))
    ev = QtGui.QMouseEvent(kind, QtCore.QPointF(pt), view.viewport().mapToGlobal(QtCore.QPointF(pt)), button,
                           button if buttons is None else buttons, QtCore.Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(view.viewport(), ev)


def test_real_mouse_events_reach_the_spots(window):
    w = window
    w.set_image(np.zeros((512, 672), np.float32))
    QtWidgets.QApplication.processEvents()
    T, L, R = QtCore.QEvent.Type, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.RightButton
    for x, y in ((200.0, 200.0), (400.0, 300.0)):
        _mouse(w, T.MouseButtonPress, x, y, L)
        _mouse(w, T.MouseButtonRelease, x, y, L, QtCore.Qt.MouseButton.NoButton)
    assert len(w.spots) == 2
    _mouse(w, T.MouseButtonPress, 200.0, 200.0, L)                      # drag spot 1 by the mouse
    for x in (220.0, 250.0, 280.0):
        _mouse(w, T.MouseMove, x, 210.0, QtCore.Qt.MouseButton.NoButton, L)
    _mouse(w, T.MouseButtonRelease, 280.0, 210.0, L, QtCore.Qt.MouseButton.NoButton)
    assert np.allclose(w.spots[0], (280.0, 210.0), atol=2) and len(w.spots) == 2
    _mouse(w, T.MouseButtonPress, 400.0, 300.0, R)                      # right click on spot 2 removes it
    _mouse(w, T.MouseButtonRelease, 400.0, 300.0, R, QtCore.Qt.MouseButton.NoButton)
    assert len(w.spots) == 1
    before = w.view.vb.viewRange()
    _mouse(w, T.MouseButtonPress, 600.0, 450.0, L)                      # dragging the background: no pan
    _mouse(w, T.MouseMove, 500.0, 350.0, QtCore.Qt.MouseButton.NoButton, L)
    _mouse(w, T.MouseButtonRelease, 500.0, 350.0, L, QtCore.Qt.MouseButton.NoButton)
    assert np.allclose(w.view.vb.viewRange(), before)


def test_wrong_image_size_warns(window):
    w = window
    log = w.log_view.toPlainText
    w.set_image(np.zeros((1024, 1344), np.float32))        # unbinned camera image
    assert not w.lbl_image_warning.isHidden()
    assert w.lbl_image_warning.text() == "WRONG IMAGE SIZE\n1344 × 1024 px"          # short on the panel
    assert "WRONG IMAGE SIZE: 1344 x 1024 px instead of 672 x 512" in log()        # detailed in the log
    assert "unbinned camera size: the epi image was not binned 2x2" in log()
    w.set_image(np.zeros((672, 512), np.float32))          # rotated: 512 wide, 672 high
    assert "512 × 672" in w.lbl_image_warning.text()
    assert "probably not binned 2x2 (unbinned it would be 1344 x 1024)" in log()
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
    other.mkdir()
    w.edit_wfd_root._set(str(other))
    assert w.config.wfd_output_dir == str(other / "GeneratedPhasemasks")
    assert w.lbl_output_dir.text() == str(w.mask_dir)  # the session's folder, whatever the Wavefront folder
    assert "folder not found: " + str(other / "GeneratedPhasemasks") in w.log_view.toPlainText()
    w.edit_wfd_root._set(str(other))                    # left without a change: nothing checked or said again
    assert w.log_view.toPlainText().count("folder not found: ") == 1
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

    # Wavefront never runs the script (its Tcp server not ticked): sent again (attempt 1/3, 2/3), aborted at 3/3
    monkeypatch.setattr("oriole.holo.MASK_WAIT_S", -1)
    w.generate()
    for k in (1, 2):
        w.poll()
        assert (f"did not run the Pattern1 script within -1 s (attempt {k}/3): sending it again" in log()
                and w.btn_generate.text() == "■ STOP")
    assert "Wavefront: computing Pattern1 (1 of 1, attempt 3/3)" in log() and len(w.link.sent) == 3
    w.poll()
    assert "(attempt 3/3): generation aborted; cleaning up in 2 s" in log() and not w.jobs
    w.cleanup["at"] -= holo.ABORT_WAIT_S                   # 2 s later
    w.poll()
    assert ("cleaned up after the abort: Wavefront's phase mask folder emptied (0 files); Wavefront IV did not answer: "
            "tick \"Tools → Script engine → Tcp server\" in Wavefront IV") in log()
    assert w.btn_generate.isEnabled() and w.btn_generate.text() == "GENERATE PHASE MASKS"
    w.ping()                                            # it answers again (ticked): ready to generate again
    w.poll()
    w.generate()
    assert w.jobs and w.btn_generate.text() == "■ STOP"
    w.stop_generation()
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()

    # Wavefront not reachable: the generation is aborted at once
    w.link.down = True
    w.generate()
    assert ("cannot send the Pattern1 script: Wavefront IV not reachable over TCP (port 59454): refused: generation "
            "aborted") in log() and not w.jobs and w.wf_port is False and len(w.link.sent) == 5   # 3 + the ping + 1
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()
    assert "restart Wavefront IV and generate again" in log().splitlines()[-1]

    # no NI-DAQ: the error is logged and the shutter stays closed
    def no_daq(device, ao):
        raise ShutterError("nidaqmx is not installed")
    monkeypatch.setattr("oriole.shutter._new_task", no_daq)
    w.btn_open.click()
    assert not w.shutter.is_open and "nidaqmx is not installed" in log()


def test_wavefront_started_checked_and_watched(window, tmp_path, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    root, ini = Path(w.config.wfd_root), tmp_path / "wfdIV.ini"
    procs, started = {}, []                             # Wavefront's processes on a pretend PC
    monkeypatch.setattr("oriole.wavefront.SUPPORTED", True)
    monkeypatch.setattr("oriole.wavefront.INI_PATH", ini)
    monkeypatch.setattr("oriole.wavefront.running", lambda: dict(procs))
    monkeypatch.setattr("oriole.wavefront.window_open", lambda pid: pid in (2, 3, 4))
    monkeypatch.setattr("oriole.wavefront.start", lambda folder: started.append(Path(folder)))
    last = lambda: log().splitlines()[-1]                           # noqa: E731
    w._watch_wavefront()
    assert w.lbl_wavefront.text() == "Wavefront: not running" and w.btn_wavefront.text() == "Start"

    # Start: its program missing (licence); then there: in auto-load mode and saving its masks where Oriole reads them
    # (both set in its settings), then started
    w.btn_wavefront.click()
    assert f"cannot start Wavefront IV: wfdIVCore.dll is missing or empty in {root}" in log() and not started
    (root / "wfdIVCore.dll").write_bytes(b"x")
    w.btn_wavefront.click()
    assert started == [root] and ("set in wfdIV.ini: scriptEngine/autoload, mainSettings/rootPath; Oriole ticks its "
                                  "Tcp server once it is ready") in log()
    assert wavefront.read_ini("scriptEngine", "autoload", ini) == "true"
    assert w.lbl_wavefront.text() == "Wavefront: starting…" and not w.btn_wavefront.isEnabled()

    # ready (its window open): pinged 2 s later; it answers (Tcp server ticked): green
    procs.update(launcher=(1, root), core=(2, root))
    w._watch_wavefront()
    assert w.lbl_wavefront.text() == "Wavefront: running" and w.btn_wavefront.text() == "Check"
    assert w.wf_check.isActive() and "saves its masks in" not in log()
    w.wf_check.stop()
    w._first_check()                                    # what the 2 s timer does
    w.poll()
    assert last().endswith("Wavefront IV runs Oriole's scripts (port 59454)") and w.link.sent == [""]
    assert w.lbl_wavefront.text() == "Wavefront: TCP ✓ (port 59454)" and "#00FF00" in w.lbl_wavefront.styleSheet()
    w.set_image(np.zeros((512, 672), np.float32))
    w._on_click(336.0, 256.0, False)
    _generate(w)
    _simulate_wavefront(w)
    assert w.lbl_wavefront.text() == "Wavefront: TCP ✓ (port 59454)"
    w.btn_wavefront.click()                             # Check: pinged again
    w.poll()
    assert last().endswith("Wavefront IV runs Oriole's scripts (port 59454)") and w.link.sent[-1] == ""

    # its server not reachable (e.g. its port file stale): said in red, until it is again
    w.link.down = True
    w.btn_wavefront.click()
    assert "Wavefront IV not reachable over TCP (port 59454)" in last()
    assert w.lbl_wavefront.text() == "Wavefront: TCP server not reachable"
    w.link.down = False
    w.btn_wavefront.click()
    w.poll()
    assert w.lbl_wavefront.text() == "Wavefront: TCP ✓ (port 59454)"

    # started again, Tcp server not ticked: the ping not answered, Oriole ticks it with a script in its script folder
    (root / "script").mkdir()
    procs.update(core=(3, root))
    w.link.runs = False
    w._watch_wavefront()
    assert w.wf_answers is None and w.lbl_wavefront.text() == "Wavefront: running"
    w.wf_check.stop()
    w._first_check()
    w.waiting["sent"] -= tcplink.PING_WAIT_S
    w.poll()
    assert "Oriole ticks its Tcp server (a script dropped in its script folder)" in last()
    assert w.lbl_wavefront.text() == "Wavefront: ticking its Tcp server…"
    assert "tcpServerQckx" in (root / "script" / "test.js").read_text() and (root / "script" / "zdone.js").exists()
    for name in ("test.js", "zdone.js"):                # Wavefront (in auto-load mode) takes it and runs it
        (root / "script" / name).unlink()
    w.link.runs = True                                  # ticked: it answers, over TCP
    w.link.answer(w.waiting["sid"])
    w.poll()
    assert last().endswith("Tcp server ticked by Oriole: Wavefront IV runs Oriole's scripts (port 59454)")
    assert w.lbl_wavefront.text() == "Wavefront: TCP ✓ (port 59454)"

    # started again, not in auto-load mode: the script not taken, taken back; pinged until ticked by hand
    procs.update(core=(4, root))
    w.link.runs = False
    w._watch_wavefront()
    w.wf_check.stop()
    w._first_check()
    w.waiting["sent"] -= tcplink.PING_WAIT_S
    w.poll()
    w.waiting["sent"] -= tcplink.TICK_WAIT_S
    w.poll()
    assert not any((root / "script").iterdir())         # the script folder as it was
    assert ("Oriole could not tick Wavefront IV's Tcp server: it did not take the script dropped in its script folder "
            "(not in auto-load mode). tick") in last()
    assert w.lbl_wavefront.text() == "Wavefront: no answer (tick Tcp server)"
    w.generate()
    assert "Wavefront IV does not run Oriole's scripts: tick" in last() and not w.jobs
    pings = len(w.link.sent)
    w._watch_wavefront()                                # not again so soon
    w.last_ping -= tcplink.PING_EVERY_S
    w._watch_wavefront()                                # every PING_EVERY_S
    assert len(w.link.sent) == pings + 1 and w.link.sent[-1] == ""
    w.link.runs = True                                  # ticked by hand
    w.link.answer(w.waiting["sid"])
    w.poll()
    assert w.lbl_wavefront.text() == "Wavefront: TCP ✓ (port 59454)"

    # closed: forgotten (its next start has another port and another tick); Generate and Load say so at once
    procs.clear()
    w._watch_wavefront()
    assert w.wf_port is None and w.wf_answers is None and w.lbl_wavefront.text() == "Wavefront: not running"
    w._on_click(100.0, 100.0, False)
    w.generate()
    assert "Wavefront IV is not running: press Start" in log().splitlines()[-1] and not w.jobs

    # running from another install: said once, Generate refused
    other = tmp_path / "wfdIV_old"
    procs.update(core=(2, other))
    w._watch_wavefront()
    w._watch_wavefront()
    assert log().count(f"Wavefront IV runs from {other}, not from the Wavefront folder") == 1
    assert "another install" in w.lbl_wavefront.text() and not w.wf_check.isActive()
    w.generate()
    assert log().count(f"Wavefront IV runs from {other}") == 2 and not w.jobs

    # started by hand (end of a reinstall): it still saves its masks in the old install
    procs.clear()
    w._watch_wavefront()
    procs.update(core=(2, root))
    wavefront.write_ini({("mainSettings", "rootPath"): str(other / "GeneratedPhasemasks")}, ini)
    w._watch_wavefront()
    assert (f"Wavefront IV saves its masks in {other / 'GeneratedPhasemasks'}, not in {w.config.wfd_output_dir}: "
            f"close it and press Start") in log()
    sent = len(w.link.sent)
    w.generate()                                        # it would wait for masks saved elsewhere: nothing sent
    assert log().count(f"Wavefront IV saves its masks in {other / 'GeneratedPhasemasks'}") == 2
    assert not w.jobs and len(w.link.sent) == sent
    _load(w)                                            # a Load names its file: wherever Wavefront saves
    assert "SLM: Pattern1 loaded" in last() and len(w.link.sent) == sent + 1

    # started but its program never runs: the licence
    procs.clear()
    w._watch_wavefront()
    procs.update(launcher=(1, root))
    w.btn_wavefront.click()
    w.wf_started -= 30
    w._watch_wavefront()
    assert "Wavefront IV did not start (see its own message): the licence expired?" in log()

    # Oriole opening starts it (setting kept in the config)
    w.chk_start_wavefront.setChecked(True)
    assert json.loads(Path(w.config.path).read_text())["start_wavefront"] is True
    procs.clear()
    started.clear()
    w2 = MainWindow(Config(wfd_root=str(root)), config_path=tmp_path / "cfg2.json")
    QtWidgets.QApplication.processEvents()
    assert started == [root]
    w2.close()


def test_micro_manager_snap_and_live(window, monkeypatch):
    w = window
    log = w.log_view.toPlainText
    monkeypatch.setattr("oriole.camera.LIVE_PERIOD_S", 0.005)

    class Source:                                       # Micro-Manager as camera.Feed uses it
        def __init__(self):
            self.n, self.error, self.mm_live = 0, None, False

        def settings(self):
            return "2x2", 100.0

        def snap(self):
            if self.error:
                raise CameraError(self.error)
            self.n += 1
            return np.full((512, 672), self.n, np.uint16)

        def live_on(self):
            if self.error:
                raise CameraError(self.error)
            return self.mm_live

        def set_live(self, on):
            self.mm_live = on

        def last_frame(self):
            self.n += 1
            return np.full((512, 672), self.n, np.uint16)

        def shown(self):                                # what Micro-Manager's window shows: nothing taken
            return np.full((512, 672), 500, np.uint16)
    w.feed.source = src = Source()
    w.btn_snap.click()
    assert w.btn_snap.isEnabled() and w.btn_update.isEnabled()        # never greyed out
    wait_for(lambda: w.image is not None)
    assert "image Micro-Manager snap: 672 x 512 px" in log() and w.btn_snap.isEnabled() and w.lbl_image_warning.isHidden()
    assert "Micro-Manager camera: binning 2x2, 100 ms (epi images: 2x2, 1000 ms; set them in Micro-Manager)" in log()
    w._on_click(100.0, 100.0, False)

    # live: Micro-Manager's, its frames one after the other, logged once, the spots kept
    w.btn_live.click()
    assert w.btn_live.isChecked() and w.btn_snap.isEnabled() and "Micro-Manager live: on" in log()
    wait_for(lambda: src.n >= 5 and w.image[0, 0] >= 4)
    assert src.mm_live and log().count("image Micro-Manager live: 672 x 512 px") == 1 and w.spots == [(100.0, 100.0)]
    w.btn_live.click()
    wait_for(lambda: not w.feed.busy)
    QtWidgets.QApplication.processEvents()
    assert "Micro-Manager live: off" in log() and w.btn_snap.isEnabled() and not src.mm_live

    # Snap during Live: Live off first, then the still image (as Micro-Manager's Snap stops its live)
    w.btn_live.click()
    wait_for(lambda: src.mm_live and w.feed.busy)
    snaps = log().count("image Micro-Manager snap")
    w.btn_snap.click()
    assert not w.btn_live.isChecked()
    wait_for(lambda: log().count("image Micro-Manager snap") == snaps + 1)
    wait_for(lambda: not w.feed.busy)
    assert not src.mm_live                              # Oriole had switched it on: off again

    # stopped in Micro-Manager: Oriole's Live goes off by itself
    w.btn_live.click()
    wait_for(lambda: src.mm_live)
    src.mm_live = False
    wait_for(lambda: not w.btn_live.isChecked())
    assert "Micro-Manager's live was stopped: Oriole's Live too" in log() and w.btn_snap.isEnabled()

    # Update: the image Micro-Manager shows, nothing taken
    n = src.n
    w.btn_update.click()
    wait_for(lambda: w.image[0, 0] == 500)
    assert src.n == n and "image Micro-Manager's image: 672 x 512 px" in log() and w.btn_update.isEnabled()

    # Reset: as Oriole opens; a frame still on its way is not shown
    w.btn_live.click()
    wait_for(lambda: src.n > n + 2)
    w.slm_spot = {"x_px": 1.0, "y_px": 2.0, "diameter_um": 10.0}
    w.redraw_spots()
    w.btn_reset.click()
    assert w.image is None and w.spots == [] and w.undo_stack == [] and w.slm_spot is None
    assert not w.btn_live.isChecked() and w.view.image_item.image.shape == (512, 672, 3)
    assert len(w.view.slm_item.points()) == 0 and not any(i.isVisible() for i in w.view.field_items)
    assert "reset: no image, no spot" in log().splitlines()[-1]
    wait_for(lambda: not w.feed.busy)
    QtWidgets.QApplication.processEvents()
    assert w.image is None and w.btn_snap.isEnabled() and w.btn_update.isEnabled()
    w._on_click(10.0, 10.0, False)
    assert "load an image first" in log() and not w.spots

    # Micro-Manager not there: said, live off
    src.error = "Micro-Manager not found on port 4827: start it"
    w.btn_live.click()
    wait_for(lambda: not w.btn_live.isChecked())
    assert "Micro-Manager not found on port 4827" in log() and w.btn_snap.isEnabled() and not w.feed.busy


def test_internal_errors_and_unreadable_settings_are_logged(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("oriole.wavefront.SUPPORTED", False)
    monkeypatch.setattr("oriole.gui.LOG_PATH", tmp_path / "oriole.log")
    monkeypatch.setattr("oriole.gui.JOURNAL_MAX_BYTES", 100)
    journal = tmp_path / "journal.log"                  # where the fake_link fixture puts it
    journal.write_text("x" * 101)                       # grown too big: kept aside, started again
    cfg = Config(wfd_root=str(tmp_path), start_wavefront=False, load_error="settings file x unreadable")
    w = MainWindow(cfg, config_path=tmp_path / "cfg.json")
    try:
        raise ValueError("boom")
    except ValueError as e:
        w.internal_error(type(e), e, e.__traceback__)
    assert "settings file x unreadable" in w.log_view.toPlainText()
    assert "internal error (ValueError: boom), details in" in w.log_view.toPlainText()
    assert 'raise ValueError("boom")' in (tmp_path / "oriole.log").read_text()
    w.close()

    # every line of the log in the journal, dated, with its level
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert lines[1].endswith("----- Oriole TCP opens") and (tmp_path / "journal.log.old").read_text() == "x" * 101
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  error  settings file x unreadable", lines[3])
    assert any("info   Oriole ready: load an image" in line for line in lines)


def test_brooms_delete_only_wavefront_and_oriole_files(window):
    w = window
    log = w.log_view.toPlainText
    out_dir, masks = Path(w.config.wfd_output_dir), w.mask_dir
    notes, mine, sub = masks / "notes.txt", out_dir / "mine.txt", masks / "kept"
    for f in (notes, mine):
        f.write_text("mine")
    sub.mkdir()
    w.refresh_masks()
    assert "#00FF00" in w.btn_generate.styleSheet()         # not Wavefront's: Generate stays green
    (masks / "Pattern3_000.algoPhp.png").write_bytes(b"png")
    w.answers["Sweep the phase mask folder"] = "delete"
    w.btn_sweep_masks.click()
    assert not (masks / "Pattern3_000.algoPhp.png").exists() and notes.exists() and sub.is_dir()
    assert "left alone (not Oriole's or Wavefront's): notes.txt" in log()

    # a generation stopped: its cleanup leaves them too
    w.set_image(np.zeros((512, 672), np.float32))
    w._on_click(100.0, 100.0, False)
    w.generate()                                            # nothing of Wavefront's left: no question asked
    (out_dir / "Pattern1_000.algoPhp.png").write_bytes(b"png")
    w.btn_generate.click()                                  # STOP
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()
    assert ("cleaned up after the abort: Wavefront's phase mask folder emptied (1 file: Pattern1_000.algoPhp.png)"
            in log()) and "left alone (not Oriole's or Wavefront's): mine.txt" in log()
    assert notes.exists() and mine.exists() and sub.is_dir()

    # no longer a Wavefront install (no wfdIV.exe): its folder is never swept, nothing generated
    (Path(w.config.wfd_root) / "wfdIV.exe").unlink()
    w.generate()
    assert f"not generating: {out_dir} is not in a Wavefront install" in log().splitlines()[-1] and not w.jobs
    assert len(w.link.sent) == 1


def test_tcp_version_session_phase_mask_folder(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("oriole.wavefront.SUPPORTED", False)
    picked = {"folder": ""}                             # what the folder dialog gives: "" is Cancel
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda parent, title, start: picked.setdefault("start", start) and
                                     picked["folder"]))
    cfg = Config(wfd_root=str(tmp_path / "wfd"), start_wavefront=False, experiment_drive=str(tmp_path))
    os.makedirs(cfg.wfd_output_dir)
    (tmp_path / "wfd" / "wfdIV.exe").write_bytes(b"")
    w = MainWindow(cfg, config_path=tmp_path / "cfg.json")
    w.tcp_ticked = True                                 # Tcp server ticked, said (that question: in another test)
    log = w.log_view.toPlainText
    assert w.windowTitle() == "Oriole TCP"
    actions = w.wavefront_actions
    assert actions["IV"].isChecked() and not actions["V"].isEnabled()           # V: not ready yet
    assert actions["IV"].text() == "Wavefront IV (TCP)" and not hasattr(w, "btn_sweep_scripts")    # no script folder

    # nothing asked when Oriole opens; Generate green, the folder's broom and 📂 off until it is chosen
    QtWidgets.QApplication.processEvents()
    assert "start" not in picked and w.mask_dir is None and w.lbl_output_dir.text() == ""
    assert w.btn_generate.text() == "GENERATE PHASE MASKS" and "#00FF00" in w.btn_generate.styleSheet()
    assert not w.btn_sweep_masks.isEnabled() and not w.btn_open_masks.isEnabled()

    # no spot yet: said at once, no folder asked
    w.generate()
    assert "start" not in picked and "click at least one spot first" in log()

    # Generate asks for it, the dialog opening on the experiment drive: Cancel, nothing sent
    w.set_image(np.zeros((512, 672), np.float32))
    w._on_click(100.0, 100.0, False)
    w.generate()
    assert picked["start"] == str(tmp_path) and w.mask_dir is None and not w.jobs
    assert "no phase mask folder chosen: nothing generated" in log()

    # chosen: the generation goes on into it
    (tmp_path / "exp").mkdir()
    picked["folder"] = str(tmp_path / "exp")
    w.generate()
    assert w.mask_dir == tmp_path / "exp" / "Phasemasks" and w.jobs and w.btn_generate.text() == "■ STOP"
    w.stop_generation()
    w.cleanup["at"] -= holo.ABORT_WAIT_S
    w.poll()

    # a folder already named Phasemasks is used as it is; its masks are listed, and Generate turns yellow
    existing = tmp_path / "exp2" / "Phasemasks"
    existing.mkdir(parents=True)
    (existing / "Pattern3_000.algoPhp.png").write_bytes(b"png")
    picked["folder"] = str(existing)
    w.btn_choose_masks.click()
    assert w.mask_dir == existing and "(already 1 mask there)" in log() and w.combo_mask.currentText() == "3"
    assert w.btn_sweep_masks.isEnabled() and "#FFD700" in w.btn_generate.styleSheet()
    w.close()
