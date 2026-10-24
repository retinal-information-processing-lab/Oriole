"""Visual (DMD) stimulation: spots -> disc frames in a small dated bin, merged into a linked bin.

A spot bin ``YYYY_MM_DD_<N>Frames.bin`` holds, for spot i (0-based), the spot frame at 2i and its
negative at 2i+1 (N = number of spots). ``<bin>.json`` describes the spots and the frame indices.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

import numpy as np

from .binfile import HEADER_BYTES, BinFile, read_header
from .calibration import Calibration
from .config import Config

RIG_ID = 3                  # MEA3, the only rig (polarity + flip applied when writing frames)
MAX_FRAMES = 65535          # nb_images is a uint16 in the header


def dmd_px(spots_um, config: Config) -> np.ndarray:
    """µm relative to the DMD centre -> (x, y) DMD frame pixels, shape (N, 2)."""
    h, w = config.dmd_shape
    return np.asarray(spots_um, dtype=float).reshape(-1, 2) / config.pixel_size_um + [w / 2, h / 2]


def spot_masks(spots_um, diameter_um: float, config: Config) -> np.ndarray:
    """One disc per spot, bool (N, rows, cols)."""
    h, w = config.dmd_shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = diameter_um / 2 / config.pixel_size_um
    centres = dmd_px(spots_um, config)
    masks = np.zeros((len(centres), h, w), dtype=bool)
    for k, (cx, cy) in enumerate(centres):
        masks[k] = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r ** 2
    return masks


def bin_name(n_spots: int, directory=None, date: _dt.date | None = None) -> str:
    """``YYYY_MM_DD_<N>Frames.bin``; a name already taken in ``directory`` gets ``_2``, ``_3``..."""
    base = f"{(date or _dt.date.today()):%Y_%m_%d}_{n_spots}Frames"
    name, k = f"{base}.bin", 2
    while directory is not None and (Path(directory) / name).exists():
        name, k = f"{base}_{k}.bin", k + 1
    return name


def _write_bin(out_bin: Path, xsize: int, ysize: int, n_total: int, write):
    """Write through a .tmp then rename, so a failed write never leaves a partial bin."""
    if n_total > MAX_FRAMES:
        raise ValueError(f"the bin would have {n_total} frames, more than {MAX_FRAMES}")
    tmp = out_bin.with_suffix(out_bin.suffix + ".tmp")
    try:
        with BinFile(str(tmp), xsize, ysize, rig_id=RIG_ID, nb_images=n_total, mode="w") as bf:
            write(bf)
            if bf.get_frame_nb() + 1 != n_total:
                raise ValueError(f"wrote {bf.get_frame_nb() + 1} frames, header says {n_total}")
        os.replace(tmp, out_bin)
    finally:
        tmp.unlink(missing_ok=True)


def write_spot_bin(directory, spots_px, image_shape, diameter_um: float, cal: Calibration,
                   config: Config) -> Path:
    """Write the spot bin (spot, negative per spot) and its .json in ``directory``."""
    if not len(spots_px):
        raise ValueError("no spot")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    spots_um = cal.to_um(spots_px, image_shape)
    masks = spot_masks(spots_um, diameter_um, config)
    out = directory / bin_name(len(masks), directory)
    h, w = config.dmd_shape

    def write(bf):
        for m in masks:
            bf.append(m.astype(float))
            bf.append(1.0 - m)

    _write_bin(out, w, h, 2 * len(masks), write)
    info = {"bin": out.name, "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "rig_id": RIG_ID, "dmd_shape": [h, w], "dmd_pixel_um": config.pixel_size_um,
            "diameter_um": diameter_um, "calibration": cal.to_dict(), "image_shape": list(image_shape[:2]),
            "spots": [{"spot": k + 1, "x_px": float(p[0]), "y_px": float(p[1]),
                       "x_um": float(u[0]), "y_um": float(u[1]), "pos_idx": 2 * k, "neg_idx": 2 * k + 1}
                      for k, (p, u) in enumerate(zip(np.asarray(spots_px, float), spots_um))]}
    Path(str(out) + ".json").write_text(json.dumps(info, indent=2))
    return out


def _copy_frames(path, header: dict, bf: BinFile):
    frame_bytes = header["xsize"] * header["ysize"]
    with open(path, "rb") as src:
        src.seek(HEADER_BYTES)
        for _ in range(header["nb_images"]):
            chunk = src.read(frame_bytes)
            if len(chunk) != frame_bytes:
                raise ValueError(f"{Path(path).name} is truncated")
            bf.append(chunk)


def merge_bin(linked_bin, spot_bin, out_bin) -> dict:
    """``out_bin`` = linked bin frames then spot bin frames, all byte for byte (inputs untouched).

    Writes ``<out_bin>.indices.json`` with each spot's frame indices in the merged bin.
    """
    linked_bin, spot_bin, out_bin = Path(linked_bin), Path(spot_bin), Path(out_bin)
    if out_bin.resolve() in (linked_bin.resolve(), spot_bin.resolve()):
        raise ValueError("the merged bin must be a new file (inputs are never modified)")
    h0, h1 = read_header(linked_bin), read_header(spot_bin)
    if (h0["xsize"], h0["ysize"]) != (h1["xsize"], h1["ysize"]):
        raise ValueError(f"frame size {h1['xsize']}x{h1['ysize']} of {spot_bin.name} does not match "
                         f"{h0['xsize']}x{h0['ysize']} of {linked_bin.name}")
    if h0["nb_bits"] != 8 or h1["nb_bits"] != 8:
        raise ValueError("only 8-bit bins can be merged")
    n0 = h0["nb_images"]

    def write(bf):              # byte copies: no rig transform
        _copy_frames(linked_bin, h0, bf)
        _copy_frames(spot_bin, h1, bf)

    _write_bin(out_bin, h0["xsize"], h0["ysize"], n0 + h1["nb_images"], write)
    side = Path(str(spot_bin) + ".json")
    spots = json.loads(side.read_text())["spots"] if side.exists() else []
    mapping = {"output_bin": str(out_bin), "linked_bin": str(linked_bin), "spot_bin": str(spot_bin),
               "n_linked_frames": n0, "n_total_frames": n0 + h1["nb_images"],
               "spots": [{**s, "pos_idx": s["pos_idx"] + n0, "neg_idx": s["neg_idx"] + n0} for s in spots]}
    Path(str(out_bin) + ".indices.json").write_text(json.dumps(mapping, indent=2))
    return mapping
