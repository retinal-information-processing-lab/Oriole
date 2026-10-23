"""Loading microscope images (TIFF 8/16-bit, PNG, JPG) as one grey float32 2D array."""
from __future__ import annotations

from pathlib import Path

import numpy as np


def load_gray(path) -> np.ndarray:
    """Image as float32 (rows, cols): colour -> mean of R, G, B; stacks -> max projection."""
    path = Path(path)
    if path.suffix.lower() in (".tif", ".tiff"):
        import tifffile
        arr = tifffile.imread(str(path))
    else:
        import imageio.v2 as imageio
        arr = imageio.imread(str(path))
    arr = np.squeeze(np.asarray(arr))
    if arr.ndim >= 3 and arr.shape[-1] in (3, 4):
        arr = arr[..., :3].mean(axis=-1)
    while arr.ndim > 2:
        arr = arr.max(axis=0)
    if arr.ndim != 2:
        raise ValueError(f"unsupported image shape {arr.shape}")
    return arr.astype(np.float32)
