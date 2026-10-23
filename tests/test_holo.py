import datetime
import json
import os
from pathlib import Path

import pytest

from oriole.calibration import HOLO_DEFAULT
from oriole.holo import (WavefrontBusy, WavefrontClient, date_range, load_script, mask_png, mask_script,
                         old_masks, write_spot_record)

DHGUI_SCRIPT = Path(__file__).resolve().parents[1] / "DHgui_20261022" / "scripts" / "test.js"


def test_mask_script_has_dhgui_layout():
    js = mask_script("Pattern56", -41.0276, 76.1798, 10)
    assert js.splitlines() == [
        "cleanAllBlob();", "selectTool(2);", 'setProfileName("Pattern56");', "selectTool(2);",
        "setIntensity(1);", "drawSpot(-41.0276,76.1798,10.0000);", "proceedPhp();",
        "var date = new Date();", "var curDate = null;", "do { curDate = new Date(); }",
        "while(curDate-date < 1000);", "saveProfile(0);", "deleteProfile(0);"]
    if DHGUI_SCRIPT.exists():           # MATLAB reference (2 spots at intensity 0.5): same structure
        ref = [l for l in DHGUI_SCRIPT.read_text().splitlines() if not l.startswith(("setIntensity", "drawSpot"))]
        assert ref == [l for l in js.splitlines() if not l.startswith(("setIntensity", "drawSpot"))]


def test_load_script_is_plerions():
    assert load_script(r"E:\wfdIV\GeneratedPhasemasks\Pattern2_000.algoPhp.png") == (
        'cleanAllBlob();\ndeleteProfile(0);\n'
        'loadProfile("E:\\\\wfdIV\\\\GeneratedPhasemasks\\\\Pattern2_000.algoPhp.png", 2);\n')


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
    with pytest.raises(FileNotFoundError):
        WavefrontClient(tmp_path / "missing").submit("x")


def test_old_masks_dates_and_record(tmp_path):
    for n in ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern12_000.algoPhp.png", "Pattern2_000.slm.png",
              "PatternAll_000.algoPhp.png", "Pattern_spots.json", "Pattern3.txt"]:
        (tmp_path / n).touch()
    t = datetime.datetime(2026, 10, 20, 12).timestamp()
    os.utime(tmp_path / "Pattern1_000.xml", (t, t))
    old = old_masks(tmp_path, "Pattern")
    assert [p.name for p in old] == ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern2_000.slm.png",
                                     "Pattern12_000.algoPhp.png"]
    assert date_range(old) == (datetime.date(2026, 10, 20), datetime.date.today())
    assert date_range([]) is None and old_masks(tmp_path / "missing", "Pattern") == []
    assert mask_png(tmp_path, "Pattern4") == tmp_path / "Pattern4_000.algoPhp.png"
    rec = write_spot_record(tmp_path, "Pattern", [(1, 2), (3, 4)], [(10.0, 20.0), (30.0, 40.0)], 12.0, HOLO_DEFAULT)
    d = json.loads(rec.read_text())
    assert rec.name == "Pattern_spots.json" and d["diameter_um"] == 12.0
    assert [(m["mask"], m["x_um"]) for m in d["masks"]] == [("Pattern1_000.algoPhp.png", 10.0),
                                                            ("Pattern2_000.algoPhp.png", 30.0)]
