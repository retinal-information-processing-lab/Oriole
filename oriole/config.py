"""Rig configuration (DMD, Wavefront IV, shutter) shared by all modules."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".oriole" / "config.json"
LEGACY_CONFIG_PATH = Path.home() / ".aule" / "config.json"    # read once when the default is missing


@dataclass
class Config:
    rig_id: int = 3
    dmd_height: int = 672          # rows of the frame array
    dmd_width: int = 672           # columns of the frame array
    pixel_size_um: float = 2.5     # size of one DMD frame pixel on the sample
    last_calibration: str = ""     # path of the last saved calibration JSON
    calibration_max_age_days: int = 30   # warn in the GUI when the calibration is older
    last_dir: str = ""             # last directory used in file dialogs
    mode: str = "visual"           # "visual" (DMD frames) | "holo" (SLM phase masks)
    last_holo_calibration: str = ""      # path of the last holo calibration JSON
    wfd_script_dir: str = r"E:\wfdIV_20261022\script"                  # folder Wavefront IV watches
    wfd_output_dir: str = r"E:\wfdIV_20261022\GeneratedPhasemasks"     # where it saves the phase masks
    wfd_profile_prefix: str = "Pattern"  # masks saved as <prefix><n>_000.algoPhp.png (what Plerion counts)
    wfd_timeout_s: float = 120.0   # give up waiting for Wavefront after this long per script
    nidaq_device: str = "Dev1"     # NI-DAQ device driving the shutter (as in Plerion)
    shutter_ao: int = 0            # its analog output channel
    shutter_open_v: float = 10.0   # voltage that opens the shutter; 0 V closes it
    path: str = field(default="", compare=False)   # where this config is saved (not serialised)

    @property
    def dmd_shape(self) -> tuple[int, int]:
        return (self.dmd_height, self.dmd_width)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
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
