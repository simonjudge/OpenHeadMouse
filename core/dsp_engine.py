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

        Pixel_x = ScreenWidth/2 + kx * Yaw_relative
        Pixel_y = ScreenHeight/2 + ky * Pitch_relative
        (spec 7.1, angles in degrees, relative to q_neutral). To make "look
        up" move the cursor toward the top of the screen (smaller pixel_y in
        the usual top-left-origin screen convention), given this project's
        "pitch up = positive" convention (core/quaternion_math.py), ``ky``
        should be configured negative.
        """
        yaw_deg, pitch_deg, _roll_deg = self.relative_euler_deg(q_current)

        pixel_x = self.screen_width / 2.0 + self.kx * yaw_deg
        pixel_y = self.screen_height / 2.0 + self.ky * pitch_deg

        pixel_x = float(np.clip(pixel_x, 0, self.screen_width - 1))
        pixel_y = float(np.clip(pixel_y, 0, self.screen_height - 1))
        return pixel_x, pixel_y
