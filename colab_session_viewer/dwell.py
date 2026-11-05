"""Selection metadata and sample-based dwell estimates; never alter recordings."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from core import quaternion_math as qm
from fitts_law.results import FittsStats, TrialResult

if TYPE_CHECKING:
    from colab_session_viewer.data import Session


SELECTION_METHODS = (
    ("Not specified", "unknown"), ("External switch", "external_switch"),
    ("Dwell", "dwell"), ("Mouse click", "mouse"), ("Other non-dwell", "other"),
)


def selection_settings(method: str, duration) -> tuple[str, float | None]:
    """Validate selection labels and return (method, dwell seconds or None).

    Dwell requires a positive finite duration; other methods require no
    duration. Blank method means unknown, never inferred non-dwell.
    """
    method = method.strip() or "unknown"
    if isinstance(duration, str):
        duration = duration.strip()
    if method not in dict(SELECTION_METHODS).values():
        raise ValueError(f"Unknown selection method: {method}")
    if method != "dwell":
        if duration not in (None, ""):
            raise ValueError("Dwell time must be blank for non-dwell selection")
        return method, None
    try:
        seconds = float(duration)
    except (TypeError, ValueError) as error:
        raise ValueError("Dwell selection requires a positive dwell_time_s in seconds") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Dwell selection requires a positive finite dwell_time_s in seconds")
    return method, seconds


def target_intervals(trial: TrialResult) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Estimate inside-target intervals from time-stamped cursor samples.

    Times are seconds relative to trial start. Add saved start at time 0 and
    click at end_time-start_time; duplicate times keep the last point. For
    each linear segment p(u)=p0+u*(p1-p0), solve |p(u)-target|^2 <= (width/2)^2,
    intersect roots with u in [0,1], convert to seconds and merge touching
    intervals. Tangencies have zero duration and are omitted. No trajectory
    gives empty results, not an invented path. Bad sample times raise errors.
    This circular-region proxy does not observe software dwell-timer events.
    """
    if not trial.trajectory:
        return np.empty((0, 3)), []
    duration = trial.end_time - trial.start_time
    points = [[0.0, *trial.start_pos]]
    for time, x, y in trial.trajectory:
        if not (0 <= time <= duration):
            raise ValueError("Dwell analysis: cursor sample outside trial duration")
        if time < points[-1][0]:
            raise ValueError("Dwell analysis: cursor samples must be chronological")
        if time == points[-1][0]:
            points[-1] = [time, x, y]
        else:
            points.append([time, x, y])
    if points[-1][0] == duration:
        points[-1] = [duration, *trial.click_pos]
    else:
        points.append([duration, *trial.click_pos])
    points = np.asarray(points, dtype=float)
    intervals = []
    centre = np.asarray(trial.target_pos)
    radius = trial.width_px / 2
    for start, end in zip(points[:-1], points[1:]):
        offset, delta = start[1:] - centre, end[1:] - start[1:]
        a = float(delta @ delta)
        if a == 0:
            if float(offset @ offset) > radius ** 2:
                continue
            lo, hi = 0.0, 1.0
        else:
            b, c = 2 * float(offset @ delta), float(offset @ offset) - radius ** 2
            discriminant = b * b - 4 * a * c
            if discriminant <= 0:
                continue
            root = math.sqrt(discriminant)
            lo, hi = max(0.0, (-b - root) / (2 * a)), min(1.0, (-b + root) / (2 * a))
            if hi <= lo:
                continue
        left, right = start[0] + lo * (end[0] - start[0]), start[0] + hi * (end[0] - start[0])
        if intervals and math.isclose(intervals[-1][1], left, abs_tol=1e-9):
            intervals[-1] = (intervals[-1][0], right)
        else:
            intervals.append((left, right))
    return points, intervals


def _window_points(times, values, start, end):
    """Return samples strictly inside [start,end] plus interpolated endpoints.

    Each value column uses linear interpolation on the supplied seconds clock.
    Callers ensure coverage so endpoint extrapolation is not needed.
    """
    indices = times[(times > start) & (times < end)]
    grid = np.r_[start, indices, end]
    return np.column_stack([np.interp(grid, times, column) for column in values.T])


