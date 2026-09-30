"""Head-pose to cursor DSP.

The engine deliberately keeps Mode A's original API, while Mode B is an
integrating velocity controller.  All angles exposed by this module are
degrees (and degrees/second for velocities).

Sign convention (identical in both modes): with POSITIVE gains the cursor
follows the head — yaw left (+) moves the cursor left and pitch up (+) moves
it up. Screen x grows rightwards and y grows downwards, hence the minus signs.
Negative gains invert an axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from core import quaternion_math as qm


@dataclass(frozen=True)
class OneEuroConfig:
    enabled: bool = False
    min_cutoff: float = 1.0
    beta: float = 0.3
    d_cutoff: float = 1.0


@dataclass(frozen=True)
class AutoZeroConfig:
    enabled: bool = False
    stationary_window_ms: float = 200.0
    stationary_threshold_deg_s: float = 2.0


@dataclass(frozen=True)
class YawDriftConfig:
    enabled: bool = False
    time_constant_s: float = 20.0


class _OneEuro:
    def __init__(self, config: OneEuroConfig):
        self.config = config
        self.x: float | None = None
        self.dx = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        if cutoff <= 0:
            return 1.0
        tau = 1.0 / (2.0 * np.pi * cutoff)
        return float(1.0 / (1.0 + tau / max(dt, 1e-9)))

    def update(self, value: float, dt: float) -> float:
        if not self.config.enabled:
            return value
        if self.x is None:
            self.x = value
            return value
        derivative = (value - self.x) / max(dt, 1e-9)
        self.dx += self._alpha(self.config.d_cutoff, dt) * (derivative - self.dx)
        cutoff = self.config.min_cutoff + self.config.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (value - self.x)
        return self.x

    def reset(self) -> None:
        self.x = None
        self.dx = 0.0


class DSPEngine:
    """Map relative head pose to pixels in absolute or velocity mode."""

    def __init__(
        self,
        kx: float,
        ky: float,
        screen_width: int,
        screen_height: int,
        origin_x: int = 0,
        origin_y: int = 0,
        mode: str = "absolute",
        velocity_kx: float = 1.0,
        velocity_ky: float = 1.0,
        gamma: float = 1.0,
        deadband_deg_s: float = 0.0,
        one_euro_filter: OneEuroConfig | None = None,
        auto_zero: AutoZeroConfig | None = None,
        yaw_drift_correction: YawDriftConfig | None = None,
    ):
        self.kx, self.ky = float(kx), float(ky)
        self.screen_width, self.screen_height = int(screen_width), int(screen_height)
        self.origin_x, self.origin_y = int(origin_x), int(origin_y)
        self.mode = mode
        self.velocity_kx, self.velocity_ky = float(velocity_kx), float(velocity_ky)
        self.gamma, self.deadband_deg_s = float(gamma), float(deadband_deg_s)
        if self.gamma <= 0:
            raise ValueError("gamma must be positive")
        self.one_euro_filter = one_euro_filter or OneEuroConfig()
        self.auto_zero = auto_zero or AutoZeroConfig()
        self.yaw_drift_correction = yaw_drift_correction or YawDriftConfig()
        self._q_neutral = qm.IDENTITY_QUATERNION.copy()
        self._last_angles: tuple[float, float] | None = None
        self._last_time: float | None = None
        self._absolute_last_time: float | None = None
        self._position = np.array([self.origin_x + self.screen_width / 2.0, self.origin_y + self.screen_height / 2.0])
        self._bias = np.zeros(2)
        self._drift_bias = 0.0
        self._stationary_time = 0.0
        self._absolute_filters = (_OneEuro(self.one_euro_filter), _OneEuro(self.one_euro_filter))
        self._velocity_filters = (_OneEuro(self.one_euro_filter), _OneEuro(self.one_euro_filter))

    @classmethod
    def from_config(cls, dsp_config: Mapping[str, object], screen_width: int, screen_height: int) -> "DSPEngine":
        absolute = dsp_config.get("absolute", {})
        velocity = dsp_config.get("velocity", {})
        euro = dsp_config.get("one_euro_filter", {})
        zero = dsp_config.get("auto_zero", {})
        drift = dsp_config.get("yaw_drift_correction", {})
        return cls(
            kx=float(absolute.get("kx", 1.0)), ky=float(absolute.get("ky", 1.0)),
            screen_width=screen_width, screen_height=screen_height,
            mode=str(dsp_config.get("mode", "absolute")),
            velocity_kx=float(velocity.get("kx", 1.0)), velocity_ky=float(velocity.get("ky", 1.0)),
            gamma=float(velocity.get("gamma", 1.0)),
            deadband_deg_s=float(velocity.get("deadband_deg_s", 0.0)),
            one_euro_filter=OneEuroConfig(**{k: euro[k] for k in ("enabled", "min_cutoff", "beta", "d_cutoff") if k in euro}),
            auto_zero=AutoZeroConfig(**{k: zero[k] for k in ("enabled", "stationary_window_ms", "stationary_threshold_deg_s") if k in zero}),
            yaw_drift_correction=YawDriftConfig(**{k: drift[k] for k in ("enabled", "time_constant_s") if k in drift}),
        )

    def recenter(self, q_current: np.ndarray) -> None:
        self._q_neutral = np.asarray(q_current, dtype=np.float64).copy()
        self.reset()

    def reset(self, position: tuple[float, float] | None = None) -> None:
        self._last_angles = None
        self._last_time = None
        self._absolute_last_time = None
        self._bias[:] = 0.0
        self._drift_bias = 0.0
        self._stationary_time = 0.0
        self._position[:] = position if position is not None else (
            self.origin_x + self.screen_width / 2.0, self.origin_y + self.screen_height / 2.0
        )
        for filt in (*self._absolute_filters, *self._velocity_filters):
            filt.reset()

    def set_screen_geometry(self, width: int, height: int, origin_x: int = 0, origin_y: int = 0) -> None:
        self.screen_width, self.screen_height = int(width), int(height)
        self.origin_x, self.origin_y = int(origin_x), int(origin_y)
        self._position[:] = (self.origin_x + width / 2.0, self.origin_y + height / 2.0)

    def set_filter_configs(
        self,
        one_euro_filter: OneEuroConfig | None = None,
        auto_zero: AutoZeroConfig | None = None,
        yaw_drift_correction: YawDriftConfig | None = None,
    ) -> None:
        if one_euro_filter is not None:
            self.one_euro_filter = one_euro_filter
            self._absolute_filters = (_OneEuro(one_euro_filter), _OneEuro(one_euro_filter))
            self._velocity_filters = (_OneEuro(one_euro_filter), _OneEuro(one_euro_filter))
        if auto_zero is not None:
            self.auto_zero = auto_zero
        if yaw_drift_correction is not None:
            self.yaw_drift_correction = yaw_drift_correction
        self.reset(position=tuple(self._position))

    def relative_euler_deg(self, q_current: np.ndarray) -> tuple[float, float, float]:
        yaw, pitch, roll = qm.to_euler_zyx(qm.relative_rotation(self._q_neutral, q_current))
        return float(np.degrees(yaw)), float(np.degrees(pitch)), float(np.degrees(roll))

    def _angles(self, q_current: np.ndarray) -> tuple[float, float]:
        return self.relative_euler_deg(q_current)[:2]

    @staticmethod
    def _shape_velocity(value: float, deadband: float, gamma: float) -> float:
        """Return a signed, deadbanded and non-linear velocity.

        The linear factor preserves the input when ``gamma == 1``.  Using
        ``1 + magnitude`` for the power term keeps small rates useful while
        still increasing the response for larger rates.
        """
        value = float(value)
        deadband = float(deadband)
        gamma = float(gamma)
        if not np.isfinite(value):
            return 0.0
        if not np.isfinite(deadband) or deadband < 0.0:
            raise ValueError("deadband must be finite and non-negative")
        if not np.isfinite(gamma) or gamma <= 0.0:
            raise ValueError("gamma must be finite and positive")
        magnitude = max(abs(value) - deadband, 0.0)
        if magnitude == 0.0:
            return 0.0
        shaped = magnitude * (1.0 + magnitude) ** (gamma - 1.0)
        return float(np.copysign(shaped, value))

    def process_absolute(
        self,
        q_current: np.ndarray,
        dt: float | None = None,
        timestamp: float | None = None,
    ) -> tuple[float, float]:
        angles = np.asarray(self._angles(q_current), dtype=float)
        if self.one_euro_filter.enabled:
            if dt is None:
                if timestamp is not None and self._absolute_last_time is not None:
                    dt = max(0.0, timestamp - self._absolute_last_time)
                else:
                    dt = 1.0 / 60.0
            filter_dt = max(float(dt), 1e-6)
            angles = np.array(
                [filt.update(value, filter_dt) for filt, value in zip(self._absolute_filters, angles)]
            )
        self._absolute_last_time = timestamp
        yaw_deg, pitch_deg = angles
        px = self.origin_x + self.screen_width / 2.0 - self.kx * yaw_deg
        py = self.origin_y + self.screen_height / 2.0 - self.ky * pitch_deg
        return float(np.clip(px, self.origin_x, self.origin_x + self.screen_width - 1)), float(
            np.clip(py, self.origin_y, self.origin_y + self.screen_height - 1)
        )

    def process_velocity(self, q_current: np.ndarray, dt: float | None = None, timestamp: float | None = None) -> tuple[float, float]:
        """Integrate shaped angular velocity; ``dt`` is seconds between samples."""
        now = timestamp
        if dt is None:
            dt = 0.0 if self._last_time is None or now is None else max(0.0, now - self._last_time)
        dt = float(max(dt, 0.0))
        angles = np.asarray(self._angles(q_current), dtype=float)
        if self._last_angles is None:
            initial = np.array([f.update(v, max(dt, 1e-6)) for f, v in zip(self._velocity_filters, angles)])
            self._last_angles, self._last_time = tuple(initial), now
            return tuple(self._position)
        filtered = np.array([f.update(v, max(dt, 1e-6)) for f, v in zip(self._velocity_filters, angles)])
        # Bias is an angular rate, not an accumulated pose offset. Keep the
        # previous measured angles uncorrected so bias cannot cancel itself.
        delta = (filtered - np.asarray(self._last_angles) + 180.0) % 360.0 - 180.0
        measured_velocity = delta / max(dt, 1e-6)
        speed = float(np.linalg.norm(measured_velocity))
        stationary = speed <= self.auto_zero.stationary_threshold_deg_s
        if stationary:
            self._stationary_time += dt
        else:
            self._stationary_time = 0.0
        if stationary and self._stationary_time * 1000 >= self.auto_zero.stationary_window_ms:
            if self.auto_zero.enabled:
                alpha = 1 - np.exp(-dt / max(self.auto_zero.stationary_window_ms / 1000, 1e-6))
                self._bias += alpha * (measured_velocity - self._bias)
            if self.yaw_drift_correction.enabled:
                alpha = 1 - np.exp(-dt / max(self.yaw_drift_correction.time_constant_s, 1e-6))
                residual_yaw = measured_velocity[0] - (self._bias[0] if self.auto_zero.enabled else 0.0)
                self._drift_bias += alpha * (residual_yaw - self._drift_bias)
        velocity = measured_velocity.copy()
        if self.auto_zero.enabled:
            velocity -= self._bias
        if self.yaw_drift_correction.enabled:
            velocity[0] -= self._drift_bias
        if stationary:
            # A retained rate estimate must not invent opposite motion when
            # the sensor stops drifting.
            velocity = np.sign(measured_velocity) * np.clip(
                velocity * np.sign(measured_velocity), 0.0, np.abs(measured_velocity)
            )
        velocity = np.array([self._shape_velocity(velocity[0], self.deadband_deg_s, self.gamma),
                             self._shape_velocity(velocity[1], self.deadband_deg_s, self.gamma)])
        self._position -= np.array([self.velocity_kx * velocity[0], self.velocity_ky * velocity[1]]) * dt
        self._position[0] = np.clip(self._position[0], self.origin_x, self.origin_x + self.screen_width - 1)
        self._position[1] = np.clip(self._position[1], self.origin_y, self.origin_y + self.screen_height - 1)
        self._last_angles, self._last_time = tuple(filtered), now
        return tuple(map(float, self._position))

    def process(self, q_current: np.ndarray, dt: float | None = None, timestamp: float | None = None) -> tuple[float, float]:
        return (
            self.process_velocity(q_current, dt, timestamp)
            if self.mode.lower() in {"velocity", "mode_b", "b"}
            else self.process_absolute(q_current, dt, timestamp)
        )
