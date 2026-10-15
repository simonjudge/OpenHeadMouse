"""GUI-independent dwell timing and movement-tolerance state machine."""

from __future__ import annotations

from dataclasses import dataclass
import math


Point = tuple[float, float]


@dataclass(frozen=True)
class DwellSettings:
    dwell_time_s: float = 1.0
    movement_tolerance_px: float = 10.0
    resume_time_s: float = 0.5
    ring_color: str = "#ff0000"
    ring_diameter_px: int = 40
    ring_thickness_px: int = 4

    def __post_init__(self) -> None:
        if self.dwell_time_s <= 0:
            raise ValueError("dwell_time_s must be greater than zero")
        if self.movement_tolerance_px < 0:
            raise ValueError("movement_tolerance_px cannot be negative")
        if self.resume_time_s < 0:
            raise ValueError("resume_time_s cannot be negative")
        if self.ring_diameter_px <= 0:
            raise ValueError("ring_diameter_px must be greater than zero")
        if self.ring_thickness_px <= 0:
            raise ValueError("ring_thickness_px must be greater than zero")

    @classmethod
    def from_config(cls, config: dict | None) -> "DwellSettings":
        """Build settings from a ``ui.dwell_click`` config dict, using defaults for missing keys."""
        config = config or {}
        defaults = cls()
        return cls(
            dwell_time_s=float(config.get("dwell_time_s", defaults.dwell_time_s)),
            movement_tolerance_px=float(
                config.get("movement_tolerance_px", defaults.movement_tolerance_px)
            ),
            resume_time_s=float(config.get("resume_time_s", defaults.resume_time_s)),
            ring_color=str(config.get("ring_color", defaults.ring_color)),
            ring_diameter_px=int(config.get("ring_diameter_px", defaults.ring_diameter_px)),
            ring_thickness_px=int(config.get("ring_thickness_px", defaults.ring_thickness_px)),
        )

    def to_config(self) -> dict:
        return {
            "dwell_time_s": self.dwell_time_s,
            "movement_tolerance_px": self.movement_tolerance_px,
            "resume_time_s": self.resume_time_s,
            "ring_color": self.ring_color,
            "ring_diameter_px": self.ring_diameter_px,
            "ring_thickness_px": self.ring_thickness_px,
        }


@dataclass(frozen=True)
class DwellUpdate:
    progress: float
    position: Point
    visible: bool
    click: bool = False


@dataclass
class _Area:
    anchor: Point
    elapsed_s: float
    last_time_s: float


class DwellEngine:
    """Tracks stable pointer areas and emits one click per completed dwell."""

    def __init__(self, settings: DwellSettings | None = None):
        self.settings = settings or DwellSettings()
        self._active: _Area | None = None
        self._suspended: _Area | None = None
        self._resume_deadline_s: float | None = None
        self._clicked_anchor: Point | None = None

    def reset(self) -> None:
        self._active = None
        self._suspended = None
        self._resume_deadline_s = None
        self._clicked_anchor = None

    def update_settings(self, settings: DwellSettings) -> None:
        self.settings = settings
        self.reset()

    def update(self, position: Point, now_s: float) -> DwellUpdate:
        point = (float(position[0]), float(position[1]))
        if (
            self._resume_deadline_s is not None
            and now_s > self._resume_deadline_s
        ):
            self._suspended = None
            self._resume_deadline_s = None

        if self._clicked_anchor is not None:
            if self._within(point, self._clicked_anchor):
                return DwellUpdate(1.0, point, visible=False)
            self._clicked_anchor = None
            self._active = _Area(point, 0.0, now_s)
            return DwellUpdate(0.0, point, visible=True)

        if self._active is None:
            self._active = _Area(point, 0.0, now_s)
            return DwellUpdate(0.0, point, visible=True)

        if (
            self._suspended is not None
            and self._resume_deadline_s is not None
            and now_s <= self._resume_deadline_s
            and self._within(point, self._suspended.anchor)
        ):
            self._active = _Area(
                self._suspended.anchor,
                self._suspended.elapsed_s,
                now_s,
            )
            self._suspended = None
            self._resume_deadline_s = None

        if not self._within(point, self._active.anchor):
            if self._active.elapsed_s > 0:
                self._suspended = self._active
                self._resume_deadline_s = now_s + self.settings.resume_time_s
            self._active = _Area(point, 0.0, now_s)
            return DwellUpdate(0.0, point, visible=True)

        elapsed_delta = max(0.0, now_s - self._active.last_time_s)
        self._active.elapsed_s += elapsed_delta
        self._active.last_time_s = now_s
        completed = self._active.elapsed_s + 1e-12 >= self.settings.dwell_time_s
        progress = min(1.0, self._active.elapsed_s / self.settings.dwell_time_s)
        if completed:
            self._clicked_anchor = point
            self._active = None
            self._suspended = None
            self._resume_deadline_s = None
            return DwellUpdate(1.0, point, visible=False, click=True)
        return DwellUpdate(progress, point, visible=True)

    def _within(self, point: Point, anchor: Point) -> bool:
        return math.dist(point, anchor) <= self.settings.movement_tolerance_px
