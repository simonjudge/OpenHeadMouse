"""Review candidates only: detection never discards a trial automatically."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, stdev

from colab_session_viewer.cursor_paths import numbered_targets
from colab_session_viewer.data import Session
from fitts_law.results import FittsStats, TrialResult


@dataclass
class TargetReview:
    participant: str
    number: int
    session: Session
    file_number: int
    trial: TrialResult
    reasons: tuple[str, ...]


def review_targets(sessions: list[Session], exclude: int) -> list[TargetReview]:
    """Flag >3 sample SD deviations within each participant's retained pool.

    Include task-axis endpoint error as well as movement time/distance so
    different target distances do not hide a gross selection error.
    """
    candidates = []
    measures = (
        ("Movement time (s)", lambda t: t.movement_time_s),
        ("Movement distance (px)", lambda t: math.dist(t.start_pos, t.click_pos)),
        ("Task-axis endpoint error (px)", FittsStats._axis_signed_error),
    )
    for participant in sorted({s.participant for s in sessions}):
        targets = [item for item in numbered_targets(
            [s for s in sessions if s.participant == participant], exclude,
        ) if item[-1]]
        reasons: dict[int, list[str]] = {}
        for label, measure in measures:
            values = [measure(item[3]) for item in targets]
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"{participant}: non-finite {label.lower()} in target review")
            if len(values) < 2:
                continue
            average, sd = mean(values), stdev(values)
            if not sd:
                continue
            for item, value in zip(targets, values):
                z = (value - average) / sd
                if abs(z) > 3:
                    reasons.setdefault(item[0], []).append(
                        f"{label}: {value:.1f}; mean {average:.1f}, SD {sd:.1f}, z {z:+.1f}"
                    )
        for number, session, file_number, trial, _ in targets:
            if number in reasons:
                candidates.append(TargetReview(
                    participant, number, session, file_number, trial, tuple(reasons[number]),
                ))
    return candidates
