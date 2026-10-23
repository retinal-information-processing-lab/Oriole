"""Wavefront IV (wfdIV) scripting by file drop, the way DHgui.m and Plerion drive it.

wfdIV watches its ``script`` folder (script auto-load on): a ``test.js`` followed by an empty
``zdone.js`` makes it run the script, then the files are removed. Spot scripts follow DHgui.m;
loading a saved phase mask follows Plerion's modules/slm.py.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

SCRIPT_NAME = "test.js"
TRIGGER_NAME = "zdone.js"
MASK_SUFFIX = "_000.algoPhp.png"     # wfdIV saves profile <name> as <name>_000.algoPhp.png (+ siblings)
LIVE_PROFILE = "Live"


# ----------------------------------------------------------------------------- scripts
def _num(v: float) -> str:
    return f"{v:.4f}"


def _draw(spots) -> list[str]:
    return [f"drawSpot({_num(s.x_um)},{_num(s.y_um)},{_num(s.d_um)});" for s in spots]


def live_script(spots) -> str:
    """Compute and display one pattern with all spots, without saving it (DHgui GenerateOnePattern)."""
    lines = ["cleanAllBlob();", "selectTool(2);", "deleteProfile(0);", f'setProfileName("{LIVE_PROFILE}");',
             "selectTool(2);", "setIntensity(0.9999);", *_draw(spots), "proceedPhp();", "cleanAllBlob();"]
    return "\n".join(lines) + "\n"


def mask_script(profile: str, spot) -> str:
    """Compute and save the phase mask of one spot at full power (DHgui GeneratePhaseMasks)."""
    lines = ["cleanAllBlob();", "selectTool(2);", f'setProfileName("{profile}");', "selectTool(2);",
             "setIntensity(1);", *_draw([spot]), "proceedPhp();",
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


def list_masks(folder) -> list[Path]:
    """Saved phase masks (*.algoPhp.png) in natural order (Pattern2 before Pattern10)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.algoPhp.png"), key=natural_key)


def old_masks(output_dir, prefix: str) -> list[Path]:
    """Every file wfdIV wrote for profiles <prefix><n> (png, xml...): what Plerion counts as spots."""
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    rx = re.compile(rf"^{re.escape(prefix)}\d+_000\.")
    return sorted((p for p in folder.iterdir() if p.is_file() and rx.match(p.name)), key=natural_key)


def date_range(paths) -> tuple[_dt.date, _dt.date] | None:
    """Oldest and newest modification date of ``paths``."""
    dates = [_dt.date.fromtimestamp(Path(p).stat().st_mtime) for p in paths]
    return (min(dates), max(dates)) if dates else None


# ----------------------------------------------------------------------------- client
class WavefrontBusy(RuntimeError):
    pass


class WavefrontClient:
    """Drop scripts into the wfdIV script folder; ``done()`` once wfdIV has consumed the last one."""

    def __init__(self, script_dir):
        self.script_dir = Path(script_dir)

    @property
    def available(self) -> bool:
        return self.script_dir.is_dir()

    @property
    def script_path(self) -> Path:
        return self.script_dir / SCRIPT_NAME

    @property
    def busy(self) -> bool:
        return self.script_path.exists()

    def done(self) -> bool:
        return not self.busy

    def submit(self, js: str):
        if not self.available:
            raise FileNotFoundError(f"Wavefront script folder not found: {self.script_dir}")
        if self.busy:
            raise WavefrontBusy(f"{self.script_path} is still pending: is Wavefront IV running "
                                f"with script auto-load on?")
        part = self.script_dir / (SCRIPT_NAME + ".part")
        with open(part, "w") as f:          # written whole then renamed: wfdIV never reads half a script
            f.write(js)
        os.replace(part, self.script_path)
        with open(self.script_dir / TRIGGER_NAME, "w"):
            pass
