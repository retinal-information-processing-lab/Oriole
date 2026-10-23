import datetime
import json
import shutil

import numpy as np
import pytest

from oriole.binfile import BinFile, read_header, read_raw_frame_bytes
from oriole.calibration import Calibration
from oriole.visual import bin_name, dmd_px, merge_bin, spot_masks, write_spot_bin
from tests.conftest import EXISTING_BIN


def test_spot_masks_are_discs_in_dmd_space(config):
    masks = spot_masks([(0, 0), (100, -50)], 25.0, config)      # µm from the DMD centre; 2.5 µm per px
    assert masks.shape == (2, 672, 672)
    assert abs(masks[0].sum() - np.pi * 5 ** 2) < 8              # radius 12.5 µm = 5 px
    assert masks[0][336, 336] and masks[1][336 - 20, 336 + 40]
    np.testing.assert_allclose(dmd_px([(100, -50)], config), [[336 + 40, 336 - 20]])


def test_bin_name(tmp_path):
    d = datetime.date(2026, 10, 23)
    assert bin_name(12, date=d) == "2026_10_23_12Frames.bin"
    (tmp_path / "2026_10_23_12Frames.bin").touch()
    assert bin_name(12, tmp_path, d) == "2026_10_23_12Frames_2.bin"
    (tmp_path / "2026_10_23_12Frames_2.bin").touch()
    assert bin_name(12, tmp_path, d) == "2026_10_23_12Frames_3.bin"


def test_write_spot_bin(config, tmp_path):
    cal = Calibration(0, 0, 0, 2.5, 2.5)                        # 1 camera px = 1 DMD px, centred
    spots = [(335.5, 335.5), (375.5, 315.5)]                    # camera pixel centres (336, 336), (376, 316)
    path = write_spot_bin(tmp_path, spots, (672, 672), 25.0, cal, config)
    assert path.name == f"{datetime.date.today():%Y_%m_%d}_2Frames.bin"
    assert read_header(path) == {"xsize": 672, "ysize": 672, "nb_images": 4, "nb_bits": 8}
    with BinFile(str(path), rig_id=3, mode="r") as bf:
        on, neg = bf.read_frame_uint8(2), bf.read_frame_uint8(3)
        assert on[316, 376] == 255 and on[336, 336] == 0
        np.testing.assert_array_equal(on.astype(int) + neg, 255)
    info = json.loads((tmp_path / (path.name + ".json")).read_text())
    assert [(s["spot"], s["pos_idx"], s["neg_idx"]) for s in info["spots"]] == [(1, 0, 1), (2, 2, 3)]
    assert info["spots"][1]["x_um"] == pytest.approx(100.0) and info["diameter_um"] == 25.0
    with pytest.raises(ValueError):
        write_spot_bin(tmp_path, [], (672, 672), 25.0, cal, config)


def test_merge_bin(config, tmp_path):
    spot_bin = write_spot_bin(tmp_path, [(300, 300), (350, 300), (400, 300)], (672, 672), 20.0,
                              Calibration(0, 0, 0, 2.5, 2.5), config)
    linked = tmp_path / "linked.bin"
    shutil.copy(EXISTING_BIN, linked)
    out = tmp_path / "merged.bin"
    m = merge_bin(linked, spot_bin, out)
    assert read_header(out)["nb_images"] == 15 + 6 and m["n_linked_frames"] == 15
    for i in range(15):
        assert read_raw_frame_bytes(out, i) == read_raw_frame_bytes(linked, i)
    for i in range(6):
        assert read_raw_frame_bytes(out, 15 + i) == read_raw_frame_bytes(spot_bin, i)
    assert linked.read_bytes() == EXISTING_BIN.read_bytes()
    assert [(s["spot"], s["pos_idx"], s["neg_idx"]) for s in m["spots"]] == [(1, 15, 16), (2, 17, 18), (3, 19, 20)]
    assert json.loads((tmp_path / "merged.bin.indices.json").read_text())["spots"][2]["neg_idx"] == 20
    with pytest.raises(ValueError, match="new file"):
        merge_bin(linked, spot_bin, linked)
    small = tmp_path / "small"
    small.mkdir()
    tiny = write_spot_bin(small, [(5, 5)], (10, 10), 5.0, Calibration(),
                          type(config)(dmd_height=64, dmd_width=80))
    with pytest.raises(ValueError, match="does not match"):
        merge_bin(linked, tiny, tmp_path / "never.bin")
    assert not (tmp_path / "never.bin").exists()
