"""Pattern masks -> DMD frames (pos + neg per mask): small standalone bins and merges into a bin.

A pattern source is either a ``.patterns.npz`` (masks, written through the rig transform) or a
``.bin`` such as the small bins of :func:`write_pattern_bin` (frames copied byte for byte; frame
names come from its ``<bin>.indices.json`` when there is one).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .binfile import HEADER_BYTES, RIG_MAX_DIMS, BinFile, read_header
from .patterns import NPZ_SUFFIX, load_masks, pattern_stem

MAX_FRAMES = 65535          # nb_images is a uint16 in the header


def indices_json_path(bin_path) -> Path:
    return Path(str(bin_path) + ".indices.json")


def _read_source(path) -> dict:
    """Frames a pattern source adds: entries (name, pos, neg, n_pixels) relative to its first frame."""
    path = Path(path)
    if path.suffix.lower() == ".bin":
        header = read_header(path)
        side = indices_json_path(path)
        meta = json.loads(side.read_text()) if side.exists() else {}
        named = sorted(meta.get("frames", {}).items(), key=lambda kv: kv[1]["pos_idx"])
        if named:
            entries = [(n, e["pos_idx"], e.get("neg_idx"), e.get("n_pixels")) for n, e in named]
        else:
            entries = [(f"{path.stem}_{k}", k, None, None) for k in range(header["nb_images"])]
        return {"path": path, "kind": "bin", "header": header, "n_frames": header["nb_images"],
                "dmd_shape": (header["ysize"], header["xsize"]), "rig_id": meta.get("rig_id"),
                "prefix": path.stem, "entries": entries}
    names, masks, meta = load_masks(path)
    entries = [(n, 2 * k, 2 * k + 1, int(m.sum())) for k, (n, m) in enumerate(zip(names, masks))]
    return {"path": path, "kind": "npz", "masks": masks, "n_frames": 2 * len(masks),
            "dmd_shape": tuple(meta["dmd_shape"]), "rig_id": meta["rig_id"],
            "prefix": pattern_stem(path).name, "entries": entries}


def _unique(name: str, used: set, prefix: str) -> str:
    """``name``, or ``<prefix>_<name>`` (then a counter) when another source already used it."""
    cand, k = name, 2
    if cand in used:
        cand = f"{prefix}_{name}"
    while cand in used:
        cand, k = f"{prefix}_{name}_{k}", k + 1
    used.add(cand)
    return cand


def _plan(sources, shape, n0: int) -> tuple[list[dict], int]:
    entries, idx, used = [], n0, set()
    for src in sources:
        if src["dmd_shape"] != shape:
            raise ValueError(f"{src['path'].name}: frame shape {src['dmd_shape']} does not match bin {shape}")
        for name, pos, neg, n_pix in src["entries"]:
            entries.append({"frame": _unique(name, used, src["prefix"]), "pattern_file": str(src["path"]),
                            "pos_idx": idx + pos, "neg_idx": None if neg is None else idx + neg,
                            "n_pixels": n_pix})
        idx += src["n_frames"]
    return entries, idx - n0


def plan_merge(original_bin, pattern_files) -> dict:
    """Describe what a merge would do without writing anything."""
    header = read_header(original_bin)
    n0 = header["nb_images"]
    sources = [_read_source(p) for p in pattern_files]
    entries, n_new = _plan(sources, (header["ysize"], header["xsize"]), n0)
    return {"header": header, "n_original": n0, "n_new": n_new, "entries": entries, "sources": sources}


def _resolve_rig(rig_id: int | None, sources) -> int | None:
    if rig_id is not None:
        return rig_id
    rigs = {s["rig_id"] for s in sources if s["rig_id"] is not None}
    if len(rigs) > 1:
        raise ValueError(f"pattern files come from different rigs: {sorted(rigs)}")
    return rigs.pop() if rigs else None


def _copy_frames(path, header: dict, bf: BinFile):
    frame_bytes = header["xsize"] * header["ysize"]
    with open(path, "rb") as src:
        src.seek(HEADER_BYTES)
        for _ in range(header["nb_images"]):
            chunk = src.read(frame_bytes)
            if len(chunk) != frame_bytes:
                raise ValueError(f"{Path(path).name} is truncated")
            bf.append(chunk)


def _write_sources(bf: BinFile, sources):
    """Masks as pos then neg frame (the order of every Oriole bin); bins byte for byte."""
    for src in sources:
        if src["kind"] == "npz":
            for m in src["masks"]:
                pos = m.astype(float)
                bf.append(pos)
                bf.append(1.0 - pos)
        else:
            _copy_frames(src["path"], src["header"], bf)


def _write_bin(out_bin: Path, xsize: int, ysize: int, rig: int | None, n_total: int, write):
    """Write through a .tmp then rename, so a failed write never leaves a partial bin."""
    if n_total > MAX_FRAMES:
        raise ValueError(f"resulting bin would exceed {MAX_FRAMES} frames (uint16 header)")
    if rig is None:     # only byte copies: any rig whose frame fits is fine for BinFile's checks
        rig = next(r for r, (mx, my) in RIG_MAX_DIMS.items() if xsize <= mx and ysize <= my)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_bin.with_suffix(out_bin.suffix + ".tmp")
    try:
        with BinFile(str(tmp), xsize, ysize, rig_id=rig, nb_images=n_total, mode="w") as bf:
            write(bf)
            if bf.get_frame_nb() + 1 != n_total:
                raise ValueError(f"wrote {bf.get_frame_nb() + 1} frames, header says {n_total}")
        os.replace(tmp, out_bin)
    finally:
        tmp.unlink(missing_ok=True)


def _save_mapping(out_bin: Path, mapping: dict, entries) -> dict:
    mapping["frames"] = {e["frame"]: {"pos_idx": e["pos_idx"], "neg_idx": e["neg_idx"],
                                      "n_pixels": e["n_pixels"], "pattern_file": e["pattern_file"]}
                         for e in entries}
    json_path = indices_json_path(out_bin)
    with open(json_path, "w") as f:
        json.dump(mapping, f, indent=2)
    mapping["indices_json"] = str(json_path)
    return mapping


def write_pattern_bin(pattern_file, out_bin, rig_id: int | None = None) -> dict:
    """Write a small standalone bin from a .patterns.npz: pos then neg frame per mask (2N frames).

    Returns the index mapping (also written to ``<out_bin>.indices.json``, which lets the Merge
    tab name the frames when this bin is merged into another one).
    """
    out_bin = Path(out_bin)
    src = _read_source(pattern_file)
    if src["kind"] != "npz":
        raise ValueError(f"{Path(pattern_file).name}: expected a {NPZ_SUFFIX} file")
    h, w = src["dmd_shape"]
    rig = _resolve_rig(rig_id, [src])
    entries, n_total = _plan([src], (h, w), 0)
    _write_bin(out_bin, w, h, rig, n_total, lambda bf: _write_sources(bf, [src]))
    return _save_mapping(out_bin, {"output_bin": str(out_bin), "pattern_files": [str(src["path"])],
                                   "rig_id": rig, "dmd_shape": [h, w], "n_original_frames": 0,
                                   "n_total_frames": n_total}, entries)


def merge_bin(original_bin, pattern_files, out_bin, rig_id: int | None = None) -> dict:
    """Write ``out_bin`` = original frames (byte-exact) + the frames of each pattern source.

    Returns the index mapping (also written to ``<out_bin>.indices.json``).
    """
    original_bin, out_bin = Path(original_bin), Path(out_bin)
    if not original_bin.exists():
        raise FileNotFoundError(original_bin)
    pattern_files = [Path(p) for p in pattern_files]
    if not pattern_files:
        raise ValueError("no pattern file given")
    if out_bin.resolve() in {p.resolve() for p in [original_bin, *pattern_files]}:
        raise ValueError("output must differ from the original bin and the pattern files (inputs are never modified)")

    plan = plan_merge(original_bin, pattern_files)
    header, sources = plan["header"], plan["sources"]
    for h in [header] + [s["header"] for s in sources if s["kind"] == "bin"]:
        if h["nb_bits"] != 8:
            raise ValueError(f"unsupported nb_bits={h['nb_bits']}")
    n_total = plan["n_original"] + plan["n_new"]
    rig = _resolve_rig(rig_id, sources)

    def write(bf):
        _copy_frames(original_bin, header, bf)
        _write_sources(bf, sources)

    _write_bin(out_bin, header["xsize"], header["ysize"], rig, n_total, write)
    return _save_mapping(out_bin, {"output_bin": str(out_bin), "original_bin": str(original_bin),
                                   "pattern_files": [str(p) for p in pattern_files], "rig_id": rig,
                                   "n_original_frames": plan["n_original"], "n_total_frames": n_total},
                         plan["entries"])
