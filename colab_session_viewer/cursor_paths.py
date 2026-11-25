"""Cumulative Fitts cursor paths for a participant, as a downloadable SVG."""

from __future__ import annotations

import base64
import hashlib
import html
import re
from collections import defaultdict

import numpy as np

from colab_session_viewer.data import Session, fitts_screen_dimensions, retained_trials
from fitts_law.clutch import (
    DEFAULT_CLUTCH_SETTINGS, ClutchSettings, clutch_episodes, onscreen_episodes,
)
from fitts_law.results import FittsStats, TrialResult

COLORS = ("#0057b8", "#cf281c", "#008442", "#a644ae", "#a05b00", "#007f87",
          "#cf4890", "#4a4b99", "#5f7610", "#a54012")


def numbered_targets(sessions: list[Session], exclude: int) -> list[tuple[int, Session, int, TrialResult, bool]]:
    """Number a participant's recorded Fitts targets 1..N across all files.

    Files are ordered by their first saved host time (files without one keep
    import order, after timed files); trials keep their saved order within a
    file. Returns (number, session, file target number, trial, retained);
    first-N excluded targets keep their numbers but are marked not retained.
    """
    files = [s for s in sessions if s.fitts_filename and s.trials]
    order = sorted(
        range(len(files)),
        key=lambda i: (files[i].trials[0].start_host_time is None,
                       files[i].trials[0].start_host_time or 0, i),
    )
    numbered, number = [], 0
    for i in order:
        session = files[i]
        retained = {id(t) for t in retained_trials(session, exclude)}
        for file_number, trial in enumerate(session.trials, start=1):
            number += 1
            numbered.append((number, session, file_number, trial, id(trial) in retained))
    return numbered


def parse_target_selection(text: str, maximum: int) -> list[int]:
    """Parse '1-12', '1,2,24' or '3-5, 9' into sorted unique target numbers.

    Blank or 'all' selects 1..maximum. Numbers must lie within 1..maximum.
    """
    text = text.strip().lower()
    if text in ("", "all"):
        return list(range(1, maximum + 1))
    selected = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*[-–]\s*(\d+))?", part)
        if not match:
            raise ValueError(f"Cannot read target selection '{part}'; use e.g. 1-12 or 1,2,24")
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        if first > last:
            raise ValueError(f"Target range '{part}' must run from low to high")
        if first < 1 or last > maximum:
            raise ValueError(f"Target numbers must be between 1 and {maximum}")
        selected.update(range(first, last + 1))
    if not selected:
        raise ValueError("Enter at least one target number, e.g. 1-12 or 1,2,24")
    return sorted(selected)


def format_target_selection(numbers) -> str:
    """Compact sorted numbers as '1-3, 7, 9-10'."""
    numbers = sorted(set(numbers))
    parts, index = [], 0
    while index < len(numbers):
        end = index
        while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
            end += 1
        parts.append(str(numbers[index]) if end == index else f"{numbers[index]}-{numbers[end]}")
        index = end + 1
    return ", ".join(parts)


def _episode_paths(trial: TrialResult, start: float, end: float, max_gap: float):
    """Clip recorded paths to an episode, without bridging missing samples."""
    points = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
    duration = (trial.end_host_time - trial.start_host_time
                if trial.start_host_time is not None and trial.end_host_time is not None
                else trial.end_time - trial.start_time)
    if points[-1, 0] < duration:
        points = np.vstack((points, [duration, *trial.click_pos]))
    gaps = np.diff(points[:, 0])
    breaks = np.flatnonzero((gaps <= 0) | (gaps > max_gap)) + 1
    for run in np.split(points, breaks):
        first, last = max(start, run[0, 0]), min(end, run[-1, 0])
        if last < first:
            continue
        times = np.r_[first, run[(run[:, 0] > first) & (run[:, 0] < last), 0], last]
        yield np.column_stack([np.interp(times, run[:, 0], run[:, axis]) for axis in (1, 2)])


