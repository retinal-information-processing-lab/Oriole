import json

import numpy as np
import pytest

from oriole.geometry import Circle, Polygon
from oriole.holo import HoloCalibration, HoloSpot, holo_spots, write_holo_record


def test_camera_to_slm_matches_dhgui_by_hand():
    cal = HoloCalibration()                     # DHparams.m: 0.67 µm/px, ΔX 5, ΔY -8, θ 0, mirror y
    # click on the centre of pixel (69, 29) = MATLAB pixel (70, 30); 100x100 image -> u = 20, v = -(30 - 50) = 20
    np.testing.assert_allclose(cal.camera_to_slm([(69.5, 29.5)], (100, 100)), [[13.4 + 5, 13.4 - 8]])
    cal.theta_deg = 90                          # R(-90°) (20, 20) -> (20, -20)
    np.testing.assert_allclose(cal.camera_to_slm([(69.5, 29.5)], (100, 100)), [[13.4 + 5, -13.4 - 8]], atol=1e-12)
    cal.theta_deg, cal.flip_y, cal.flip_x = 0, False, True
    np.testing.assert_allclose(cal.camera_to_slm([(69.5, 29.5)], (100, 100)), [[-13.4 + 5, -13.4 - 8]])


def test_slm_to_camera_inverts():
    cal = HoloCalibration(pixel_size_um=0.5, delta_x_um=-3, delta_y_um=12, theta_deg=17, flip_x=True)
    pts = np.random.default_rng(0).uniform(0, 500, (20, 2))
    np.testing.assert_allclose(cal.slm_to_camera(cal.camera_to_slm(pts, (480, 640)), (480, 640)), pts)
    assert cal.um_to_camera_px(cal.camera_px_to_um(7.0)) == pytest.approx(7.0)


def test_json_roundtrip(tmp_path):
    cal = HoloCalibration(pixel_size_um=0.71, delta_x_um=1.5, theta_deg=-2, flip_x=True)
    p = cal.save(tmp_path / "holo.json")
    assert cal.created and "path" not in json.load(open(p))
    back = HoloCalibration.load(p)
    assert back == cal and back.path == str(p) and "today" in back.created_text()
    assert "0.71 µm/px" in back.summary() and "mirror x+y" in back.summary()


def test_holo_spots_keep_enabled_circles():
    cal = HoloCalibration(pixel_size_um=0.5)
    shapes = [Circle(50, 50, 10, name="S1"), Circle(10, 10, 4, name="S2", enabled=False),
              Polygon([[0, 0], [5, 0], [0, 5]], name="S3"), Circle(20, 70, 6, name="S4")]
    spots = holo_spots(shapes, cal, (100, 100))
    assert [s.name for s in spots] == ["S1", "S4"]
    assert spots[0].d_um == pytest.approx(10.0) and spots[1].d_um == pytest.approx(6.0)
    np.testing.assert_allclose([spots[1].x_um, spots[1].y_um], cal.camera_to_slm([(20, 70)], (100, 100))[0])
    assert (spots[1].cx, spots[1].cy) == (20, 70)
    assert holo_spots([], cal, (100, 100)) == []


def test_write_holo_record(tmp_path):
    cal = HoloCalibration()
    spots = [HoloSpot("S1", 1.0, 2.0, 10.0, 3.0, 4.0)]
    masks = [{"profile": "Pattern1", "spot": "S1", "png": "x/Pattern1_000.algoPhp.png", "exists": True}]
    p = write_holo_record(tmp_path, "my set", spots, cal, "img.tif", (480, 640, 3), masks)
    assert p.name == "my_set.holo.json"
    d = json.load(open(p))
    assert d["spots"][0] == {"name": "S1", "x_um": 1.0, "y_um": 2.0, "d_um": 10.0, "cx": 3.0, "cy": 4.0}
    assert d["image_shape"] == [480, 640] and d["masks"] == masks and d["calibration"]["delta_y_um"] == -8.0
