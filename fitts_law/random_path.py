"""'Random Path' - a gamified alternative to the standard ISO tapping task,
where targets appear at randomized positions/sizes anywhere on screen.

Reuses the same TrialResult shape (tagged block_type="random_path") so
FittsStats can still compute throughput/ID for this mode, even though the
primary feedback shown to the user is score/streak rather than bits/s.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class RandomPathConfig:
    duration_s: float = 60.0
    max_targets: int | None = 30  # None = run until duration_s elapses
    min_width_px: float = 30.0
    max_width_px: float = 100.0
    margin_px: float = 40.0  # keep targets fully on-screen, inset from edges


class RandomPathRound:
    """Tracks score/streak and produces randomized target positions/widths."""

    def __init__(self, config: RandomPathConfig, screen_w: float, screen_h: float, rng: random.Random | None = None):
        self.config = config
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._rng = rng or random.Random()
        self.score = 0
        self.streak = 0
        self.targets_hit = 0
        self.targets_missed = 0

    def next_target(self) -> tuple[tuple[float, float], float]:
        """Returns ((x, y), width_px) for the next random target."""
        width = self._rng.uniform(self.config.min_width_px, self.config.max_width_px)
        half = width / 2
        m = self.config.margin_px
        x = self._rng.uniform(m + half, self.screen_w - m - half)
        y = self._rng.uniform(m + half, self.screen_h - m - half)
        return (x, y), width

    def register_result(self, hit: bool, movement_time_s: float, width_px: float) -> int:
        """Updates score/streak for one completed trial; returns points awarded (0 on miss)."""
        if not hit:
            self.streak = 0
            self.targets_missed += 1
            return 0

        self.streak += 1
        self.targets_hit += 1
        base_width = (self.config.min_width_px + self.config.max_width_px) / 2
        speed_factor = base_width / max(width_px, 1.0)
        points = round(1000 / max(movement_time_s, 0.1) * speed_factor / 100) * max(1, self.streak)
        self.score += points
        return points

    def is_complete(self, elapsed_s: float) -> bool:
        if self.config.max_targets is not None and (self.targets_hit + self.targets_missed) >= self.config.max_targets:
            return True
        return elapsed_s >= self.config.duration_s
