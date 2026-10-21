"""Load recordings and prepare Qt-free playback and participant statistics."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from core import quaternion_math as qm
from core.recorder import RecordedSample, Recorder
from core.session_stats import SessionStats
from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult
from session_viewer.mesh_visualizer import (
    OBJ_PATH, calculate_neutral_quaternion, centre_and_scale,
    head_rotation_to_mesh_frame, load_obj_wireframe, rotation_from_quaternion,
)
from session_viewer.pretest_stats import (
    format_pretest_summary_html, phase_description, pretest_records,
    pretest_time_range, summarise_pretest,
)


def _finite(values, label: str) -> None:
    if not np.isfinite(np.asarray(values, dtype=float)).all():
        raise ValueError(f"{label} contains non-finite values")


def _ordered(values, label: str) -> None:
    _finite(values, label)
    if np.any(np.diff(values) < 0):
        raise ValueError(f"{label} must be in chronological order")


@dataclass
class Session:
    participant: str
    name: str
    samples: list[RecordedSample] = field(default_factory=list)
    recorder: Recorder | None = None
    trials: list[TrialResult] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    head_filename: str = ""
    fitts_filename: str = ""

    @classmethod
    def load(cls, participant: str, name: str, *, head_path=None, fitts_path=None):
        participant, name = participant.strip(), name.strip()
        if not participant or not name:
            raise ValueError("Participant and session names are required")
        if head_path is None and fitts_path is None:
            raise ValueError("Choose a head recording, a Fitts recording, or both")
        session = cls(participant, name)
        if head_path is not None:
            session.recorder = Recorder()
            session.samples = session.recorder.load(Path(head_path))
            session.head_filename = Path(head_path).name
            if not session.samples:
                raise ValueError("The head recording has no movement samples")
            for sample in session.samples:
                if sample.metadata is None:
                    sample.metadata = {}
                if not isinstance(sample.metadata, dict):
                    raise ValueError("Head sample metadata must be an object")
                _finite([sample.timestamp, *sample.quaternion,
                         sample.yaw_deg, sample.pitch_deg, sample.roll_deg], "Head sample")
                if np.linalg.norm(sample.quaternion) == 0:
                    raise ValueError("A head sample has a zero quaternion")
                if sample.host_time is not None:
                    _finite([sample.host_time], "Head host time")
            _ordered(session.head_times()[0], "Head sample times")
            calculate_neutral_quaternion(session.samples, session.recorder)
        if fitts_path is not None:
            logger = FittsResultsLogger()
            session.trials = sorted(logger.load(fitts_path), key=lambda t: (t.start_time, t.trial_index))
            session.metadata = logger.metadata
            if not isinstance(session.metadata, dict):
                raise ValueError("Fitts metadata must be an object")
            session.fitts_filename = Path(fitts_path).name
            for trial in session.trials:
                _finite([trial.start_time, trial.end_time, trial.movement_time_s,
                         trial.amplitude_px, trial.width_px, *trial.start_pos,
                         *trial.target_pos, *trial.click_pos], "Fitts trial")
                if trial.end_time < trial.start_time or trial.movement_time_s < 0 or trial.width_px <= 0:
                    raise ValueError("Fitts trial has invalid duration or target width")
                trajectory = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
                _finite(trajectory, "Cursor trajectory")
                _ordered(trajectory[:, 0], "Cursor trajectory times")
                for value in (trial.start_host_time, trial.end_host_time):
                    if value is not None:
                        _finite([value], "Fitts host time")
            session.fitts_times()
        if session.samples and fitts_path is not None:
            session.synchronised_samples()  # Refuse misleading synchronization.
        return session

    def head_times(self):
        samples = self.samples
        times = (SessionStats(samples).times() if len(samples) > 1
                 else np.array([s.timestamp for s in samples]))
        hosts = [s.host_time for s in samples]
        aligned = (times + float(np.median(np.asarray(hosts) - times))
                   if samples and all(h is not None for h in hosts) else None)
        return times, aligned

    def fitts_times(self):
        host = all(t.start_host_time is not None and t.end_host_time is not None for t in self.trials)
        starts = np.array([t.start_host_time if host else t.start_time for t in self.trials], dtype=float)
        ends = np.array([t.end_host_time if host else t.end_time for t in self.trials], dtype=float)
        head_range = pretest_time_range(self.metadata) if host else None
        if not len(starts) and head_range is None:
            raise ValueError("The Fitts recording has no trials or timed head-movement tasks")
        _ordered(starts, "Fitts start times")
        _finite(ends, "Fitts end times")
        if np.any(ends < starts):
            raise ValueError("Fitts end times precede start times")
        spans = [head_range] if head_range else []
        if len(starts):
            spans.append((float(starts[0]), float(ends.max())))
        return starts, ends, host, (min(s for s, _ in spans), max(e for _, e in spans))

    def synchronised_samples(self):
        _, _, host, span = self.fitts_times()
        if not host or any(s.host_time is None for s in self.samples):
            raise ValueError("Cannot synchronise without shared host timestamps. Import these files as separate sessions.")
        hosts = [s.host_time for s in self.samples]
        if max(hosts) < span[0] or min(hosts) > span[1]:
            raise ValueError("The head and Fitts recordings do not overlap. Import the matching pair or each file separately.")
        samples = [s for s in self.samples if span[0] <= s.host_time <= span[1]]
        if len(samples) < 2:
            raise ValueError("The head recording has fewer than 2 samples during the Fitts test")
        return samples


def retained_trials(session: Session, exclude: int) -> list[TrialResult]:
    if isinstance(exclude, bool) or not isinstance(exclude, int) or exclude < 0:
        raise ValueError("Excluded targets must be a non-negative integer")
    # List position, not repetition_index: remove only the first N targets per file.
    return session.trials[exclude:]


def _numeric_leaves(values: dict, prefix: str):
    for key, value in values.items():
        label = f"{prefix}_{key}"
        if isinstance(value, dict):
            yield from _numeric_leaves(value, label)
        elif isinstance(value, (float, int)) and not isinstance(value, bool):
            _finite([value], label)
            yield label, value


def participant_rows(sessions: list[Session], exclusions: dict[str, int] | None = None) -> list[dict]:
    exclusions = exclusions or {}
    grouped: dict[str, list[Session]] = {}
    for session in sessions:
        grouped.setdefault(session.participant, []).append(session)
    rows = []
    for participant, recordings in sorted(grouped.items()):
        exclude = exclusions.get(participant, 0)
        trials = [t for s in recordings for t in retained_trials(s, exclude)]
        total = sum(len(s.trials) for s in recordings)
        row = {
            "participant": participant, "sessions": len(recordings),
            "head_files": sum(bool(s.head_filename) for s in recordings),
            "fitts_files": sum(bool(s.fitts_filename) for s in recordings),
            "exclude_first_targets_per_file": exclude,
            "targets_recorded": total, "targets_excluded": total - len(trials),
            "targets_analysed": len(trials), "hits": sum(t.hit for t in trials),
        }
        iso = FittsStats(trials).summary()
        for key, value in iso.items():
            if key.startswith("overall_"):
                row[key.removeprefix("overall_")] = value if trials else None
        row.update({f"path_{k}": v for k, v in FittsStats.accuracy_summary(trials).items()})
        speeds, accelerations, poses = [], [], []
        duration = 0.0
        pretests: dict[str, list[float]] = {}
        for session in recordings:
            if session.samples:
                neutral = calculate_neutral_quaternion(session.samples, session.recorder)
                angles = np.degrees(qm.to_euler_zyx(qm.relative_rotation(
                    neutral, np.array([s.quaternion for s in session.samples]))))
                poses.append(np.degrees(np.unwrap(np.radians(angles), axis=0)))
                if len(session.samples) > 1:
                    stats = SessionStats(session.samples)
                    duration += stats.session_duration()
                    speeds.extend(stats.angular_speed_series()[1])
                    accelerations.extend(stats.angular_acceleration_series()[1])
            pretest = summarise_pretest(session.metadata, session.samples)
            for section, values in pretest.items():
                if section != "configuration" and isinstance(values, dict):
                    for key, value in _numeric_leaves(values, section):
                        pretests.setdefault(f"{key}_session_mean", []).append(value)
        row["head_duration_s"] = duration
        row["head_samples"] = sum(len(s.samples) for s in recordings)
        for label, values in (("head_speed_deg_s", speeds), ("head_acceleration_deg_s2", accelerations)):
            row[f"{label}_mean"] = float(np.mean(values)) if values else None
            row[f"{label}_max"] = float(np.max(values)) if values else None
        angles = np.concatenate(poses) if poses else np.empty((0, 3))
        for i, axis in enumerate(("yaw", "pitch", "roll")):
            row[f"head_{axis}_range_deg"] = (
                float(np.ptp(angles[:, i])) if len(angles) else None
            )
        row.update({key: float(np.mean(values)) for key, values in pretests.items()})
        rows.append(row)
    return rows


def summary_csv(rows: list[dict]) -> str:
    output = io.StringIO(newline="")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    # Treat user labels as text when opened in spreadsheet software.
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                         for k, v in row.items()})
    return output.getvalue()


def playback_payload(session: Session, mesh_path: Path | None = None) -> dict:
    samples = session.samples
    starts, ends, host, span = (session.fitts_times() if session.fitts_filename else
                               (np.array([]), np.array([]), False, None))
    synced = bool(samples and session.fitts_filename)
    if synced:
        samples = session.synchronised_samples()
    frames = []
    head_graphs = []
    if samples:
        neutral = calculate_neutral_quaternion(samples, session.recorder, reference_samples=session.samples)
        stats = SessionStats(samples) if len(samples) > 1 else None
        times = stats.times() if stats else np.array([s.timestamp for s in samples])
        if synced:
            times = times + float(np.median(np.array([s.host_time for s in samples]) - times))
        else:
            span = (float(times[0]), float(times[-1]))
        origin = span[0]
        speeds = stats.angular_speed_per_sample() if stats else np.zeros(len(samples))
        accels = stats.angular_acceleration_per_sample() if stats else np.zeros(len(samples))
        for sample, timestamp, speed, accel in zip(samples, times, speeds, accels):
            relative = qm.relative_rotation(neutral, sample.quaternion)
            angles = np.degrees(qm.to_euler_zyx(relative)).tolist()
            rotation = head_rotation_to_mesh_frame(rotation_from_quaternion(*relative))
            frames.append({
                "t": float(timestamp - origin), "rotation": rotation.tolist(),
                "angles": angles, "quaternion": sample.quaternion.tolist(),
                "raw_angles": [sample.yaw_deg, sample.pitch_deg, sample.roll_deg],
                "speed": float(speed), "acceleration": float(accel),
                "phase": phase_description(sample.metadata.get("task_phase")),
            })
        head_graphs = [
            {"label": label, "points": [[f["t"], value(f)] for f in frames]}
            for label, value in (
                ("Yaw (deg)", lambda f: f["angles"][0]), ("Pitch (deg)", lambda f: f["angles"][1]),
                ("Angular speed (deg/s)", lambda f: f["speed"]),
                ("Angular acceleration (deg/s2)", lambda f: f["acceleration"]),
            )
        ]
    origin = span[0]
    trials, speed_points, distance_points = [], [], []
    for trial, start, end in zip(session.trials, starts, ends):
        item = asdict(trial)
        item["start"], item["end"] = float(start - origin), float(end - origin)
        trajectory = np.asarray(trial.trajectory, dtype=float).reshape(-1, 3)
        speeds = np.zeros(len(trajectory))
        if len(trajectory) > 1:
            dt = np.diff(trajectory[:, 0])
            np.divide(np.linalg.norm(np.diff(trajectory[:, 1:], axis=0), axis=1),
                      dt, out=speeds[1:], where=dt > 0)
        item["speeds"] = speeds.tolist()
        item["full_path"] = FittsStats.path_points(trial).tolist()
        for point, speed in zip(trajectory, speeds):
            t = float(start - origin + point[0])
            speed_points.append([t, float(speed)])
            distance_points.append([t, float(np.linalg.norm(point[1:] - trial.target_pos))])
        speed_points.append(None)
        distance_points.append(None)
        trials.append(item)
    records = []
    if host:
        for record in pretest_records(session.metadata):
            if record.get("start_host_time") is None:
                continue
            item = dict(record)
            item["start"] = float(record["start_host_time"] - origin)
            item["description"] = phase_description(record.get("phase"))
            if record.get("cancelled"):
                item["description"] += " (cancelled)"
            item["points"] = [[float(p["host_time"] - origin), float(p["x_px"]), float(p["y_px"])]
                              for p in record.get("cursor_trajectory", [])]
            _finite(item["points"], "Pre-test cursor trajectory")
            records.append(item)
    width, height = session.metadata.get("screen_width_px"), session.metadata.get("screen_height_px")
    if not width or not height:
        xs, ys = [], []
        for trial in session.trials:
            for x, y in (trial.start_pos, trial.target_pos, trial.click_pos):
                xs.append(x + trial.width_px / 2)
                ys.append(y + trial.width_px / 2)
            xs.extend(p[1] for p in trial.trajectory)
            ys.extend(p[2] for p in trial.trajectory)
        width, height = (max(max(xs) + 20, 100), max(max(ys) + 20, 100)) if xs else (1920, 1080)
    _finite([width, height], "Screen dimensions")
    if width <= 0 or height <= 0:
        raise ValueError("Screen dimensions must be positive")
    geometry = {"vertices": [], "edges": []}
    if frames:
        vertices, _, edges = load_obj_wireframe(mesh_path or OBJ_PATH)
        geometry = {"vertices": centre_and_scale(vertices).tolist(), "edges": edges.tolist()}
    head_range = pretest_time_range(session.metadata) if host else None
    return {
        "title": f"{session.participant} / {session.name}",
        "clock_origin": float(origin),
        "duration": float(span[1] - origin), "synced": synced,
        "frames": frames, "trials": trials, "pretests": records,
        "head_task_end": head_range[1] - origin if head_range else None,
        "screen": [float(width), float(height)], "geometry": geometry,
        "head_graphs": head_graphs,
        "cursor_graphs": [{"label": "Cursor speed (px/s)", "points": speed_points},
                          {"label": "Distance to target (px)", "points": distance_points}] if trials else [],
        "pretest_html": format_pretest_summary_html(summarise_pretest(session.metadata, session.samples)),
        "session_summary": participant_rows([session])[0],
    }


def payload_json(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
