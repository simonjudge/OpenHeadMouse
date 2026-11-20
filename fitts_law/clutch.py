"""Conservative, sample-based estimates of headmouse boundary clutching."""

from __future__ import annotations

import numpy as np


_EDGE_ZONE_PX = 30.0
_STILL_AXIS_PX = 4.0
_MIN_STILL_S = 0.06
_MAX_CURSOR_GAP_S = 0.5
_MAX_HEAD_GAP_S = 0.2
_OUTWARD_DEG = 2.0
_RETURN_DEG = 1.0
_RETURN_PX = 20.0
_RETURN_WINDOW_S = 0.6


def boundary_counts(trials, metadata):
    """Recorded event counts, or estimated near-edge episodes for older paths.

    The 30-pixel zone accommodates canvas-local coordinates in older files.
    Consecutive samples at any side form one episode, including corners.
    """
    width, height = metadata.get("screen_width_px"), metadata.get("screen_height_px")
    geometry_available = (
        width is not None and height is not None
        and np.isfinite([width, height]).all()
        and min(width, height) > 2 * _EDGE_ZONE_PX
    )
    counts = []
    for trial in trials:
        if trial.boundary_hits is not None:
            counts.append(trial.boundary_hits)
            continue
        if not geometry_available or not trial.trajectory:
            counts.append(None)
            continue
        points = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)[:, 1:]
        if not np.isfinite(points).all():
            raise ValueError("Boundary analysis: cursor path contains non-finite values")
        points = np.vstack((points, trial.click_pos))
        near = (
            (points[:, 0] <= _EDGE_ZONE_PX) | (points[:, 0] >= width - _EDGE_ZONE_PX)
            | (points[:, 1] <= _EDGE_ZONE_PX) | (points[:, 1] >= height - _EDGE_ZONE_PX)
        )
        counts.append(int(np.count_nonzero(near & ~np.r_[False, near[:-1]])))
    return counts


def clutch_counts(trials, samples, metadata):
    """Return one estimated clutch count or None per trial.

    Screen dimensions, cursor trajectory and complete shared-clock head
    coverage are required. The saved cursor coordinates are canvas-local, so
    the 30-pixel edge zone is approximate when the canvas origin is unknown.
    """
    access = str(metadata.get("access_method") or "").replace(" ", "").lower()
    if access not in {"headmouse", "openheadmouse"}:
        return [None] * len(trials)
    width, height = metadata.get("screen_width_px"), metadata.get("screen_height_px")
    if not samples or not width or not height:
        return [None] * len(trials)
    if not np.isfinite([width, height]).all() or min(width, height) <= 2 * _EDGE_ZONE_PX:
        return [None] * len(trials)
    if any(s.host_time is None for s in samples):
        return [None] * len(trials)
    ordered = sorted(samples, key=lambda s: s.host_time)
    unique = {s.host_time: s for s in ordered}
    times = np.asarray(list(unique), dtype=float)
    angles = np.asarray([[s.yaw_deg, s.pitch_deg] for s in unique.values()], dtype=float)
    if len(times) < 2 or not np.isfinite(times).all() or not np.isfinite(angles).all():
        return [None] * len(trials)
    angles = np.degrees(np.unwrap(np.radians(angles), axis=0))
    return [_trial_clutches(t, times, angles, float(width), float(height)) for t in trials]


def _trial_clutches(trial, times, angles, width, height):
    if trial.start_host_time is None or trial.end_host_time is None or not trial.trajectory:
        return None
    start, end = trial.start_host_time, trial.end_host_time
    if end <= start or times[0] > start or times[-1] < end:
        return None
    first = max(0, int(np.searchsorted(times, start, side="right")) - 1)
    last = min(len(times), int(np.searchsorted(times, end, side="left")) + 1)
    if np.any(np.diff(times[first:last]) > _MAX_HEAD_GAP_S):
        return None
    points = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
    if len(points) < 2 or not np.isfinite(points).all() or np.any(np.diff(points[:, 0]) < 0):
        return None
    duration = end - start
    if points[0, 0] < 0 or points[-1, 0] > duration:
        return None
    points = np.vstack((points, [duration, *trial.click_pos]))
    relative_times = times[first:last] - start
    head = angles[first:last]
    detections = []
    # Positive yaw/pitch moves the cursor left/up in the normal headmouse mapping.
    for axis, extent, side, sign in ((1, width, 0, 1), (1, width, 1, -1),
                                     (2, height, 0, 1), (2, height, 1, -1)):
        coordinate = points[:, axis]
        near = coordinate <= _EDGE_ZONE_PX if side == 0 else coordinate >= extent - _EDGE_ZONE_PX
        indices = np.flatnonzero(near & ~np.r_[False, near[:-1]])
        for begin in indices:
            stop = begin
            while stop + 1 < len(points) and near[stop + 1]:
                stop += 1
            i = begin
            while i < stop:
                j = i
                while (j < stop and points[j + 1, 0] - points[j, 0] <= _MAX_CURSOR_GAP_S
                       and abs(coordinate[j + 1] - coordinate[i]) <= _STILL_AXIS_PX):
                    j += 1
                if j > i and points[j, 0] - points[i, 0] >= _MIN_STILL_S:
                    a, b = points[i, 0], points[j, 0]
                    grid = np.r_[a, relative_times[(relative_times > a) & (relative_times < b)], b]
                    orientation = sign * np.interp(grid, relative_times, head[:, axis - 1])
                    peak = float(np.max(orientation))
                    if peak - orientation[0] >= _OUTWARD_DEG:
                        later = np.flatnonzero(
                            (points[:, 0] > b) & (points[:, 0] <= b + _RETURN_WINDOW_S)
                            & ((coordinate - coordinate[i] >= _RETURN_PX) if side == 0
                               else (coordinate[i] - coordinate >= _RETURN_PX)))
                        for k in later:
                            departure = points[k, 0]
                            returned = sign * np.interp(departure, relative_times, head[:, axis - 1])
                            if peak - returned >= _RETURN_DEG:
                                detections.append((a, departure))
                                break
                        if detections and detections[-1][0] == a:
                            break
                i = max(i + 1, j + 1)
    # A corner can satisfy both axes; one overlapping episode is one clutch.
    detections.sort()
    merged = []
    for start, end in detections:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return len(merged)


def clutch_summary(trials, samples, metadata):
    counts = [count for count in clutch_counts(trials, samples, metadata) if count is not None]
    return {
        "clutch_trial_count": len(counts),
        "clutch_count": sum(counts) if counts else None,
        "clutch_mean": float(np.mean(counts)) if counts else None,
    }
