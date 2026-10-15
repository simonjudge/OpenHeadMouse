"""Locate the head-movement recording made alongside a Fitts session file."""

from __future__ import annotations

import re
from pathlib import Path

from core.recorder import Recorder
from session_viewer.pretest_stats import pretest_time_range

_MAX_ID_GAP_S = 600


def _session_id(path: Path) -> int | None:
    match = re.search(r"(\d{6,})$", path.stem)
    return int(match.group(1)) if match else None


def _fitts_span(metadata: dict | None, trials) -> tuple[float, float] | None:
    spans = []
    head_range = pretest_time_range(metadata)
    if head_range is not None:
        spans.append(head_range)
    timed = [t for t in trials or [] if t.start_host_time is not None and t.end_host_time is not None]
    if timed:
        spans.append((min(t.start_host_time for t in timed), max(t.end_host_time for t in timed)))
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)


def _candidate_paths(fitts_path: Path, metadata: dict | None) -> list[Path]:
    candidates: list[Path] = []
    recorded = (metadata or {}).get("head_session_file")
    folders = [fitts_path.parent.parent / "sessions", fitts_path.parent / "sessions", fitts_path.parent]
    if recorded:
        recorded = Path(recorded)
        candidates.append(recorded)
        candidates.extend(folder / recorded.name for folder in folders)
    fitts_id = _session_id(fitts_path)
    if fitts_id is not None:
        nearby = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for path in folder.glob("session_*.json"):
                head_id = _session_id(path)
                if head_id is not None and abs(head_id - fitts_id) <= _MAX_ID_GAP_S:
                    nearby.append((abs(head_id - fitts_id), path))
        candidates.extend(path for _, path in sorted(nearby))
    unique: list[Path] = []
    for path in candidates:
        if path not in unique and path.resolve() != fitts_path.resolve():
            unique.append(path)
    return unique


def find_head_session_for_fitts(fitts_path, metadata: dict | None, trials=()):
    """Return ``(path, recorder, samples)`` for the matching head recording, or None.

    The recording named in ``metadata["head_session_file"]`` is preferred;
    otherwise the ``session_*.json`` saved closest in time is used. A
    candidate is accepted only if its host timestamps overlap the Fitts
    session's head-task phases or trials.
    """
    fitts_path = Path(fitts_path)
    span = _fitts_span(metadata, trials)
    if span is None:
        return None
    for path in _candidate_paths(fitts_path, metadata):
        if not path.is_file():
            continue
        try:
            recorder = Recorder()
            samples = recorder.load(path)
        except Exception:
            continue
        times = [s.host_time for s in samples if getattr(s, "host_time", None) is not None]
        if times and min(times) <= span[1] and span[0] <= max(times):
            return path, recorder, samples
    return None