def _trial_dwell(session: Session, trial: TrialResult) -> dict:
    """Estimate per-target dwell behaviour over all inside-target visits.

    Sum visit durations; final residence is the last visit's duration only if
    it reaches selection, otherwise 0. Settling = final visit start - first
    entry, unavailable without final residence. Cursor path sums Euclidean
    steps within visits; axis ranges are max-min over all clipped visit points.

    With shared host-clock head coverage, interpolate quaternion boundaries
    by shortest-arc SLERP and sum geodesic angular steps within visits (degrees).
    Head axis ranges use linearly interpolated, unwrapped recorded Euler angles
    across all visits. Neither cursor nor head path bridges an outside gap.
    Duplicate host times keep the last sample. Missing/partial coverage leaves
    head estimates absent; no path or unsupported typing geometry gives {}.
    Existing recording/rest opening exclusions do not filter these windows.
    """
    if trial.block_type == "typing":
        return {}
    points, intervals = target_intervals(trial)
    if not len(points):
        return {}
    result = {"dwell_inside_time_s_mean": sum(end - start for start, end in intervals)}
    duration = trial.end_time - trial.start_time
    final = intervals[-1] if intervals and math.isclose(intervals[-1][1], duration, abs_tol=1e-9) else None
    result["dwell_final_inside_s_mean"] = final[1] - final[0] if final else 0.0
    if final:
        result["dwell_settling_s_mean"] = final[0] - intervals[0][0]
    if not intervals:
        return result
    runs = [_window_points(points[:, 0], points[:, 1:], start, end) for start, end in intervals]
    cursor = np.concatenate(runs)
    result["dwell_cursor_path_px_mean"] = sum(float(np.linalg.norm(np.diff(run, axis=0), axis=1).sum()) for run in runs)
    result["dwell_cursor_x_range_px_mean"], result["dwell_cursor_y_range_px_mean"] = np.ptp(cursor, axis=0)
    if not session.samples or trial.start_host_time is None:
        return result
    if any(sample.host_time is None for sample in session.samples):
        return result
    samples = list({sample.host_time: sample for sample in sorted(
        session.samples, key=lambda sample: sample.host_time,
    )}.values())
    times = np.asarray([sample.host_time - trial.start_host_time for sample in samples])
    # Do not extrapolate a partially recorded dwell window.
    if len(times) < 2 or times[0] > intervals[0][0] or times[-1] < intervals[-1][1]:
        return result
    quaternions = qm.normalize(np.asarray([sample.quaternion for sample in samples]))
    # Unwrapped tracker angles describe axis ranges, independently of mesh reference.
    angles = np.degrees(np.unwrap(qm.to_euler_zyx(quaternions), axis=0))
    head_runs = [_window_points(times, angles, start, end) for start, end in intervals]
    head = np.concatenate(head_runs)
    for axis, value in zip(("yaw", "pitch", "roll"), np.ptp(head, axis=0)):
        result[f"dwell_head_{axis}_range_deg_mean"] = float(value)
    # SLERP each boundary, keeping full recorded quaternions within each run.
    def rotation_at(time):
        """Interpolate shortest-arc unit quaternion at a covered trial-relative time.

        For endpoint separation theta, use sin((1-f)*theta)/sin(theta) and
        sin(f*theta)/sin(theta) weights. Flip the second quaternion when the
        dot product is negative; near-identical rotations return the first.
        """
        index = min(max(int(np.searchsorted(times, time, side="right")) - 1, 0), len(times) - 2)
        fraction = (time - times[index]) / (times[index + 1] - times[index])
        first, last = quaternions[index], quaternions[index + 1]
        dot = float(first @ last)
        if dot < 0:
            last, dot = -last, -dot
        theta = math.acos(np.clip(dot, -1, 1))
        if theta < 1e-8:
            return first
        return (math.sin((1 - fraction) * theta) * first + math.sin(fraction * theta) * last) / math.sin(theta)
    movement = 0.0
    for start, end in intervals:
        run = np.vstack([rotation_at(start), quaternions[(times > start) & (times < end)], rotation_at(end)])
        movement += float(np.degrees(qm.angle_between(run[:-1], run[1:])).sum())
    result["dwell_head_path_deg_mean"] = movement
    return result


