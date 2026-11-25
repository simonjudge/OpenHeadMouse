"""Sample-based estimates of off-screen and on-screen headmouse clutching."""

from __future__ import annotations

from dataclasses import dataclass, fields
import math

import numpy as np


@dataclass(frozen=True)
class ClutchSettings:
    edge_zone_px: float = 30.0
    still_axis_px: float = 4.0
    max_cursor_gap_s: float = 0.5
    max_head_gap_s: float = 0.2
    outward_deg: float = 2.0
    onscreen_retreat_px: float = 60.0
    onscreen_speed_ratio: float = 1.2
    onscreen_opposition: float = 0.5

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field.name} must be finite and non-negative")
            if field.name in ("max_cursor_gap_s", "max_head_gap_s") and value == 0:
                raise ValueError(f"{field.name} must be positive")
        if self.onscreen_speed_ratio < 1:
            raise ValueError("onscreen_speed_ratio must be at least 1")
        if self.onscreen_opposition > 1:
            raise ValueError("onscreen_opposition must be at most 1")


DEFAULT_CLUTCH_SETTINGS = ClutchSettings()


def boundary_counts(trials, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS):
    """Recorded event counts, or estimated near-edge episodes for older paths.

    The configurable zone accommodates canvas-local coordinates in older files.
    Consecutive samples at any side form one episode, including corners.
    """
    width, height = metadata.get("screen_width_px"), metadata.get("screen_height_px")
    geometry_available = (
        width is not None and height is not None
        and np.isfinite([width, height]).all()
        and min(width, height) > 2 * settings.edge_zone_px
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
            (points[:, 0] <= settings.edge_zone_px) | (points[:, 0] >= width - settings.edge_zone_px)
            | (points[:, 1] <= settings.edge_zone_px) | (points[:, 1] >= height - settings.edge_zone_px)
        )
        counts.append(int(np.count_nonzero(near & ~np.r_[False, near[:-1]])))
    return counts


def clutch_counts(trials, samples, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS):
    """Return one estimated off-screen clutch count or None per trial."""
    return [len(episodes) if episodes is not None else None
            for episodes in clutch_episodes(trials, samples, metadata, settings)]


def clutch_episodes(trials, samples, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS):
    """Return off-screen episode intervals relative to trial start, or None.

    Screen dimensions, cursor trajectory and shared-clock head coverage are
    required; a recording-end shortfall up to max_head_gap_s is tolerated.
    Cursor coordinates are canvas-local, so the edge zone is approximate
    when the canvas origin is unknown.
    """
    access = str(metadata.get("access_method") or "").replace(" ", "").lower()
    if access not in {"headmouse", "openheadmouse"}:
        return [None] * len(trials)
    width, height = metadata.get("screen_width_px"), metadata.get("screen_height_px")
    if not samples or not width or not height:
        return [None] * len(trials)
    if not np.isfinite([width, height]).all() or min(width, height) <= 2 * settings.edge_zone_px:
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
    return [_trial_clutches(t, times, angles, float(width), float(height), settings) for t in trials]


def _trial_clutches(trial, times, angles, width, height, settings):
    if trial.start_host_time is None or trial.end_host_time is None or not trial.trajectory:
        return None
    start, end = trial.start_host_time, trial.end_host_time
    if (end <= start or times[0] > start or start > times[-1]
            or end > times[-1] + settings.max_head_gap_s):
        return None
    first = max(0, int(np.searchsorted(times, start, side="right")) - 1)
    last = min(len(times), int(np.searchsorted(times, end, side="left")) + 1)
    if np.any(np.diff(times[first:last]) > settings.max_head_gap_s):
        return None
    points = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
    if len(points) < 2 or not np.isfinite(points).all() or np.any(np.diff(points[:, 0]) < 0):
        return None
    duration = end - start
    if points[0, 0] < 0 or points[-1, 0] > duration:
        return None
    points = np.vstack((points, [duration, *trial.click_pos]))
    head_times = times[first:last]
    head = angles[first:last]
    detections = []
    # Positive yaw/pitch moves the cursor left/up in the normal headmouse mapping.
    for axis, extent, side, sign in ((1, width, 0, 1), (1, width, 1, -1),
                                     (2, height, 0, 1), (2, height, 1, -1)):
        coordinate = points[:, axis]
        near = coordinate <= settings.edge_zone_px if side == 0 else coordinate >= extent - settings.edge_zone_px
        indices = np.flatnonzero(near & ~np.r_[False, near[:-1]])
        for begin in indices:
            stop = begin
            while stop + 1 < len(points) and near[stop + 1]:
                stop += 1
            i = begin
            while i < stop:
                j = i
                while (j < stop and points[j + 1, 0] - points[j, 0] <= settings.max_cursor_gap_s
                       and abs(coordinate[j + 1] - coordinate[i]) <= settings.still_axis_px):
                    j += 1
                if j > i:
                    a, b = points[i, 0], points[j, 0]
                    grid = np.r_[start + a, head_times[(head_times > start + a) & (head_times < start + b)],
                                 start + b]
                    orientation = sign * np.interp(grid, head_times, head[:, axis - 1])
                    peak = float(np.max(orientation))
                    if peak - orientation[0] >= settings.outward_deg:
                        for k in range(j + 1, len(points)):
                            if points[k, 0] - points[k - 1, 0] > settings.max_cursor_gap_s:
                                break
                            inward = (coordinate[k] - coordinate[i] if side == 0
                                      else coordinate[i] - coordinate[k])
                            if points[k, 0] <= b or inward < settings.still_axis_px:
                                continue
                            departure = points[k, 0]
                            returned = sign * np.interp(start + departure, head_times, head[:, axis - 1])
                            if peak - returned >= settings.outward_deg:
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
    return merged


