import datetime
import json
import os
import shutil
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pytest

from oriole.calibration import HOLO_DEFAULT
from oriole.holo import (WavefrontBusy, WavefrontClient, available_masks, date_range, drawn_spot, empty_folder,
                         files_in, load_script, mask_name, mask_png, mask_saved, mask_script,
                         off_spot, old_masks, recorded_spot, write_spot_record)

DHGUI_SCRIPT = Path(__file__).resolve().parents[1] / "DHgui_20261022" / "scripts" / "test.js"
DATA = Path(__file__).resolve().parent / "data"


def test_mask_script_has_dhgui_layout():
    js = mask_script("Pattern56", -41.0276, 76.1798, 10)
    assert js.splitlines() == [
        "for (var id = 0; id <= 500; id++) { deleteProfile(id); }", "cleanAllBlob();", "selectTool(2);", 'setProfileName("Pattern56");', "selectTool(2);",
        "setIntensity(1);", "drawSpot(-41.0276,76.1798,10.0000);", "proceedPhp();",
        "var date = new Date();", "var curDate = null;", "do { curDate = new Date(); }",
        "while(curDate-date < 1000);", "saveProfile(0);", "deleteProfile(0);"]
    if DHGUI_SCRIPT.exists():           # MATLAB reference (2 spots at intensity 0.5): same structure
        ref = [l for l in DHGUI_SCRIPT.read_text().splitlines() if not l.startswith(("setIntensity", "drawSpot"))]
        assert ref == [l for l in js.splitlines()[1:] if not l.startswith(("setIntensity", "drawSpot"))]


def test_load_script_is_plerions_with_the_list_emptied():
    assert load_script(r"E:\wfdIV\GeneratedPhasemasks\Pattern2_000.algoPhp.png") == (
        "for (var id = 0; id <= 500; id++) { deleteProfile(id); }\ncleanAllBlob();\n"
        'loadProfile("E:\\\\wfdIV\\\\GeneratedPhasemasks\\\\Pattern2_000.algoPhp.png", 2);\n')


def test_drawn_spot(tmp_path):
    # Wavefront's own drawing of DHgui's drawSpot(-115.9495,74.619,10) (Pattern9, 2026 10 22): 1.8 µm off
    shutil.copy(DATA / "Pattern9_000.rawSrc.png", tmp_path)
    drawn = drawn_spot(tmp_path, "Pattern9", 10)
    assert drawn == pytest.approx((-114.40, 73.68), abs=0.01)
    assert not off_spot(drawn, -115.9495, 74.619, 10) and not off_spot(drawn, -110.0, 74.619, 10)  # 4.4 µm off
    assert off_spot(drawn, -72.4, 28.4, 10)                                                     # Pattern8's spot
    assert not off_spot(drawn, -114.4, 76.0, 2) and off_spot(drawn, -114.4, 77.0, 2)          # 3 µm at least
    imageio.imwrite(tmp_path / "Pattern1_000.rawSrc.png", np.zeros((576, 576), np.uint8))
    nothing = drawn_spot(tmp_path, "Pattern1", 10)
    assert np.isnan(nothing).all() and off_spot(nothing, 0, 0, 10)                              # nothing drawn
    assert drawn_spot(tmp_path, "Pattern2", 10) is None                                         # nothing to check


def test_client_drops_script_then_trigger(tmp_path):
    client = WavefrontClient(tmp_path)
    assert client.done()
    client.submit("cleanAllBlob();\n")
    assert (tmp_path / "test.js").read_text() == "cleanAllBlob();\n"
    assert (tmp_path / "zdone.js").exists() and not (tmp_path / "test.js.part").exists()
    assert client.busy
    with pytest.raises(WavefrontBusy, match="auto-load"):
        client.submit("x")
    (tmp_path / "test.js").unlink()             # what Wavefront does once it ran the script
    assert client.done()
    (tmp_path / "test.js").write_text("left behind")
    (tmp_path / "sub").mkdir()
    assert client.purge() == ["test.js", "zdone.js"] and not client.busy and (tmp_path / "sub").is_dir()
    assert empty_folder(tmp_path / "missing") == []
    with pytest.raises(FileNotFoundError):
        WavefrontClient(tmp_path / "missing").submit("x")


def test_old_masks_dates_and_record(tmp_path):
    for n in ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern12_000.algoPhp.png", "Pattern2_000.slm.png",
              "PatternAll_000.algoPhp.png", "Pattern_spots.json", "Pattern3.txt"]:
        (tmp_path / n).touch()
    t = datetime.datetime(2026, 10, 20, 12).timestamp()
    os.utime(tmp_path / "Pattern1_000.xml", (t, t))
    old = old_masks(tmp_path)
    assert [p.name for p in old] == ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern2_000.slm.png",
                                     "Pattern12_000.algoPhp.png"]
    assert date_range(old) == (datetime.date(2026, 10, 20), datetime.date.today())
    assert date_range([]) is None and old_masks(tmp_path / "missing") == []
    assert available_masks(tmp_path) == [1, 12] and available_masks(tmp_path / "missing") == []
    assert mask_name(4) == "Pattern4"                       # DHgui: 'Pattern' int2str(num_frame)
    assert mask_png(tmp_path, mask_name(4)) == tmp_path / "Pattern4_000.algoPhp.png"
    rec = write_spot_record(tmp_path, [(1, 2), (3, 4)], [(10.0, 20.0), (30.0, 40.0)], 12.0, HOLO_DEFAULT)
    d = json.loads(rec.read_text())
    assert rec.name == "Pattern_spots.json" and d["diameter_um"] == 12.0
    assert [(m["mask"], m["x_um"]) for m in d["masks"]] == [("Pattern1_000.algoPhp.png", 10.0),
                                                            ("Pattern2_000.algoPhp.png", 30.0)]


def test_mask_saved_and_files_in(tmp_path):
    png = mask_png(tmp_path, mask_name(1))
    assert not mask_saved(png, 0)                                       # not there
    png.write_bytes(b"")
    assert not mask_saved(png, 0)                                       # empty
    png.write_bytes(b"png")
    assert mask_saved(png, png.stat().st_mtime) and not mask_saved(png, png.stat().st_mtime + 60)   # too old
    for n in ["Pattern12_000.algoPhp.png", "_000.xml", "Pattern2_000.algoPhp.png"]:
        (tmp_path / n).touch()
    (tmp_path / "sub").mkdir()
    assert [p.name for p in files_in(tmp_path)] == ["_000.xml", "Pattern1_000.algoPhp.png",
                                                    "Pattern2_000.algoPhp.png", "Pattern12_000.algoPhp.png"]
    assert files_in(tmp_path / "missing") == []


def test_recorded_spot(tmp_path):
    for n in (1, 2, 3):
        mask_png(tmp_path, mask_name(n)).touch()
    assert recorded_spot(tmp_path, 1) is None                           # no record yet (masks made by DHgui)
    rec = write_spot_record(tmp_path, [(1, 2), (3, 4)], [(10.0, 20.0), (30.0, 40.0)], 12.0, HOLO_DEFAULT)
    assert recorded_spot(tmp_path, 2) == {"x_px": 3.0, "y_px": 4.0, "diameter_um": 12.0}
    assert recorded_spot(tmp_path, 3) is None and recorded_spot(tmp_path, 4) is None     # not in it / no mask
    t = rec.stat().st_mtime + 60
    os.utime(mask_png(tmp_path, mask_name(1)), (t, t))                  # mask 1 made again after the record
    assert recorded_spot(tmp_path, 1) is None
    rec.write_text("{not json")
    assert recorded_spot(tmp_path, 2) is None
