"""Descriptive statistics for recorded sessions.

Angular speed and acceleration are derived from the quaternion geodesic angle
between consecutive samples, not from a sum of Euler-angle differences: the
latter inflates near gimbal lock and jumps by 360° at the ±180° wrap.

Sampling artefacts are rejected before differentiating. Providers deliver
samples in bursts and their timestamps jitter, so consecutive recorded samples
are sometimes only a fraction of a millisecond apart while the pose has moved
by a normal per-sample amount. Dividing a real rotation by such an interval
produces speeds of thousands of deg/s that no head can reach. Intervals
shorter than `MIN_INTERVAL_FRACTION` of the session's median interval are
therefore skipped rather than differentiated.
"""

from __future__ import annotations

import numpy as np

from core import quaternion_math as qm
from core.recorder import RecordedSample


class SessionStats:
    """Compute kinematics statistics from a recorded session."""

    #: Intervals shorter than this fraction of the median interval are treated
    #: as delivery jitter (two samples of the same instant) and skipped.
    MIN_INTERVAL_FRACTION = 0.5

    def __init__(self, samples: list[RecordedSample]):
        self.samples = samples
        if len(samples) < 2:
            raise ValueError("Need at least 2 samples to compute statistics")

    # -- time base and per-sample series ---------------------------------

    def times(self) -> np.ndarray:
        """Sample times on the most consistent clock available.

        Provider timestamps are preferred (they time the pose measurement
        itself). If they are not one consistent clock - going backwards, or
        jumping by over an hour, as in a file holding several providers' runs -
        the shared `host_time` is used instead when every sample has one.
        """
        ts = np.array([s.timestamp for s in self.samples], dtype=float)
        hosts = [getattr(s, "host_time", None) for s in self.samples]
        steps = np.diff(ts)
        if all(h is not None for h in hosts) and len(steps) and (steps.min() < 0 or steps.max() > 3600):
            return np.array(hosts, dtype=float)
        return ts

    def _usable_intervals(self) -> tuple[np.ndarray, np.ndarray]:
        """(interval durations, index of each interval's first sample).

        Excludes non-positive intervals and burst samples (see module docstring).
        """
        times = self.times()
        dt = np.diff(times)
        positive = dt > 0
        if not positive.any():
            return np.zeros(0), np.zeros(0, dtype=int)
        threshold = self.MIN_INTERVAL_FRACTION * float(np.median(dt[positive]))
        usable = positive & (dt >= threshold)
        return dt[usable], np.flatnonzero(usable)

    def angular_speed_series(self) -> tuple[np.ndarray, np.ndarray]:
        """(sample times, angular speed in deg/s) over the usable intervals.

        Speed is the geodesic rotation between the interval's two samples
        divided by its duration, reported at the later sample's time.
        """
        dt, idx = self._usable_intervals()
        if len(idx) == 0:
            return np.zeros(0), np.zeros(0)
        quaternions = np.array([s.quaternion for s in self.samples], dtype=float)
        rotation_deg = np.degrees(qm.angle_between(quaternions[idx], quaternions[idx + 1]))
        return self.times()[idx + 1], np.atleast_1d(rotation_deg) / dt

    def angular_acceleration_series(self) -> tuple[np.ndarray, np.ndarray]:
        """(sample times, |change in angular speed| in deg/s²).

        Differentiated across consecutive usable intervals only, so a skipped
        burst sample doesn't create a spurious step.
        """
        times, speeds = self.angular_speed_series()
        if len(speeds) < 2:
            return np.zeros(0), np.zeros(0)
        dt = np.diff(times)
        usable = dt > 0
        if not usable.any():
            return np.zeros(0), np.zeros(0)
        return times[1:][usable], np.abs(np.diff(speeds))[usable] / dt[usable]

    def _per_sample(self, times: np.ndarray, values: np.ndarray) -> np.ndarray:
        """Expand a series reported at `times` to one value per sample.

        Samples before the first value, and those whose interval was skipped,
        carry the previous value (0 at the start) so plots stay continuous.
        """
        out = np.zeros(len(self.samples))
        if not len(times):
            return out
        index = np.searchsorted(self.times(), times)
        out[index] = values
        # Forward-fill the gaps left by skipped intervals.
        filled = np.maximum.accumulate(np.where(np.isin(np.arange(len(out)), index), np.arange(len(out)), -1))
        return np.where(filled >= 0, out[filled], 0.0)

    def angular_speed_per_sample(self) -> np.ndarray:
        """Angular speed (deg/s) aligned one-to-one with `self.samples`."""
        return self._per_sample(*self.angular_speed_series())

    def angular_acceleration_per_sample(self) -> np.ndarray:
        """Angular acceleration (deg/s²) aligned one-to-one with `self.samples`."""
        return self._per_sample(*self.angular_acceleration_series())

    @staticmethod
    def _spread(values: np.ndarray) -> dict[str, float]:
        if not len(values):
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {"min": float(values.min()), "max": float(values.max()), "mean": float(values.mean())}

    def sampling_stats(self) -> dict[str, float]:
        """Sample rate and how many intervals were rejected as delivery bursts."""
        dt = np.diff(self.times())
        positive = dt[dt > 0]
        if not len(positive):
            return {"median_interval_s": 0.0, "rate_hz": 0.0, "interval_count": 0, "rejected_count": 0}
        median = float(np.median(positive))
        threshold = self.MIN_INTERVAL_FRACTION * median
        return {
            "median_interval_s": median,
            "rate_hz": 1.0 / median if median > 0 else 0.0,
            "interval_count": int(len(dt)),
            "rejected_count": int(np.count_nonzero(dt < threshold)),
        }

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
        """Min/max/mean angular speed magnitude (deg/s)."""
        return self._spread(self.angular_speed_series()[1])

    def angular_acceleration_stats(self) -> dict[str, float]:
        """Min/max/mean angular acceleration magnitude (deg/s²)."""
        return self._spread(self.angular_acceleration_series()[1])

    def session_duration(self) -> float:
        """Total session duration in seconds."""
        times = self.times()
        return float(times[-1] - times[0])