def clutch_summary(trials, samples, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS):
    counts = [count for count in clutch_counts(trials, samples, metadata, settings) if count is not None]
    return {
        "clutch_trial_count": len(counts),
        "clutch_count": sum(counts) if counts else None,
        "clutch_mean": float(np.mean(counts)) if counts else None,
    }


def onscreen_counts(trials, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS, *, offscreen=None):
    """Count target-axis reset-and-recovery episodes, or None without usable paths."""
    return [len(episodes) if episodes is not None else None
            for episodes in onscreen_episodes(trials, metadata, settings, offscreen=offscreen)]


def total_clutch_counts(offscreen, onscreen):
    """Return binary target-level OR counts, preserving unknown negatives.

    A positive from either detector is sufficient; a zero requires both
    detectors to be available. Multiple episodes still count as one target.
    """
    if len(offscreen) != len(onscreen):
        raise ValueError("Off-screen and on-screen counts must match trials")
    return [
        1 if (edge is not None and edge > 0) or (reset is not None and reset > 0)
        else 0 if edge is not None and reset is not None else None
        for edge, reset in zip(offscreen, onscreen)
    ]


def onscreen_episodes(trials, metadata, settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS, *, offscreen=None):
    """Return on-screen episode intervals relative to trial start, or None."""
    access = str(metadata.get("access_method") or "").replace(" ", "").lower()
    if access not in {"headmouse", "openheadmouse"}:
        return [None] * len(trials)
    episodes = [_onscreen_trial(t, settings) for t in trials]
    if offscreen is not None:
        if len(offscreen) != len(trials):
            raise ValueError("Off-screen episode list must match trials")
        for i, found in enumerate(episodes):
            if found is not None and offscreen[i] is not None:
                episodes[i] = [(a, b) for a, b in found
                               if not any(a < d and b > c for c, d in offscreen[i])]
    return episodes


def _onscreen_trial(trial, settings):
    if not trial.trajectory:
        return None
    points = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
    if not np.isfinite(points).all() or np.any(np.diff(points[:, 0]) < 0):
        return None
    duration = (trial.end_host_time - trial.start_host_time
                if trial.start_host_time is not None and trial.end_host_time is not None
                else trial.end_time - trial.start_time)
    if duration <= 0 or points[0, 0] < 0 or points[-1, 0] > duration:
        return None
    if points[-1, 0] < duration:
        points = np.vstack((points, [duration, *trial.click_pos]))
    # Edge visits alone do not prove saturation; confirmed off-screen episodes take precedence.
    runs = []
    start = None
    for i, point in enumerate(points):
        if start is not None and (point[0] - points[i - 1, 0] > settings.max_cursor_gap_s
                                  or point[0] <= points[i - 1, 0]):
            runs.append(points[start:i])
            start = None
        if start is None:
            start = i
    if start is not None:
        runs.append(points[start:])
    runs = [run for run in runs if len(run) >= 3]
    if not runs:
        return None
    direction = np.asarray(trial.target_pos, dtype=float) - points[0, 1:]
    length = np.linalg.norm(direction)
    if length <= trial.width_px / 2:
        return []
    direction = direction / length
    return [episode for run in runs
            for episode in _onscreen_run(
                run, trial.target_pos, trial.width_px / 2, direction, settings,
            )]


def _onscreen_run(points, target, target_radius, direction, settings):
    remaining = (np.asarray(target, dtype=float) - points[:, 1:]) @ direction
    distance = np.linalg.norm(points[:, 1:] - np.asarray(target, dtype=float), axis=1)
    low = peak = end = 0
    state = "seeking"
    episodes = []

    def is_clutch(a, b, c):
        away = remaining[b] - remaining[a]
        back = remaining[b] - remaining[c]
        away_time = points[b, 0] - points[a, 0]
        back_time = points[c, 0] - points[b, 0]
        away_vector = points[b, 1:] - points[a, 1:]
        back_vector = points[c, 1:] - points[b, 1:]
        lengths = np.linalg.norm(away_vector) * np.linalg.norm(back_vector)
        opposition = -np.dot(away_vector, back_vector)
        return (distance[a] > target_radius and remaining[a] >= -target_radius
                and away >= settings.onscreen_retreat_px
                and away_time > 0 and back_time > 0
                and opposition > 0
                and opposition >= settings.onscreen_opposition * lengths
                and back / back_time > settings.onscreen_speed_ratio * away / away_time)

    for i in range(1, len(points)):
        if state == "seeking":
            if remaining[i] <= remaining[low]:
                low = i
            elif remaining[i] - remaining[low] >= settings.onscreen_retreat_px:
                peak, state = i, "away"
        elif state == "away":
            if remaining[i] >= remaining[peak]:
                peak = i
            elif remaining[peak] - remaining[i] >= settings.onscreen_retreat_px:
                end, state = i, "return"
        else:
            if remaining[i] < remaining[end]:
                end = i
            elif remaining[i] - remaining[end] >= settings.onscreen_retreat_px:
                low, peak, state = end, i, "away"
        if state == "return" and remaining[end] <= remaining[low]:
            if is_clutch(low, peak, end):
                episodes.append((float(points[low, 0]), float(points[end, 0])))
            low, state = end, "seeking"
    return episodes
