"""Summary statistics for the Fitts pre-test tasks.

A Fitts session file records the optional *Initial Rest Position* and *Range Of
Movement* tasks under ``metadata["pretest_phases"]`` (see
`fitts_law.pretest.build_pretest_stages`). Each record holds the cursor
trajectory sampled during that phase, so cursor-based statistics are available
even when the Fitts file is opened on its own.

When a head-movement session recorded at the same time is also available, the
head samples carry ``metadata["task_phase"]`` labels with the same phase names,
which gives the pose (yaw/pitch/roll) statistics as well.
"""

from __future__ import annotations

import math

DIRECTIONS = ("left", "right", "up", "down")

#: Cursor axis and the extreme taken along it, per range-of-movement direction.
_DIRECTION_AXIS = {
    "left": ("x", min),
    "right": ("x", max),
    "up": ("y", min),
    "down": ("y", max),
}


def pretest_records(metadata: dict | None) -> list[dict]:
    """The pre-test phase records stored in a Fitts session's metadata."""
    phases = (metadata or {}).get("pretest_phases") or []
    return [record for record in phases if isinstance(record, dict)]


def pretest_time_range(metadata: dict | None) -> tuple[float, float] | None:
    """Playable host-clock extent of completed or cancelled head-task phases."""
    ranges = [
        (float(record["start_host_time"]), float(record["end_host_time"]))
        for record in pretest_records(metadata)
        if record.get("start_host_time") is not None
        and record.get("end_host_time") is not None
    ]
    ranges = [(start, end) for start, end in ranges if math.isfinite(start) and math.isfinite(end) and end >= start]
    return (min(start for start, _ in ranges), max(end for _, end in ranges)) if ranges else None


def phase_description(phase: str | None) -> str:
    """Human-readable description of a recorded task phase label."""
    if not phase:
        return "Not in a test task"
    if phase == "fitts_test":
        return "Fitts mouse task - pointing at and clicking targets"
    parts = phase.split(".")
    if parts[0] == "initial_rest":
        if parts[-1] == "move":
            return "Initial head position - moving into a comfortable neutral position"
        return "Initial head position - holding still in the neutral position"
    if parts[0] == "range_of_movement" and len(parts) == 3:
        direction = parts[1].upper()
        if parts[2] == "move":
            return f"Range of movement - moving head as far {direction} as is comfortable"
        return f"Range of movement - holding head at furthest {direction} position"
    return phase


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _duration_s(record: dict) -> float | None:
    start = record.get("start_host_time")
    end = record.get("end_host_time")
    if start is None or end is None:
        return None
    return float(end) - float(start)


def _find_record(records: list[dict], task: str, segment: str, direction=None) -> dict | None:
    for record in records:
        if (
            record.get("task") == task
            and record.get("segment") == segment
            and record.get("direction") == direction
        ):
            return record
    return None


def _cursor_xy(record: dict | None) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for point in (record or {}).get("cursor_trajectory") or []:
        try:
            xs.append(float(point["x_px"]))
            ys.append(float(point["y_px"]))
        except (KeyError, TypeError, ValueError):
            continue
    return xs, ys


def cursor_initial_rest_stats(records: list[dict]) -> dict | None:
    """Cursor steadiness during the initial-rest hold phase."""
    record = _find_record(records, "initial_rest", "hold")
    if record is None:
        return None
    xs, ys = _cursor_xy(record)
    mean_x, mean_y = _mean(xs), _mean(ys)
    max_deviation = None
    if mean_x is not None and mean_y is not None:
        max_deviation = max(math.hypot(x - mean_x, y - mean_y) for x, y in zip(xs, ys))
    return {
        "duration_s": _duration_s(record),
        "sample_count": len(xs),
        "mean_x_px": mean_x,
        "mean_y_px": mean_y,
        "std_x_px": _std(xs),
        "std_y_px": _std(ys),
        "max_deviation_px": max_deviation,
        "cancelled": bool(record.get("cancelled")),
    }


