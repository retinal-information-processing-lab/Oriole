"""Manual calibration: camera image pixels -> µm in the stimulation plane (DMD or Wavefront).

    µm = R(-rot) · diag(stretch_x, stretch_y) · (p + 0.5 - image centre) + (x, y)

This is DHgui's transform (pixel centres at +0.5 as MATLAB's 1-based pixels, rotation by -θ),
with its fixed mirror y and pixel size split into two stretches: a negative stretch mirrors.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

EPI_SIZE = (672, 512)       # width, height of the epi camera images (binned 2x2) both calibrations are made for


@dataclass
class Calibration:
    x_um: float = 0.0           # shift of the pattern
    y_um: float = 0.0
    rot_deg: float = 0.0        # rotation
    stretch_x: float = 0.67     # µm per camera pixel along the image x; negative = mirror
    stretch_y: float = 0.67

    def _linear(self) -> np.ndarray:
        th = np.deg2rad(-self.rot_deg)
        rot = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        return rot @ np.diag([self.stretch_x, self.stretch_y])

    def to_um(self, points, image_shape) -> np.ndarray:
        """Camera (x, y) px of an image of shape (rows, cols) -> (x, y) µm, shape (N, 2)."""
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        h, w = image_shape[:2]
        return (pts + 0.5 - [w / 2, h / 2]) @ self._linear().T + [self.x_um, self.y_um]

    def from_um(self, points, image_shape) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        h, w = image_shape[:2]
        return (pts - [self.x_um, self.y_um]) @ np.linalg.inv(self._linear()).T - 0.5 + [w / 2, h / 2]

    @property
    def px_um(self) -> float:
        """Mean µm per camera pixel (to draw spot sizes on the image)."""
        return (abs(self.stretch_x) + abs(self.stretch_y)) / 2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None, default: "Calibration") -> "Calibration":
        d = d or {}
        return cls(**{k: float(d.get(k, v)) for k, v in asdict(default).items()})


VISUAL_DEFAULT = Calibration(0.0, 0.0, 0.0, 0.67, 0.67)
HOLO_DEFAULT = Calibration(5.0, -8.0, 0.0, 0.67, -0.67)     # DHparams.m: ΔX 5, ΔY -8, 0.67 µm/px, mirror y
