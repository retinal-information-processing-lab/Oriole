"""Holographic (SLM) stimulation through Wavefront IV, driven by file drop as DHgui and Plerion do.

Wavefront watches its ``script`` folder (script auto-load on): a ``test.js`` followed by an empty
``zdone.js`` makes it run the script, then the files are removed. One phase mask is computed and
saved per spot (DHgui GeneratePhaseMasks); a saved mask is shown with Plerion's loadProfile script.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

import numpy as np

from .calibration import Calibration
from .images import load_gray

SCRIPT_NAME = "test.js"
TRIGGER_NAME = "zdone.js"
PREFIX = "Pattern"                   # DHgui names the mask of spot n Pattern<n> (what Plerion counts)
MASK_SUFFIX = "_000.algoPhp.png"     # Wavefront saves profile <name> as <name>_000.algoPhp.png (+ siblings)
SRC_SUFFIX = "_000.rawSrc.png"       # ... and the pattern it drew for it as <name>_000.rawSrc.png
RECORD_NAME = f"{PREFIX}_spots.json"
LIST_IDS = 500                       # pattern IDs 0..LIST_IDS deleted to empty Wavefront's list (0.7 ms each)
NOT_READ_S = 10.0                    # a script still there after this long was not taken: erased, sent again
MASK_WAIT_S = 10.0                  # a script taken but its mask still not saved after this long: sent again
MAX_ATTEMPTS = 3                     # sends of the same script before giving up
# Wavefront's field as it draws it, in µm around its (0, 0) (measured on its display, rig wfdIV_20261022): its
# 576 px canvas, a square; its diffraction efficiency circles, by %; a cross at the centre
FIELD_HALF_UM = 187.8
EFFICIENCY_RADII_UM = {90: 47.0, 80: 68.4, 70: 86.3, 60: 103.0, 50: 119.6, 40: 137.0, 30: 156.2, 20: 179.0}
CROSS_HALF_UM = 18.0
MAX_SWEEPS = 10                     # sweeps of the Wavefront folders after an abort, while a file is left
ABORT_WAIT_S = 2.0                 # after an abort Wavefront may still be computing: sweep only after this long


def mask_name(n: int) -> str:
    return f"{PREFIX}{n}"


# ----------------------------------------------------------------------------- scripts
# Wavefront's pattern (profile) list, as tried on the rig: a computed or loaded pattern takes the lowest free ID
# (the number shown in the list); saveProfile(0) saves it only when that is 0, and deleteProfile(n) deletes the
# pattern of ID n (not the n-th one; an ID not there does nothing). DHgui's scripts end with deleteProfile(0), so
# one pattern left in the list (made by hand, loaded, a mask not saved) makes every following mask take ID 1 or
# more and none is saved. There is no script command to empty the list: every ID is deleted, first thing.
EMPTY_LIST = f"for (var id = 0; id <= {LIST_IDS}; id++) {{ deleteProfile(id); }}"


def mask_script(profile: str, x_um: float, y_um: float, diameter_um: float) -> str:
    """Compute and save the phase mask of one spot at full power (DHgui GeneratePhaseMasks), Wavefront's pattern
    list emptied first so that the spot's pattern is ID 0, the one saveProfile(0) saves."""
    lines = [EMPTY_LIST, "cleanAllBlob();", "selectTool(2);", f'setProfileName("{profile}");', "selectTool(2);",
             "setIntensity(1);", f"drawSpot({x_um:.4f},{y_um:.4f},{diameter_um:.4f});", "proceedPhp();",
             "var date = new Date();", "var curDate = null;",      # DHgui waits 1 s before saving
             "do { curDate = new Date(); }", "while(curDate-date < 1000);",
             "saveProfile(0);", "deleteProfile(0);"]
    return "\n".join(lines) + "\n"


def load_script(png_path) -> str:
    """Show a saved phase mask on the SLM (Plerion modules/slm.py), Wavefront's pattern list emptied first: the
    loaded mask is then its only pattern, ID 0."""
    escaped = str(png_path).replace("\\", "\\\\")
    return f'{EMPTY_LIST}\ncleanAllBlob();\nloadProfile("{escaped}", 2);\n'


def field_fit(x_um: float, y_um: float, diameter_um: float) -> str:
    """Where a spot falls in Wavefront's square: "in", "partly" (cut by its edge) or "out"."""
    far, r = max(abs(x_um), abs(y_um)), diameter_um / 2
    return "in" if far + r <= FIELD_HALF_UM else "out" if far - r >= FIELD_HALF_UM else "partly"


def field_outline() -> list[tuple[str, np.ndarray]]:
    """Wavefront's field as Wavefront draws it, (kind, polyline of (x, y) µm): its square, its 20 % ("outer") and
    90 % ("inner") diffraction efficiency circles, its centre cross."""
    h, c = FIELD_HALF_UM, CROSS_HALF_UM
    t = np.linspace(0, 2 * np.pi, 181)
    circle = np.column_stack([np.cos(t), np.sin(t)])
    return [("square", np.array([(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)])),
            ("outer", EFFICIENCY_RADII_UM[20] * circle), ("inner", EFFICIENCY_RADII_UM[90] * circle),
            ("cross", np.array([(-c, 0), (c, 0)])), ("cross", np.array([(0, -c), (0, c)]))]


# ----------------------------------------------------------------------------- mask files
def mask_png(output_dir, profile: str) -> Path:
    return Path(output_dir) / f"{profile}{MASK_SUFFIX}"


def natural_key(path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(path).name)]


def old_masks(output_dir) -> list[Path]:
    """Every file Wavefront wrote for profiles Pattern<n> (png, xml...): what Plerion counts as spots."""
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    rx = re.compile(rf"^{PREFIX}\d+_000\.")
    return sorted((p for p in folder.iterdir() if p.is_file() and rx.match(p.name)), key=natural_key)


