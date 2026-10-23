import json

import numpy as np
import pytest

from oriole.calibration import HOLO_DEFAULT, VISUAL_DEFAULT, Calibration


def test_holo_default_matches_dhgui_by_hand():
    # click on the centre of pixel (69, 29) = MATLAB pixel (70, 30); 100x100 image -> u = 20, v = -(30 - 50) = 20
    np.testing.assert_allclose(HOLO_DEFAULT.to_um([(69.5, 29.5)], (100, 100)), [[13.4 + 5, 13.4 - 8]])
    rot = Calibration(5, -8, 90, 0.67, -0.67)          # R(-90°) (20, 20) -> (20, -20)
    np.testing.assert_allclose(rot.to_um([(69.5, 29.5)], (100, 100)), [[13.4 + 5, -13.4 - 8]], atol=1e-12)
    mirror_x = Calibration(5, -8, 0, -0.67, 0.67)
    np.testing.assert_allclose(mirror_x.to_um([(69.5, 29.5)], (100, 100)), [[-13.4 + 5, -13.4 - 8]])


def test_stretch_is_per_axis_and_inverse():
    cal = Calibration(1.0, 2.0, 0.0, 0.5, 2.0)
    np.testing.assert_allclose(cal.to_um([(59.5, 49.5)], (100, 100)), [[10 * 0.5 + 1, 0 + 2]])
    np.testing.assert_allclose(cal.to_um([(49.5, 59.5)], (100, 100)), [[0 + 1, 10 * 2.0 + 2]])
    cal = Calibration(-3, 12, 17, 0.5, -0.7)
    pts = np.random.default_rng(0).uniform(0, 500, (20, 2))
    np.testing.assert_allclose(cal.from_um(cal.to_um(pts, (480, 640)), (480, 640)), pts)
    assert cal.px_um == pytest.approx(0.6)


def test_dict_roundtrip_and_defaults():
    cal = Calibration(1.5, -2, 3, 0.7, -0.71)
    assert Calibration.from_dict(json.loads(json.dumps(cal.to_dict())), VISUAL_DEFAULT) == cal
    assert Calibration.from_dict(None, HOLO_DEFAULT) == HOLO_DEFAULT
    assert Calibration.from_dict({"x_um": 9}, HOLO_DEFAULT) == Calibration(9, -8, 0, 0.67, -0.67)


def test_config_keeps_calibrations_and_reads_legacy_aule(tmp_path, monkeypatch):
    from oriole import config as config_mod
    p = tmp_path / "cfg.json"
    cfg = config_mod.Config.load(p)                       # missing -> defaults
    assert cfg.visual == VISUAL_DEFAULT and cfg.holo == HOLO_DEFAULT and cfg.spot_diameter_um == 10.0
    cfg.holo_cal = Calibration(1, 2, 3, 0.5, -0.5).to_dict()
    cfg.save()
    assert config_mod.Config.load(p).holo == Calibration(1, 2, 3, 0.5, -0.5)

    new, legacy = tmp_path / ".oriole" / "config.json", tmp_path / ".aule" / "config.json"
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", new)
    monkeypatch.setattr(config_mod, "LEGACY_CONFIG_PATH", legacy)
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"rig_id": 2, "dmd_height": 768, "last_dir": "D:/data",
                                  "last_calibration": "old.json", "wfd_profile_prefix": "X"}))
    cfg = config_mod.Config.load()                        # old keys are ignored
    assert cfg.dmd_height == 768 and cfg.last_dir == "D:/data" and cfg.path == str(new)
    assert not hasattr(cfg, "rig_id") and not hasattr(cfg, "wfd_profile_prefix")


def test_config_wavefront_folders_from_one_root():
    from pathlib import Path
    from oriole.config import Config
    cfg = Config(wfd_root=r"E:\wfdIV_20261022")
    assert Path(cfg.wfd_script_dir) == Path(r"E:\wfdIV_20261022\script")
    assert Path(cfg.wfd_output_dir) == Path(r"E:\wfdIV_20261022\GeneratedPhasemasks")
    old = Config.from_dict({"wfd_script_dir": r"D:\wfd\script", "wfd_output_dir": r"D:\wfd\GeneratedPhasemasks"})
    assert Path(old.wfd_root) == Path(r"D:\wfd") and "wfd_script_dir" not in old.to_dict()