DWELL_FIELDS = {
    "nodwell_trial_count": "Number of targets with valid no-dwell timing. Known non-dwell methods keep their recorded MT; unknown methods and dwell MT shorter than the configured duration are excluded only from adjusted timing.",
    "nodwell_invalid_count": "Targets whose recorded MT is shorter than configured dwell time. Not clamped to zero; omitted only from no-dwell timing and throughput.",
    "nodwell_unknown_count": "Targets without a labelled selection method. Excluded only from no-dwell timing and throughput; not assumed to be non-dwell.",
    "mtnodwell_s": "Average MTnodwell in seconds, calculated per target before averaging: recorded MT minus that session's configured dwell duration for dwell targets, unchanged MT for known non-dwell targets in mixed-method participants. Uses only valid adjusted targets. Not applicable when the participant/session has no dwell selection.",
    "nodwell_throughput_bits_per_s": "No-dwell throughput: pooled IDe of the valid adjusted targets divided by their average per-target MTnodwell. Unavailable if average MTnodwell is zero or no valid timing remains; not applicable without dwell selection. Original throughput is unchanged.",
    "dwell_trial_count": "Number of analysed targets labelled dwell, after global exclusions and, in the matched table only, target matching.",
    "dwell_cursor_trial_count": "Dwell targets with recorded cursor trajectories, used for inside-target estimates. No trajectory means unavailable, not zero movement.",
    "dwell_inside_trial_count": "Dwell targets with a nonzero estimated inside-target visit. Cursor path/range averages use these targets; targets with no visit still contribute zero to inside-time averages.",
    "dwell_head_trial_count": "Dwell targets with inside-target intervals fully covered by head samples on a shared host clock. Missing/partial head coverage is unavailable, not zero movement.",
    "dwell_final_trial_count": "Dwell targets whose last estimated inside-target interval continues to selection; settling-time estimates use only these targets.",
    "dwell_unsupported_trial_count": "Dwell typing targets excluded from spatial dwell estimates because saved centres/widths do not describe the rectangular keyboard hit regions. Configured-duration timing adjustment still applies.",
    "dwell_inside_time_s_mean": "Average cumulative time inside the target before selection, summing all visits. Circular hit region uses target width/2. Estimated from linearly interpolated cursor samples, not the software dwell timer; it can be shorter than configured dwell, especially for misses.",
    "dwell_final_inside_s_mean": "Average final uninterrupted inside-target duration before selection; zero when selection ends outside the target. Estimated from cursor samples, not a recorded dwell-timer event.",
    "dwell_settling_s_mean": "Average time from first target entry to the start of the final uninterrupted inside-target period. Includes exits and returns. Only targets ending inside contribute; zero means no intervening exit.",
    "dwell_cursor_path_px_mean": "Average cursor distance travelled during all inside-target visits, in pixels. No movement across outside-target gaps is counted.",
    "dwell_cursor_x_range_px_mean": "Average per-target horizontal cursor range across all inside-target visits, in pixels.",
    "dwell_cursor_y_range_px_mean": "Average per-target vertical cursor range across all inside-target visits, in pixels.",
    "dwell_head_path_deg_mean": "Average total 3D head rotation during inside-target visits, in degrees, using quaternion angular steps and interpolated boundaries. No movement across outside-target gaps is counted.",
    **{f"dwell_head_{axis}_range_deg_mean": f"Average per-target recorded {axis} range during all inside-target visits, in degrees, unwrapped across angle boundaries. Requires shared-clock head coverage; not a calibration-relative mesh angle." for axis in ("yaw", "pitch", "roll")},
}

DWELL_LABELS = {
    "nodwell_trial_count": "Valid no-dwell timing targets",
    "nodwell_invalid_count": "MT shorter than dwell targets",
    "nodwell_unknown_count": "Unknown selection targets",
    "mtnodwell_s": "MTnodwell average (s)",
    "nodwell_throughput_bits_per_s": "No-dwell throughput (bits/s)",
    "dwell_trial_count": "Dwell targets",
    "dwell_cursor_trial_count": "Dwell cursor estimate targets",
    "dwell_inside_trial_count": "Inside-target movement estimate targets",
    "dwell_head_trial_count": "Dwell head estimate targets",
    "dwell_final_trial_count": "Dwell settling estimate targets",
    "dwell_unsupported_trial_count": "Unsupported dwell geometry targets",
    "dwell_inside_time_s_mean": "Total inside-target time average (s)",
    "dwell_final_inside_s_mean": "Final inside-target time average (s)",
    "dwell_settling_s_mean": "Dwell settling time average (s)",
    "dwell_cursor_path_px_mean": "Inside-target cursor path average (px)",
    "dwell_cursor_x_range_px_mean": "Inside-target cursor horizontal range average (px)",
    "dwell_cursor_y_range_px_mean": "Inside-target cursor vertical range average (px)",
    "dwell_head_path_deg_mean": "Inside-target head rotation average (deg)",
    **{f"dwell_head_{axis}_range_deg_mean": f"Inside-target {axis} range average (deg)"
       for axis in ("yaw", "pitch", "roll")},
}

