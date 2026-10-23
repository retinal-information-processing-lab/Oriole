"""Settings (DMD, Wavefront IV, shutter) and the two calibrations, kept in ~/.oriole/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .calibration import HOLO_DEFAULT, VISUAL_DEFAULT, Calibration

DEFAULT_CONFIG_PATH = Path.home() / ".oriole" / "config.json"
LEGACY_CONFIG_PATH = Path.home() / ".aule" / "config.json"    # read once when the default is missing


@dataclass
class Config:
    dmd_height: int = 672          # rows of the frame array
    dmd_width: int = 672           # columns of the frame array
    pixel_size_um: float = 2.5     # size of one DMD frame pixel on the sample
    spot_diameter_um: float = 10.0
    visual_cal: dict = field(default_factory=VISUAL_DEFAULT.to_dict)   # camera -> DMD µm
    holo_cal: dict = field(default_factory=HOLO_DEFAULT.to_dict)       # camera -> Wavefront µm
    last_dir: str = ""             # last directory used in file dialogs
    linked_bin: str = ""           # last bin the spot bin was merged into
    wfd_root: str = r"E:\wfdIV_20261022"    # Wavefront IV folder, holding script\ and GeneratedPhasemasks\
    nidaq_device: str = "Dev1"     # NI-DAQ device driving the shutter (as in Plerion)
    shutter_ao: int = 0            # its analog output channel
    show_slm_field: bool = True    # Wavefront's field (square, no-go zone, power circles) drawn on the image
    path: str = field(default="", compare=False)   # where this config is saved (not serialised)

    @property
    def dmd_shape(self) -> tuple[int, int]:
        return (self.dmd_height, self.dmd_width)

    @property
    def visual(self) -> Calibration:
        return Calibration.from_dict(self.visual_cal, VISUAL_DEFAULT)

    @property
    def holo(self) -> Calibration:
        return Calibration.from_dict(self.holo_cal, HOLO_DEFAULT)

    @property
    def wfd_script_dir(self) -> str:
        """Folder Wavefront watches for scripts."""
        return str(Path(self.wfd_root) / "script")

    @property
    def wfd_output_dir(self) -> str:
        """Folder Wavefront saves the phase masks in."""
        return str(Path(self.wfd_root) / "GeneratedPhasemasks")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        d = dict(d)
        if "wfd_root" not in d and d.get("wfd_script_dir"):     # older configs kept both folders
            d["wfd_root"] = str(Path(d["wfd_script_dir"]).parent)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "path"}
        return cls(**known)

    def save(self, path: os.PathLike | str | None = None) -> Path:
        """Save to ``path``, else to where it was loaded from, else the default location."""
        path = Path(path or self.path or DEFAULT_CONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        self.path = str(path)
        return path

    @classmethod
    def load(cls, path: os.PathLike | str | None = None) -> "Config":
        """Load ``path`` (default location if None). The default falls back to the pre-rename ~/.aule file."""
        path = Path(path or DEFAULT_CONFIG_PATH)
        src = path
        if not src.exists() and path == DEFAULT_CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
            src = LEGACY_CONFIG_PATH
        if not src.exists():
            return cls(path=str(path))
        with open(src) as f:
            cfg = cls.from_dict(json.load(f))
        cfg.path = str(path)
        return cfg