def cursor_path_plot(
    sessions: list[Session], selection, exclude: int,
    settings: ClutchSettings = DEFAULT_CLUTCH_SETTINGS,
) -> str:
    """Render one participant's selected targets (participant-wide 1-based numbers)."""
    selection = set(selection)
    if not selection or min(selection) < 1:
        raise ValueError("Target selection must contain target numbers of 1 or greater")
    if len({s.participant for s in sessions}) > 1:
        raise ValueError("Cursor path plot must contain only one participant")
    groups = defaultdict(list)
    for number, session, file_number, trial, retained in numbered_targets(sessions, exclude):
        if retained and number in selection:
            groups[fitts_screen_dimensions(session)].append((session, number, file_number, trial))
    if not groups:
        return "<p>No retained Fitts targets in this selection.</p>"

    participant = sessions[0].participant
    clip_prefix = "cursor-" + hashlib.sha256(participant.encode("utf-8")).hexdigest()
    panel_width = 700
    y = 45.0
    elements, legend, screens = [], [], []
    for panel_index, ((width, height, estimated), trials) in enumerate(groups.items()):
        clip_id = f"{clip_prefix}-{panel_index}"
        scale = min(panel_width / width, 460 / height)
        w, h = width * scale, height * scale
        top = y
        screens.append(f"Screen {width:g} x {height:g} px"
                       f'{" (estimated from recording)" if estimated else ""}')
        elements.append(
            f'<rect x="20" y="{top:.1f}" width="{w:.3f}" height="{h:.3f}" '
            'fill="#fff" stroke="#333"/>'
            f'<defs><clipPath id="{clip_id}"><rect x="20" y="{top:.3f}" '
            f'width="{w:.3f}" height="{h:.3f}"/></clipPath></defs>'
        )
        contents = []
        by_session = defaultdict(list)
        for entry in trials:
            by_session[id(entry[0])].append(entry)
        estimates = {}
        for entries in by_session.values():
            session = entries[0][0]
            selected_trials = [entry[3] for entry in entries]
            offscreen = clutch_episodes(selected_trials, session.samples, session.metadata, settings)
            onscreen = onscreen_episodes(selected_trials, session.metadata, settings, offscreen=offscreen)
            for entry, edge, reset in zip(entries, offscreen, onscreen):
                estimates[(id(session), entry[2])] = (edge, reset)
        for session, index, file_number, trial in trials:
            color = COLORS[(index - 1) % len(COLORS)]
            label = f"Target {index} ({session.name}, file target {file_number})"
            legend.append((color, label, session.fitts_filename))
            tx, ty = trial.target_pos
            cx, cy = trial.click_pos
            title = html.escape(f"{label} - {'hit' if trial.hit else 'miss'}")
            points = FittsStats.path_points(trial)
            coords = " ".join(f"{20 + x * scale:.3f},{top + yy * scale:.3f}" for x, yy in points)
            highlights = []
            edge, reset = estimates[(id(session), file_number)]
            for episodes, kind, mark, highlight_color in (
                (reset, "onscreen", "O", "#00695c"), (edge, "offscreen", "E", "#9c3d10"),
            ):
                for start, end in episodes or []:
                    for path in _episode_paths(trial, start, end, settings.max_cursor_gap_s):
                        highlighted = " ".join(
                            f"{20 + x * scale:.3f},{top + yy * scale:.3f}" for x, yy in path
                        )
                        highlights.append(
                            f'<polyline class="clutch-highlight clutch-{kind}" data-start="{start:g}" '
                            f'data-end="{end:g}" points="{highlighted}" fill="none" '
                            f'stroke="{highlight_color}" stroke-width="8" opacity="0.45" '
                            'stroke-linecap="round" stroke-linejoin="round">'
                            f'<title>{mark}: {kind} clutch, {start:g}-{end:g} s from trial start</title>'
                            '</polyline>'
                        )
            contents.append(
                f'<g><title>{title}</title>'
                f'<circle cx="{20 + tx * scale:.3f}" cy="{top + ty * scale:.3f}" '
                f'r="{trial.width_px * scale / 2:.3f}" fill="none" stroke="{color}" stroke-width="1.2"/>'
                f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.5" '
                'stroke-linecap="round" stroke-linejoin="round" opacity="0.8"/>' + "".join(highlights) +
                f'<circle cx="{20 + cx * scale:.3f}" cy="{top + cy * scale:.3f}" r="2" fill="{color}"/></g>'
            )
        elements.append(f'<g clip-path="url(#{clip_id})">' + "".join(contents) + "</g>")
        y = top + h + 30
    elements.append(
        f'<g aria-label="Clutch highlight key"><line x1="24" x2="48" y1="{y - 14:g}" '
        f'y2="{y - 14:g}" stroke="#00695c" stroke-width="8" opacity="0.45"/>'
        f'<text x="58" y="{y - 9:g}" font-size="13">O: on-screen clutch</text>'
        f'<line x1="230" x2="254" y1="{y - 14:g}" y2="{y - 14:g}" stroke="#9c3d10" '
        f'stroke-width="8" opacity="0.45"/>'
        f'<text x="264" y="{y - 9:g}" font-size="13">E: off-screen clutch</text></g>'
    )
    title = f"{participant}: cumulative cursor paths, targets {format_target_selection(selection)}"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(title, quote=True)}" '
        f'viewBox="0 0 740 {y:.1f}" width="740" height="{y:.1f}">'
        f'<rect width="100%" height="100%" fill="white"/><text x="20" y="26" '
        f'font-size="17" font-weight="bold">{html.escape(title)}</text>'
        + "".join(elements) + "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", participant).strip("_") or "participant"
    legend_html = "".join(
        f'<li><span style="color:{color}">&#9632;</span> {html.escape(label)} '
        f'({html.escape(filename)})</li>' for color, label, filename in legend
    )
    return (
        f'<div style="max-width:100%;overflow-x:auto">{svg}</div>'
        '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">'
        f'<a download="cumulative_cursor_paths_{filename}.svg" href="data:image/svg+xml;base64,{encoded}">Download image (SVG)</a>'
        f'<span>{html.escape("; ".join(screens))}</span></div>'
        '<p>Clutch highlights use the current estimate settings. Hover over a highlight for its '
        'trial-relative interval. No highlight means none detected or insufficient data, not proof '
        'of no clutching.</p>'
        f'<details><summary>Path colours and source files</summary><ul>{legend_html}</ul></details>'
    )
