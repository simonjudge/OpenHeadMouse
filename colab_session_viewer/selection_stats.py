"""Alternative selection endpoints; original trials and playback remain untouched."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from colab_session_viewer.data import Session, fitts_summary_row, retained_trials
from colab_session_viewer.dwell import selection_settings, target_intervals
from fitts_law.results import TrialResult


def first_entry_trial(trial: TrialResult) -> TrialResult | None:
    """First point-cursor contact with the circular target, including tangency.

    Reuse dwell's validated timestamped start/sample/click sequence, then solve
    segment-circle intersections. No path samples or typing geometry means no
    estimate. The trial's saved planned start is retained; this is not a
    simulated sequence whose next trial begins at the preceding first-entry
    endpoint. The planned start need not be the cursor's actual position.
    """
    if trial.block_type == "typing":
        return None
    points, _ = target_intervals(trial)
    if not len(points):
        return None
    centre = np.asarray(trial.target_pos)
    radius2 = (trial.width_px / 2) ** 2
    entry = None
    for index, point in enumerate(points):
        offset = point[1:] - centre
        if float(offset @ offset) <= radius2:
            entry = point
            break
        if index == len(points) - 1:
            break
        delta = points[index + 1, 1:] - point[1:]
        a = float(delta @ delta)
        if not a:
            continue
        b = 2 * float(offset @ delta)
        c = float(offset @ offset) - radius2
        discriminant = b * b - 4 * a * c
        if discriminant < 0:
            continue
        fraction = (-b - math.sqrt(discriminant)) / (2 * a)
        if 0 <= fraction <= 1:
            entry = point + fraction * (points[index + 1] - point)
            break
    if entry is None:
        return None
    time = float(entry[0])
    path = points[points[:, 0] < time].tolist() + [entry.tolist()]
    return replace(
        trial, movement_time_s=time, end_time=trial.start_time + time,
        end_host_time=trial.start_host_time + time if trial.start_host_time is not None else None,
        click_pos=tuple(entry[1:]), hit=True, trajectory=path,
    )


def alternative_selection_rows(
    sessions: list[Session], exclude: int, *, first_entry=False,
    excluded_trial_ids: set[int] | None = None,
):
    """Return results and coverage, pooled across all retained files per participant."""
    results, coverage = [], []
    for participant in sorted({s.participant for s in sessions}):
        trials = []
        counts = {"participant": participant, "available": 0, "used": 0,
                  "unknown": 0, "invalid_time": 0, "missing_path": 0,
                  "no_entry": 0, "unsupported": 0}
        for session in sessions:
            if session.participant != participant:
                continue
            method, duration = selection_settings(session.selection_method, session.dwell_time_s)
            for trial in retained_trials(session, exclude):
                if id(trial) in (excluded_trial_ids or ()):
                    continue
                counts["available"] += 1
                if first_entry:
                    if trial.block_type == "typing":
                        counts["unsupported"] += 1
                        continue
                    if not trial.trajectory:
                        counts["missing_path"] += 1
                        continue
                    estimate = first_entry_trial(trial)
                    if estimate is None:
                        counts["no_entry"] += 1
                        continue
                else:
                    if method == "unknown":
                        counts["unknown"] += 1
                        continue
                    time = trial.movement_time_s - (duration if method == "dwell" else 0)
                    if time < 0:
                        counts["invalid_time"] += 1
                        continue
                    estimate = replace(trial, movement_time_s=time)
                trials.append(estimate)
        counts["used"] = len(trials)
        row = fitts_summary_row(participant, trials)
        if first_entry:
            # Paper uses nominal W because boundary endpoints make We misleading.
            ids = [math.log2(math.dist(t.start_pos, t.click_pos) / t.width_px + 1) for t in trials]
            row = {
                ("nominal_width_px" if key == "effective_width_px" else
                 "first_entry_id_bits" if key == "index_of_difficulty_bits" else key): value
                for key, value in row.items()
            }
            row["nominal_width_px"] = float(np.mean([t.width_px for t in trials])) if trials else None
            row["first_entry_id_bits"] = float(np.mean(ids)) if ids else None
            mean = row["mean_movement_time_s"]
            row["throughput_bits_per_s"] = row["first_entry_id_bits"] / mean if mean and mean > 0 else None
        elif not row["mean_movement_time_s"]:
            row["throughput_bits_per_s"] = None
        results.append(row)
        coverage.append(counts)
    return results, coverage
