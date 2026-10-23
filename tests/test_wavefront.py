import datetime
import os
from pathlib import Path

import pytest

from oriole.holo import HoloSpot
from oriole.wavefront import (WavefrontBusy, WavefrontClient, date_range, list_masks, live_script, load_script,
                              mask_png, mask_script, old_masks)

DHGUI_SCRIPT = Path(__file__).resolve().parents[1] / "DHgui_20261022" / "scripts" / "test.js"


def test_mask_script_has_dhgui_layout():
    js = mask_script("Pattern56", HoloSpot("S1", -41.0276, 76.1798, 10))
    assert js.splitlines() == [
        "cleanAllBlob();", "selectTool(2);", 'setProfileName("Pattern56");', "selectTool(2);",
        "setIntensity(1);", "drawSpot(-41.0276,76.1798,10.0000);", "proceedPhp();",
        "var date = new Date();", "var curDate = null;", "do { curDate = new Date(); }",
        "while(curDate-date < 1000);", "saveProfile(0);", "deleteProfile(0);"]
    if DHGUI_SCRIPT.exists():           # MATLAB reference (2 spots at intensity 0.5): same structure
        ref = [l for l in DHGUI_SCRIPT.read_text().splitlines()
               if not l.startswith(("setIntensity", "drawSpot"))]
        assert ref == [l for l in js.splitlines() if not l.startswith(("setIntensity", "drawSpot"))]


def test_live_and_load_scripts():
    js = live_script([HoloSpot("a", 1, 2, 10), HoloSpot("b", -3.5, 4.25, 12)])
    lines = js.splitlines()
    assert lines[:6] == ["cleanAllBlob();", "selectTool(2);", "deleteProfile(0);", 'setProfileName("Live");',
                         "selectTool(2);", "setIntensity(0.9999);"]
    assert lines[6:] == ["drawSpot(1.0000,2.0000,10.0000);", "drawSpot(-3.5000,4.2500,12.0000);",
                         "proceedPhp();", "cleanAllBlob();"]
    assert "saveProfile" not in js
    assert load_script(r"E:\wfdIV\GeneratedPhasemasks\Pattern2_000.algoPhp.png") == (
        'cleanAllBlob();\ndeleteProfile(0);\n'
        'loadProfile("E:\\\\wfdIV\\\\GeneratedPhasemasks\\\\Pattern2_000.algoPhp.png", 2);\n')


def test_client_drops_script_then_trigger(tmp_path):
    client = WavefrontClient(tmp_path)
    assert client.available and client.done()
    client.submit("cleanAllBlob();\n")
    assert (tmp_path / "test.js").read_text() == "cleanAllBlob();\n"
    assert (tmp_path / "zdone.js").exists() and not (tmp_path / "test.js.part").exists()
    assert client.busy
    with pytest.raises(WavefrontBusy, match="auto-load"):
        client.submit("x")
    (tmp_path / "test.js").unlink()             # what wfdIV does once it ran the script
    assert client.done()
    with pytest.raises(FileNotFoundError):
        WavefrontClient(tmp_path / "missing").submit("x")


def test_old_masks_and_listing(tmp_path):
    names = ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern12_000.algoPhp.png", "Pattern2_000.slm.png",
             "PatternAll_000.algoPhp.png", "Live_000.algoPhp.png", "Pattern3.txt"]
    for n in names:
        (tmp_path / n).touch()
    t = datetime.datetime(2026, 10, 20, 12).timestamp()
    os.utime(tmp_path / "Pattern1_000.xml", (t, t))
    old = [p.name for p in old_masks(tmp_path, "Pattern")]
    assert old == ["Pattern1_000.algoPhp.png", "Pattern1_000.xml", "Pattern2_000.slm.png", "Pattern12_000.algoPhp.png"]
    first, last = date_range(old_masks(tmp_path, "Pattern"))
    assert first == datetime.date(2026, 10, 20) and last == datetime.date.today()
    assert date_range([]) is None and old_masks(tmp_path / "missing", "Pattern") == []
    assert [p.name for p in list_masks(tmp_path)] == ["Live_000.algoPhp.png", "Pattern1_000.algoPhp.png",
                                                      "Pattern12_000.algoPhp.png", "PatternAll_000.algoPhp.png"]
    assert mask_png(tmp_path, "Pattern4") == tmp_path / "Pattern4_000.algoPhp.png"