def available_masks(output_dir) -> list[int]:
    """Numbers n of the Pattern<n>_000.algoPhp.png masks in the folder, in order."""
    rx = re.compile(rf"^{PREFIX}(\d+){re.escape(MASK_SUFFIX)}$")
    folder = Path(output_dir)
    found = (rx.match(p.name) for p in folder.iterdir()) if folder.is_dir() else ()
    return sorted(int(m.group(1)) for m in found if m)


def mask_saved(png, since: float) -> bool:
    """The mask file exists, is not empty and was written after ``since`` (a time.time())."""
    try:
        st = Path(png).stat()
    except OSError:
        return False
    return st.st_size > 0 and st.st_mtime >= since - 1


def drawn_spot(output_dir, profile: str, diameter_um: float) -> tuple[float, float] | None:
    """Centre (x, y) µm of what Wavefront drew for ``profile``; (nan, nan) when nothing is drawn, None when there
    is no ``rawSrc.png`` to check.

    rawSrc.png is the drawn pattern, white on black, with (0, 0) µm at the image centre and y up; its µm per
    pixel comes from the disc's own area, the diameter asked for being known.
    """
    try:
        img = load_gray(Path(output_dir) / f"{profile}{SRC_SUFFIX}")
    except Exception:  # noqa: BLE001
        return None
    ys, xs = np.nonzero(img > img.max() / 2) if img.max() > 0 else ((), ())
    if len(xs) == 0:
        return float("nan"), float("nan")
    h, w = img.shape
    um_px = diameter_um / (2 * np.sqrt(len(xs) / np.pi))
    return float((xs.mean() + 0.5 - w / 2) * um_px), float((h / 2 - ys.mean() - 0.5) * um_px)


def off_spot(drawn, x_um: float, y_um: float, diameter_um: float) -> bool:
    """A drawn spot (or nothing drawn) more than half a diameter, 3 µm at least, from (x_um, y_um)."""
    return not np.hypot(drawn[0] - x_um, drawn[1] - y_um) <= max(3.0, diameter_um / 2)


def files_in(folder) -> list[Path]:
    """The files (not the sub-folders) of ``folder``, in natural order."""
    folder = Path(folder)
    return sorted((p for p in folder.iterdir() if p.is_file()), key=natural_key) if folder.is_dir() else []


def empty_folder(folder) -> list[str]:
    """Delete the files (not the sub-folders) of ``folder``; returns their names."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    removed = []
    for p in sorted(folder.iterdir(), key=natural_key):
        if p.is_file():
            p.unlink()
            removed.append(p.name)
    return removed


def date_range(paths) -> tuple[_dt.date, _dt.date] | None:
    """Oldest and newest modification date of ``paths``."""
    dates = [_dt.date.fromtimestamp(Path(p).stat().st_mtime) for p in paths]
    return (min(dates), max(dates)) if dates else None


def write_spot_record(output_dir, spots_px, spots_um, diameter_um: float, cal: Calibration) -> Path:
    """``Pattern_spots.json`` next to the masks: which spot each Pattern<n> mask is."""
    path = Path(output_dir) / RECORD_NAME
    d = {"created": _dt.datetime.now().isoformat(timespec="seconds"), "diameter_um": diameter_um,
         "calibration": cal.to_dict(),
         "masks": [{"mask": mask_png(output_dir, mask_name(k + 1)).name, "x_px": float(p[0]),
                    "y_px": float(p[1]), "x_um": float(u[0]), "y_um": float(u[1])}
                   for k, (p, u) in enumerate(zip(spots_px, spots_um))]}
    path.write_text(json.dumps(d, indent=2))
    return path


def recorded_spot(output_dir, n: int) -> dict | None:
    """Where the spot of mask Pattern<n> was clicked (x_px, y_px, diameter_um), from ``Pattern_spots.json``.

    None without a record for it, or when the mask is newer than the record (made again since, e.g. by DHgui).
    """
    record, png = Path(output_dir) / RECORD_NAME, mask_png(output_dir, mask_name(n))
    try:
        if png.stat().st_mtime > record.stat().st_mtime + 1:
            return None
        d = json.loads(record.read_text())
        spot = next(m for m in d["masks"] if m["mask"] == png.name)
        return {"x_px": float(spot["x_px"]), "y_px": float(spot["y_px"]), "diameter_um": float(d["diameter_um"])}
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


# ----------------------------------------------------------------------------- client
class WavefrontBusy(RuntimeError):
    pass


class WavefrontClient:
    """Drop scripts into the Wavefront script folder; ``done()`` once Wavefront took the last one."""

    def __init__(self, script_dir):
        self.script_dir = Path(script_dir)

    @property
    def script_path(self) -> Path:
        return self.script_dir / SCRIPT_NAME

    @property
    def busy(self) -> bool:
        return self.script_path.exists()

    def done(self) -> bool:
        return not self.busy

    def purge(self) -> list[str]:
        """Delete every file left in the script folder (Wavefront sometimes leaves a script behind)."""
        return empty_folder(self.script_dir)

    def submit(self, js: str):
        if not self.script_dir.is_dir():
            raise FileNotFoundError(f"Wavefront script folder not found: {self.script_dir}")
        if self.busy:
            raise WavefrontBusy(f"{self.script_path} is still pending: is Wavefront IV running "
                                f"with script auto-load on?")
        part = self.script_dir / (SCRIPT_NAME + ".part")
        with open(part, "w") as f:          # written whole then renamed: Wavefront never reads half a script
            f.write(js)
        os.replace(part, self.script_path)
        with open(self.script_dir / TRIGGER_NAME, "w"):
            pass
