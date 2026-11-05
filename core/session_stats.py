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

        Keep dt > 0 and dt >= 0.5 * median(all positive dt), in seconds.
        Return empty arrays when there are no positive intervals. No angular
        outlier threshold or smoothing is applied.
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
        Rotation = degrees(2*acos(abs(dot(normalized q1, normalized q2)))).
        Only intervals accepted by _usable_intervals contribute.
        """
        dt, idx = self._usable_intervals()
        if len(idx) == 0:
            return np.zeros(0), np.zeros(0)
        quaternions = np.array([s.quaternion for s in self.samples], dtype=float)
        rotation_deg = np.degrees(qm.angle_between(quaternions[idx], quaternions[idx + 1]))
        return self.times()[idx + 1], np.atleast_1d(rotation_deg) / dt

    def angular_acceleration_series(self) -> tuple[np.ndarray, np.ndarray]:
        """(sample times, |change in angular speed| in deg/s²).

        For adjacent returned speed values, compute abs(v[i+1]-v[i]) divided
        by their positive timestamp difference. This is change in scalar
        speed, not vector angular acceleration. A rejected burst interval
        contributes no speed value, so the timestamp difference can span it.
        Notebook exclusion gaps are split by its caller before this function.
        Fewer than two speed values yield empty arrays.
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
        """Return min, max and unweighted arithmetic mean; empty arrays give zeros."""
        if not len(values):
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {"min": float(values.min()), "max": float(values.max()), "mean": float(values.mean())}

    def sampling_stats(self) -> dict[str, float]:
        """Return median positive dt and rate = 1/median(dt), in Hz.

        Count all consecutive intervals and those below half that median
        (including non-positive intervals). If no positive dt exists, all
        fields return 0 under the existing convention.
        """
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

        Convert recorded degree fields to radians, unwrap each axis in sample
        order, then convert back. Each range is max-min, not a percentile or
        a span across participants. These fields are used as supplied; no
        calibration/reference transform or phase/exclusion filter occurs here.
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
        """Min/max/arithmetic mean of usable interval speeds, in deg/s.

        Intervals are equally weighted, not weighted by duration; empty series
        give zeros through _spread. Plot forward-filled values are not used.
        """
        return self._spread(self.angular_speed_series()[1])

    def angular_acceleration_stats(self) -> dict[str, float]:
        """Min/max/arithmetic mean of scalar speed-change magnitudes, in deg/s².

        Uses the derivative series, not plot forward-filled values; empty
        series give zeros through _spread.
        """
        return self._spread(self.angular_acceleration_series()[1])

    def session_duration(self) -> float:
        """Return last minus first selected-clock time in seconds, including gaps."""
        times = self.times()
        return float(times[-1] - times[0])
