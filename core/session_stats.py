"""Descriptive statistics for recorded sessions."""

from __future__ import annotations

import numpy as np

from core import quaternion_math as qm
from core.recorder import RecordedSample


class SessionStats:
    """Compute kinematics statistics from a recorded session."""

    def __init__(self, samples: list[RecordedSample]):
        self.samples = samples
        if len(samples) < 2:
            raise ValueError("Need at least 2 samples to compute statistics")

    def cervical_rom(self) -> dict[str, float]:
        """Compute cervical Range of Motion: min/max for yaw, pitch, roll (degrees).

        Unwraps angles to handle ±180° discontinuities correctly.
        """
        yaws = np.array([s.yaw_deg for s in self.samples])
        pitches = np.array([s.pitch_deg for s in self.samples])
        rolls = np.array([s.roll_deg for s in self.samples])

        # Unwrap to handle ±180° discontinuities
        yaws_unwrapped = np.unwrap(np.radians(yaws)) * 180 / np.pi
        pitches_unwrapped = np.unwrap(np.radians(pitches)) * 180 / np.pi
        rolls_unwrapped = np.unwrap(np.radians(rolls)) * 180 / np.pi

        return {
            "yaw_min": float(np.min(yaws_unwrapped)),
            "yaw_max": float(np.max(yaws_unwrapped)),
            "yaw_range": float(np.max(yaws_unwrapped) - np.min(yaws_unwrapped)),
            "pitch_min": float(np.min(pitches_unwrapped)),
            "pitch_max": float(np.max(pitches_unwrapped)),
            "pitch_range": float(np.max(pitches_unwrapped) - np.min(pitches_unwrapped)),
            "roll_min": float(np.min(rolls_unwrapped)),
            "roll_max": float(np.max(rolls_unwrapped)),
            "roll_range": float(np.max(rolls_unwrapped) - np.min(rolls_unwrapped)),
        }

    def angular_velocity_stats(self) -> dict[str, float]:
        """Compute min/max angular velocity magnitude (deg/s) via finite difference of Euler angles."""
        if len(self.samples) < 2:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}

        velocities = []
        for i in range(1, len(self.samples)):
            dt = self.samples[i].timestamp - self.samples[i - 1].timestamp
            if dt <= 0:
                continue

            dyaw = self.samples[i].yaw_deg - self.samples[i - 1].yaw_deg
            dpitch = self.samples[i].pitch_deg - self.samples[i - 1].pitch_deg
            droll = self.samples[i].roll_deg - self.samples[i - 1].roll_deg

            omega_mag = np.sqrt(dyaw**2 + dpitch**2 + droll**2) / dt
            velocities.append(omega_mag)

        if not velocities:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}

        return {
            "min": float(np.min(velocities)),
            "max": float(np.max(velocities)),
            "mean": float(np.mean(velocities)),
        }

    def angular_acceleration_stats(self) -> dict[str, float]:
        """Compute min/max angular acceleration (deg/s²) via second derivative."""
        if len(self.samples) < 3:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}

        accelerations = []
        for i in range(2, len(self.samples)):
            dt1 = self.samples[i - 1].timestamp - self.samples[i - 2].timestamp
            dt2 = self.samples[i].timestamp - self.samples[i - 1].timestamp
            if dt1 <= 0 or dt2 <= 0:
                continue

            dyaw1 = self.samples[i - 1].yaw_deg - self.samples[i - 2].yaw_deg
            dpitch1 = self.samples[i - 1].pitch_deg - self.samples[i - 2].pitch_deg
            droll1 = self.samples[i - 1].roll_deg - self.samples[i - 2].roll_deg
            omega1 = np.sqrt(dyaw1**2 + dpitch1**2 + droll1**2) / dt1

            dyaw2 = self.samples[i].yaw_deg - self.samples[i - 1].yaw_deg
            dpitch2 = self.samples[i].pitch_deg - self.samples[i - 1].pitch_deg
            droll2 = self.samples[i].roll_deg - self.samples[i - 1].roll_deg
            omega2 = np.sqrt(dyaw2**2 + dpitch2**2 + droll2**2) / dt2

            alpha = abs(omega2 - omega1) / ((dt1 + dt2) / 2.0)
            accelerations.append(alpha)

        if not accelerations:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}

        return {
            "min": float(np.min(accelerations)),
            "max": float(np.max(accelerations)),
            "mean": float(np.mean(accelerations)),
        }

    def session_duration(self) -> float:
        """Total session duration in seconds."""
        return float(self.samples[-1].timestamp - self.samples[0].timestamp)