def cursor_range_of_movement_stats(records: list[dict]) -> dict | None:
    """Cursor extremes reached during each range-of-movement hold phase."""
    directions: dict[str, dict] = {}
    for direction in DIRECTIONS:
        record = _find_record(records, "range_of_movement", "hold", direction)
        if record is None:
            continue
        xs, ys = _cursor_xy(record)
        axis, pick = _DIRECTION_AXIS[direction]
        values = xs if axis == "x" else ys
        directions[direction] = {
            "axis": axis,
            "sample_count": len(values),
            "extreme_px": pick(values) if values else None,
            "mean_px": _mean(values),
            "duration_s": _duration_s(record),
            "cancelled": bool(record.get("cancelled")),
        }
    if not directions:
        return None

    def _extreme(direction: str):
        entry = directions.get(direction)
        return entry["extreme_px"] if entry else None

    left, right = _extreme("left"), _extreme("right")
    up, down = _extreme("up"), _extreme("down")
    return {
        "directions": directions,
        "horizontal_range_px": None if left is None or right is None else abs(right - left),
        "vertical_range_px": None if up is None or down is None else abs(down - up),
    }


def _phase_samples(samples, phase: str) -> list:
    return [
        s for s in samples
        if (getattr(s, "metadata", None) or {}).get("task_phase") == phase
    ]


def _pose_stats(samples) -> dict:
    yaw = [float(s.yaw_deg) for s in samples]
    pitch = [float(s.pitch_deg) for s in samples]
    roll = [float(s.roll_deg) for s in samples]
    host_times = [s.host_time for s in samples if getattr(s, "host_time", None) is not None]
    return {
        "sample_count": len(samples),
        "duration_s": (max(host_times) - min(host_times)) if len(host_times) > 1 else None,
        "yaw_mean_deg": _mean(yaw),
        "pitch_mean_deg": _mean(pitch),
        "roll_mean_deg": _mean(roll),
        "yaw_std_deg": _std(yaw),
        "pitch_std_deg": _std(pitch),
        "roll_std_deg": _std(roll),
    }


def head_initial_rest_stats(samples) -> dict | None:
    """Head pose held during ``initial_rest.hold``, from a head-movement session."""
    rest = _phase_samples(samples, "initial_rest.hold")
    return _pose_stats(rest) if rest else None


def head_range_of_movement_stats(samples, rest: dict | None = None) -> dict | None:
    """Head pose extremes held during each ``range_of_movement.*.hold`` phase."""
    directions: dict[str, dict] = {}
    for direction in DIRECTIONS:
        held = _phase_samples(samples, f"range_of_movement.{direction}.hold")
        if not held:
            continue
        stats = _pose_stats(held)
        angle = "yaw" if direction in ("left", "right") else "pitch"
        stats["angle"] = angle
        stats["angle_mean_deg"] = stats[f"{angle}_mean_deg"]
        rest_mean = (rest or {}).get(f"{angle}_mean_deg")
        stats["offset_from_rest_deg"] = (
            None if rest_mean is None or stats["angle_mean_deg"] is None
            else stats["angle_mean_deg"] - rest_mean
        )
        directions[direction] = stats
    if not directions:
        return None

    def _angle(direction: str):
        entry = directions.get(direction)
        return entry["angle_mean_deg"] if entry else None

    left, right = _angle("left"), _angle("right")
    up, down = _angle("up"), _angle("down")
    return {
        "directions": directions,
        "yaw_range_deg": None if left is None or right is None else abs(right - left),
        "pitch_range_deg": None if up is None or down is None else abs(down - up),
    }


