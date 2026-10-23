import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oriole.config import Config  # noqa: E402

# Lab stimulus bin (not in the repository). When absent, an equivalent synthetic bin is generated:
# 15 frames of 672x672 written for rig 3, frame 0 black, 1 white, 2 grey (128), then gratings.
_LAB_BIN = ROOT / "ReversingGrating" / "Bin_Files" / "ReversingGrating_OssStim_MEA3_12SpatialPatterns.bin"


def _synthetic_lab_bin(path: Path, seed: int) -> Path:
    from oriole.binfile import BinFile, apply_write_transform
    n, h, w = 15, 672, 672
    rng = np.random.default_rng(seed)
    with BinFile(str(path), w, h, rig_id=3, nb_images=n, mode="w") as bf:
        bf.append(apply_write_transform(np.zeros((h, w)), 3).tobytes())        # black
        bf.append(apply_write_transform(np.ones((h, w)), 3).tobytes())         # white
        bf.append(np.full((h, w), 255 - 128, np.uint8).tobytes())              # grey 128 after polarity undo
        for i in range(n - 3):
            period = int(rng.integers(8, 96))
            grating = ((np.arange(w) // period) % 2).astype(float)[None, :].repeat(h, axis=0)
            bf.append(grating if i % 2 == 0 else grating.T)
    return path


def _lab_bin_or_synthetic(lab_path: Path, name: str, seed: int) -> Path:
    if lab_path.exists() and not os.environ.get("ORIOLE_TEST_SYNTHETIC_BIN"):
        return lab_path
    cache = Path(tempfile.gettempdir()) / "oriole_tests"
    cache.mkdir(exist_ok=True)
    path = cache / name
    if not path.exists() or path.stat().st_size != 15 * 672 * 672 + 8:
        _synthetic_lab_bin(path, seed)
    return path


EXISTING_BIN = _lab_bin_or_synthetic(_LAB_BIN, "synthetic_reversing.bin", 1)


@pytest.fixture
def config():
    return Config(dmd_height=672, dmd_width=672, pixel_size_um=2.5)
