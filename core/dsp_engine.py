"""Absolute mapping DSP engine (spec section 7, headmouse_dsp.py).

Converts a raw head-pose quaternion into cursor pixel coordinates. Mode A
(absolute mapping) only in this milestone; Mode B (velocity mapping) and the
filter stages (One Euro, auto-zero bias, yaw drift correction) are added in
later milestones per the plan's incremental-vertical-slice sequencing.
"""

from __future__ import annotations

import numpy as np

from core import quaternion_math as qm


class DSPEngine:
    """Maps a relative head-pose quaternion to an absolute (pixel_x, pixel_y).

    Owns ``q_neutral`` and the calibration relation from spec section 3:
    ``q_relative = q_neutral^-1 ⊗ q_current``.
    """

    def __init__(self, kx: float, ky: float, screen_width: int, screen_height: int):
        self.kx = kx
        self.ky = ky
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._q_neutral = qm.IDENTITY_QUATERNION.copy()

    @classmethod
    def from_config(cls, dsp_config: dict, screen_width: int, screen_height: int) -> "DSPEngine":
        absolute = dsp_config["absolute"]
        return cls(kx=absolute["kx"], ky=absolute["ky"], screen_width=screen_width, screen_height=screen_height)

    def recenter(self, q_current: np.ndarray) -> None:
        """Capture the current pose as the new neutral posture (the "Recenter" shortcut, spec 7.1)."""
        self._q_neutral = np.asarray(q_current, dtype=np.float64).copy()

    def set_screen_geometry(self, width: int, height: int) -> None:
        self.screen_width = width
        self.screen_height = height

    def relative_euler_deg(self, q_current: np.ndarray) -> tuple[float, float, float]:
        """Return (yaw, pitch, roll) in degrees, relative to the current q_neutral."""
        q_relative = qm.relative_rotation(self._q_neutral, q_current)
        yaw, pitch, roll = qm.to_euler_zyx(q_relative)
        return float(np.degrees(yaw)), float(np.degrees(pitch)), float(np.degrees(roll))

    def process_absolute(self, q_current: np.ndarray) -> tuple[float, float]:
        """Map a raw head-pose quaternion to a clamped absolute (pixel_x, pixel_y).

        Extracts yaw and pitch directly from the relative quaternion (continuous),
        avoiding Euler angle discontinuities at ±180°.

        Pixel_x = ScreenWidth/2 + kx * Yaw_relative
        Pixel_y = ScreenHeight/2 + ky * Pitch_relative
        """
        q_relative = qm.relative_rotation(self._q_neutral, q_current)

        # Extract yaw and pitch directly from quaternion to avoid Euler discontinuities
        # Yaw: rotation about Z axis
        # For q=[w,x,y,z], yaw ≈ 2*atan2(z, w) when z-axis dominates
        w, x, y, z = q_relative

        # Yaw from quaternion (rotation about Z)
        yaw_rad = 2.0 * np.arctan2(z, w)

        # Pitch from quaternion (rotation about Y)
        # Use sin(pitch) = 2(w*y - z*x) clamped to [-1, 1]
        pitch_sin = np.clip(2.0 * (w * y - z * x), -1.0, 1.0)
        pitch_rad = np.arcsin(pitch_sin)

        yaw_deg = float(np.degrees(yaw_rad))
        pitch_deg = float(np.degrees(pitch_rad))

        pixel_x = self.screen_width / 2.0 + self.kx * yaw_deg
        pixel_y = self.screen_height / 2.0 + self.ky * pitch_deg

        pixel_x = float(np.clip(pixel_x, 0, self.screen_width - 1))
        pixel_y = float(np.clip(pixel_y, 0, self.screen_height - 1))
        return pixel_x, pixel_y
