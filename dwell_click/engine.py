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
    zoom_enabled: bool = False
    zoom_width_px: int = 300
    zoom_height_px: int = 200
    zoom_scale: float = 4.0
    context_menu_enabled: bool = False
    context_menu_distance_px: int = 60
    context_menu_size_px: int = 54
    context_menu_lock_time_s: float = 3.0

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
        for name in ("zoom_width_px", "zoom_height_px"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(self.zoom_scale) or self.zoom_scale <= 1:
            raise ValueError("zoom_scale must be finite and greater than one")
        if (
            not isinstance(self.context_menu_distance_px, int)
            or isinstance(self.context_menu_distance_px, bool)
            or self.context_menu_distance_px < 48
        ):
            raise ValueError("context_menu_distance_px must be an integer of at least 48")
        if not math.isfinite(self.context_menu_lock_time_s) or self.context_menu_lock_time_s <= 0:
            raise ValueError("context_menu_lock_time_s must be finite and greater than zero")
        if (
            not isinstance(self.context_menu_size_px, int)
            or isinstance(self.context_menu_size_px, bool)
            or not 24 <= self.context_menu_size_px <= 200
        ):
            raise ValueError("context_menu_size_px must be an integer between 24 and 200")

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
            zoom_enabled=bool(config.get("zoom_enabled", defaults.zoom_enabled)),
            zoom_width_px=int(config.get("zoom_width_px", defaults.zoom_width_px)),
            zoom_height_px=int(config.get("zoom_height_px", defaults.zoom_height_px)),
            zoom_scale=float(config.get("zoom_scale", defaults.zoom_scale)),
            context_menu_enabled=bool(config.get("context_menu_enabled", defaults.context_menu_enabled)),
            context_menu_distance_px=int(config.get("context_menu_distance_px", defaults.context_menu_distance_px)),
            context_menu_size_px=int(config.get("context_menu_size_px", defaults.context_menu_size_px)),
            context_menu_lock_time_s=float(config.get("context_menu_lock_time_s", defaults.context_menu_lock_time_s)),
        )

    def to_config(self) -> dict:
        return {
            "dwell_time_s": self.dwell_time_s,
            "movement_tolerance_px": self.movement_tolerance_px,
            "resume_time_s": self.resume_time_s,
            "ring_color": self.ring_color,
            "ring_diameter_px": self.ring_diameter_px,
            "ring_thickness_px": self.ring_thickness_px,
            "zoom_enabled": self.zoom_enabled,
            "zoom_width_px": self.zoom_width_px,
            "zoom_height_px": self.zoom_height_px,
            "zoom_scale": self.zoom_scale,
            "context_menu_enabled": self.context_menu_enabled,
            "context_menu_distance_px": self.context_menu_distance_px,
            "context_menu_size_px": self.context_menu_size_px,
            "context_menu_lock_time_s": self.context_menu_lock_time_s,
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

    def inhibit_until_moved(self, position: Point) -> None:
        """Require leaving this area before starting another dwell."""
        self.reset()
        self._clicked_anchor = position

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
