"""Holographic (SLM) stimulation: spots on the camera image -> Wavefront IV coordinates in µm.

The transform is a port of DHgui.m (DefineEnsembleBtn_Callback), the MATLAB GUI that produced the
phase masks so far, so calibrations typed there (DHparams.m) keep their meaning: centre on the
image, mirror y, rotate, scale by the camera pixel size, then shift by (ΔX, ΔY).
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .calibration import DatedMixin
from .geometry import Circle
from .patterns import _safe

HOLO_SUFFIX = ".holo.json"


@dataclass
class HoloCalibration(DatedMixin):
    pixel_size_um: float = 0.67      # µm per camera pixel on the sample
    delta_x_um: float = 5.0          # shift of the pattern in the SLM frame
    delta_y_um: float = -8.0
    theta_deg: float = 0.0           # rotation
    flip_y: bool = True              # camera y points down, Wavefront y up
    flip_x: bool = False
    created: str = ""                # ISO date-time
    path: str = field(default="", compare=False)    # where it was saved / loaded (not serialised)

    def um_to_camera_px(self, um: float) -> float:
        return um / self.pixel_size_um

    def camera_px_to_um(self, px: float) -> float:
        return px * self.pixel_size_um

    def _linear(self) -> np.ndarray:
        """Centred camera px -> µm: mirror, then rotate by -θ (as DHgui's DefineEnsemble), then scale.

        DHgui's GenerateOnePattern rotated by +θ instead; θ was always 0 there so both agreed.
        """
        th = np.deg2rad(-self.theta_deg)
        rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        mirror = np.diag([-1.0 if self.flip_x else 1.0, -1.0 if self.flip_y else 1.0])
        return self.pixel_size_um * rot @ mirror

    def camera_to_slm(self, pts, image_shape) -> np.ndarray:
        """Camera (x, y) image px -> Wavefront (x, y) µm, for an image of shape (rows, cols)."""
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        h, w = image_shape[:2]
        # pixel centres sit at +0.5 here and on integers in MATLAB (1-based): x_matlab = x + 0.5
        centred = pts + 0.5 - np.array([w / 2, h / 2])
        return centred @ self._linear().T + np.array([self.delta_x_um, self.delta_y_um])

    def slm_to_camera(self, pts, image_shape) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        h, w = image_shape[:2]
        centred = (pts - np.array([self.delta_x_um, self.delta_y_um])) @ np.linalg.inv(self._linear()).T
        return centred - 0.5 + np.array([w / 2, h / 2])

    def summary(self) -> str:
        mirror = "+".join(a for a, f in (("x", self.flip_x), ("y", self.flip_y)) if f) or "none"
        return (f"{self.pixel_size_um:g} µm/px, ΔX {self.delta_x_um:g} µm, ΔY {self.delta_y_um:g} µm, "
                f"θ {self.theta_deg:g}°, mirror {mirror}")

    # -- persistence ----------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path")
        return d

    @classmethod
    def from_dict(cls, d: dict, path: str = "") -> "HoloCalibration":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "path"}
        return cls(**known, path=path)

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.created:
            self.created = _dt.datetime.now().isoformat(timespec="seconds")
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        self.path = str(path)
        return path

    @classmethod
    def load(cls, path) -> "HoloCalibration":
        with open(path) as f:
            return cls.from_dict(json.load(f), path=str(path))


@dataclass
class HoloSpot:
    name: str
    x_um: float                      # Wavefront coordinates
    y_um: float
    d_um: float                      # spot diameter
    cx: float = 0.0                  # camera px, for the record
    cy: float = 0.0


def holo_spots(shapes, cal: HoloCalibration, image_shape) -> list[HoloSpot]:
    """Enabled circles -> spots in Wavefront µm (freehand outlines have no holographic equivalent)."""
    circles = [s for s in shapes if s.enabled and isinstance(s, Circle)]
    if not circles:
        return []
    xy = cal.camera_to_slm([(c.cx, c.cy) for c in circles], image_shape)
    return [HoloSpot(c.name, float(x), float(y), cal.camera_px_to_um(2 * c.r), float(c.cx), float(c.cy))
            for c, (x, y) in zip(circles, xy)]


def write_holo_record(directory, name: str, spots, cal: HoloCalibration, image_path="", image_shape=(),
                      masks=()) -> Path:
    """Save what was sent to Wavefront: spots, calibration, image and the phase mask of each spot."""
    path = Path(directory) / f"{_safe(name)}{HOLO_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"name": name,
         "created": _dt.datetime.now().isoformat(timespec="seconds"),
         "image_path": str(image_path),
         "image_shape": [int(v) for v in image_shape[:2]],
         "calibration": cal.to_dict(),
         "calibration_path": cal.path,
         "spots": [asdict(s) for s in spots],
         "masks": list(masks)}
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
    return path
