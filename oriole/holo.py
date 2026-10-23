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

from .calibration import Calibration

SCRIPT_NAME = "test.js"
TRIGGER_NAME = "zdone.js"
PREFIX = "Pattern"                   # DHgui names the mask of spot n Pattern<n> (what Plerion counts)
MASK_SUFFIX = "_000.algoPhp.png"     # Wavefront saves profile <name> as <name>_000.algoPhp.png (+ siblings)
TIMEOUT_S = 120.0                    # give up waiting for Wavefront after this long per script
NOT_READ_S = 10.0                    # a script still there after this long: auto-load is probably off


def mask_name(n: int) -> str:
    return f"{PREFIX}{n}"


# ----------------------------------------------------------------------------- scripts
def mask_script(profile: str, x_um: float, y_um: float, diameter_um: float) -> str:
    """Compute and save the phase mask of one spot at full power (DHgui GeneratePhaseMasks)."""
    lines = ["cleanAllBlob();", "selectTool(2);", f'setProfileName("{profile}");', "selectTool(2);",
             "setIntensity(1);", f"drawSpot({x_um:.4f},{y_um:.4f},{diameter_um:.4f});", "proceedPhp();",
             "var date = new Date();", "var curDate = null;",      # DHgui waits 1 s before saving
             "do { curDate = new Date(); }", "while(curDate-date < 1000);",
             "saveProfile(0);", "deleteProfile(0);"]
    return "\n".join(lines) + "\n"


def load_script(png_path) -> str:
    """Show a saved phase mask on the SLM (Plerion modules/slm.py)."""
    escaped = str(png_path).replace("\\", "\\\\")
    return f'cleanAllBlob();\ndeleteProfile(0);\nloadProfile("{escaped}", 2);\n'


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
    path = Path(output_dir) / f"{PREFIX}_spots.json"
    d = {"created": _dt.datetime.now().isoformat(timespec="seconds"), "diameter_um": diameter_um,
         "calibration": cal.to_dict(),
         "masks": [{"mask": mask_png(output_dir, mask_name(k + 1)).name, "x_px": float(p[0]),
                    "y_px": float(p[1]), "x_um": float(u[0]), "y_um": float(u[1])}
                   for k, (p, u) in enumerate(zip(spots_px, spots_um))]}
    path.write_text(json.dumps(d, indent=2))
    return path


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