def summarise_pretest(metadata: dict | None, head_samples=None) -> dict:
    """Combined cursor and (optional) head-pose pre-test statistics."""
    records = pretest_records(metadata)
    head_samples = list(head_samples or [])
    head_rest = head_initial_rest_stats(head_samples)
    summary = {
        "configuration": (metadata or {}).get("pretest_configuration") or {},
        "cursor_initial_rest": cursor_initial_rest_stats(records),
        "cursor_range_of_movement": cursor_range_of_movement_stats(records),
        "head_initial_rest": head_rest,
        "head_range_of_movement": head_range_of_movement_stats(head_samples, head_rest),
    }
    summary["available"] = any(
        summary[key] for key in (
            "cursor_initial_rest", "cursor_range_of_movement",
            "head_initial_rest", "head_range_of_movement",
        )
    )
    return summary


def _fmt(value, spec: str, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:{spec}}{unit}"


def format_pretest_summary_html(summary: dict) -> str:
    """Render `summarise_pretest` output for the Fitts playback side panel."""
    if not summary.get("available"):
        return (
            "No initial rest position or range of movement data was recorded "
            "for this session."
        )

    lines: list[str] = []
    rest = summary.get("cursor_initial_rest")
    head_rest = summary.get("head_initial_rest")
    if rest or head_rest:
        lines.append("<b>Initial head pose</b>")
    if head_rest:
        lines.append(
            f"Yaw: {_fmt(head_rest['yaw_mean_deg'], '+.2f', '°')} "
            f"± {_fmt(head_rest['yaw_std_deg'], '.2f', '°')}"
        )
        lines.append(
            f"Pitch: {_fmt(head_rest['pitch_mean_deg'], '+.2f', '°')} "
            f"± {_fmt(head_rest['pitch_std_deg'], '.2f', '°')}"
        )
        lines.append(
            f"Roll: {_fmt(head_rest['roll_mean_deg'], '+.2f', '°')} "
            f"± {_fmt(head_rest['roll_std_deg'], '.2f', '°')}"
        )
        lines.append(
            f"Head samples: {head_rest['sample_count']} over "
            f"{_fmt(head_rest['duration_s'], '.1f', ' s')}"
        )
    if rest:
        lines.append(
            f"Cursor drift: σx {_fmt(rest['std_x_px'], '.1f', ' px')}, "
            f"σy {_fmt(rest['std_y_px'], '.1f', ' px')}"
        )
        lines.append(f"Max deviation: {_fmt(rest['max_deviation_px'], '.1f', ' px')}")
        lines.append(
            f"Hold: {_fmt(rest['duration_s'], '.1f', ' s')}, "
            f"{rest['sample_count']} cursor points"
            + (" (cancelled)" if rest["cancelled"] else "")
        )

    head_rom = summary.get("head_range_of_movement")
    cursor_rom = summary.get("cursor_range_of_movement")
    if head_rom or cursor_rom:
        if lines:
            lines.append("")
        lines.append("<b>Range of movement</b>")
    if head_rom:
        for direction in DIRECTIONS:
            entry = head_rom["directions"].get(direction)
            if entry is None:
                continue
            lines.append(
                f"{direction.capitalize()}: {entry['angle']} "
                f"{_fmt(entry['angle_mean_deg'], '+.2f', '°')} "
                f"(Δrest {_fmt(entry['offset_from_rest_deg'], '+.2f', '°')})"
            )
        lines.append(f"Yaw range: {_fmt(head_rom['yaw_range_deg'], '.2f', '°')}")
        lines.append(f"Pitch range: {_fmt(head_rom['pitch_range_deg'], '.2f', '°')}")
    if cursor_rom:
        for direction in DIRECTIONS:
            entry = cursor_rom["directions"].get(direction)
            if entry is None:
                continue
            lines.append(
                f"{direction.capitalize()}: {entry['axis']} "
                f"{_fmt(entry['extreme_px'], '.0f', ' px')}"
                + (" (cancelled)" if entry["cancelled"] else "")
            )
        lines.append(
            f"Cursor H range: {_fmt(cursor_rom['horizontal_range_px'], '.0f', ' px')}"
        )
        lines.append(
            f"Cursor V range: {_fmt(cursor_rom['vertical_range_px'], '.0f', ' px')}"
        )

    return "<br>".join(lines)