NODWELL_FITTS_FIELDS = ("mtnodwell_s", "nodwell_throughput_bits_per_s")
NODWELL_EQUIVALENTS = dict(zip(("mean_movement_time_s", "throughput_bits_per_s"), NODWELL_FITTS_FIELDS))
DWELL_BEHAVIOUR_FIELDS = tuple(key for key in DWELL_FIELDS if key not in NODWELL_FITTS_FIELDS)


def selection_summary(sessions: list[Session], trials: list[TrialResult]) -> dict:
    """Aggregate selection timing and dwell estimates for exactly the given targets.

    Target identity links each trial to its session metadata without mutation.
    Per target, subtract that session's configured duration for dwell, retain
    recorded MT for known non-dwell, and skip unknown methods. Flag/exclude
    negative differences from adjusted timing only; keep zero. MTnodwell is the
    arithmetic mean of valid differences. Adjusted throughput is pooled IDe
    recalculated on those valid targets / mean MTnodwell, None if mean <= 0.

    Average each available _trial_dwell measure equally across contributing
    dwell targets (not by duration); counts disclose differing denominators.
    Invalid timing targets may still contribute spatial estimates. Purely
    non-dwell/unknown sessions have None for dwell-only and adjusted Fitts
    measures; mixed-method pools keep valid non-dwell times in their timing
    denominator. Callers apply global exclusions and optional matching first.
    """
    selected = {id(trial) for trial in trials}
    adjusted, valid, estimates = [], [], []
    invalid = unknown = dwell_count = 0
    for session in sessions:
        method, seconds = selection_settings(session.selection_method, session.dwell_time_s)
        for trial in session.trials:
            if id(trial) not in selected:
                continue
            if method == "unknown":
                unknown += 1
                continue
            time = trial.movement_time_s - (seconds if method == "dwell" else 0)
            if time < 0:
                invalid += 1
            else:
                adjusted.append(time)
                valid.append(trial)
            if method == "dwell":
                dwell_count += 1
                estimates.append(_trial_dwell(session, trial))
    row = {key: None for key in DWELL_FIELDS}
    mean = float(np.mean(adjusted)) if adjusted else None
    ide = FittsStats.index_of_difficulty(FittsStats.effective_amplitude(valid), FittsStats.effective_width(valid))
    row.update(
        nodwell_trial_count=len(valid), nodwell_invalid_count=invalid, nodwell_unknown_count=unknown,
        mtnodwell_s=mean, nodwell_throughput_bits_per_s=ide / mean if mean is not None and mean > 0 else None,
        dwell_trial_count=dwell_count,
        dwell_cursor_trial_count=sum("dwell_inside_time_s_mean" in item for item in estimates),
        dwell_inside_trial_count=sum("dwell_cursor_path_px_mean" in item for item in estimates),
        dwell_head_trial_count=sum("dwell_head_path_deg_mean" in item for item in estimates),
        dwell_final_trial_count=sum("dwell_settling_s_mean" in item for item in estimates),
        dwell_unsupported_trial_count=sum(
            trial.block_type == "typing" for session in sessions if session.selection_method == "dwell"
            for trial in session.trials if id(trial) in selected
        ),
    )
    for key in DWELL_FIELDS:
        values = [item[key] for item in estimates if key in item]
        if values:
            row[key] = float(np.mean(values))
    if not any(session.selection_method == "dwell" for session in sessions):
        for key in row:
            if key.startswith("dwell_") or key == "nodwell_invalid_count" or key in NODWELL_FITTS_FIELDS:
                row[key] = None
    return row
