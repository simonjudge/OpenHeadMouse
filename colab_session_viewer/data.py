"""Load recordings and prepare Qt-free playback and participant statistics."""

from __future__ import annotations

import csv
import io
import json
import html
from dataclasses import asdict, dataclass, field, replace
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


def calibration_mean_yaw(session: Session) -> float | None:
    """Heading of the saved mean calibration quaternion, never an inferred neutral."""
    calibration = session.recorder.calibration if session.recorder else None
    samples = session.recorder.calibration_samples if session.recorder else []
    reference = (qm.mean_quaternion(s.quaternion for s in samples) if samples
                 else calibration.get("neutral_quaternion") if calibration else None)
    if reference is None:
        return None
    reference = np.asarray(reference, dtype=float)
    _finite(reference, "Calibration quaternion")
    if reference.shape != (4,) or np.linalg.norm(reference) == 0:
        raise ValueError("Invalid calibration quaternion")
    return float(np.degrees(qm.to_euler_zyx(reference)[0]))


def _circular_mean_deg(values) -> float:
    radians = np.radians(values)
    return float(np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())))


def head_pretest_summary(session: Session) -> dict:
    """Explicit provider-frame and calibration-heading-relative initial yaw."""
    calibration_yaw = calibration_mean_yaw(session)
    samples = session.samples
    if calibration_yaw is not None:
        heading = qm.from_euler_zyx(np.radians(calibration_yaw), 0, 0)
        samples = [
            replace(s, yaw_deg=float(np.degrees(qm.to_euler_zyx(
                qm.relative_rotation(heading, s.quaternion))[0])))
            for s in samples
        ]
    summary = summarise_pretest(session.metadata, samples)
    rest = summary["head_initial_rest"]
    if rest:
        absolute_yaw = [
            float(np.degrees(qm.to_euler_zyx(s.quaternion)[0]))
            for s in session.samples if s.metadata.get("task_phase") == "initial_rest.hold"
        ]
        rest["yaw_absolute_mean_deg"] = _circular_mean_deg(absolute_yaw)
        rest["yaw_calibration_mean_deg"] = calibration_yaw
        if calibration_yaw is None:
            rest["yaw_mean_deg"] = None
        else:
            yaw = np.radians([s.yaw_deg for s in samples
                              if s.metadata.get("task_phase") == "initial_rest.hold"])
            rest["yaw_mean_deg"] = _circular_mean_deg(np.degrees(yaw))
            rest["yaw_std_deg"] = float(np.std(np.degrees(np.unwrap(yaw))))
        rest["yaw_relative_mean_deg"] = rest["yaw_mean_deg"]
    return summary


def head_range_diagnostics(session: Session) -> dict | None:
    """Quaternion-relative Euler spans within one reference frame, never between files."""
    if not session.samples:
        return None
    neutral = calculate_neutral_quaternion(session.samples, session.recorder)
    quaternions = np.array([s.quaternion for s in session.samples])
    angles = np.degrees(qm.to_euler_zyx(qm.relative_rotation(neutral, quaternions)))
    unwrapped = np.degrees(np.unwrap(np.radians(angles), axis=0))
    fitts = np.array([s.metadata.get("task_phase") == "fitts_test" for s in session.samples])
    row = {"participant": session.participant, "session": session.name,
           "head_file": session.head_filename, "samples": len(angles),
           "fitts_samples": int(fitts.sum()),
           "max_abs_relative_pitch_deg": float(np.abs(angles[:, 1]).max()),
           "near_gimbal_samples": int(np.count_nonzero(np.abs(angles[:, 1]) >= 80))}
    for i, axis in enumerate(("yaw", "pitch", "roll")):
        row[f"{axis}_range_deg"] = float(np.ptp(unwrapped[:, i]))
        row[f"fitts_{axis}_range_deg"] = float(np.ptp(unwrapped[fitts, i])) if fitts.any() else None
    return row


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
        speeds, accelerations, ranges = [], [], []
        duration = 0.0
        pretests: dict[str, list[float]] = {}
        for session in recordings:
            if session.samples:
                diagnostics = head_range_diagnostics(session)
                ranges.append([diagnostics[f"{axis}_range_deg"] for axis in ("yaw", "pitch", "roll")])
                if len(session.samples) > 1:
                    stats = SessionStats(session.samples)
                    duration += stats.session_duration()
                    speeds.extend(stats.angular_speed_series()[1])
                    accelerations.extend(stats.angular_acceleration_series()[1])
            pretest = head_pretest_summary(session)
            for section, values in pretest.items():
                if section.startswith("head_") and isinstance(values, dict):
                    for key, value in _numeric_leaves(values, section):
                        pretests.setdefault(f"{key}_session_mean", []).append(value)
            rom_samples = [
                s for s in session.samples
                if (s.metadata.get("task_phase") or "").startswith("range_of_movement.")
            ]
            if rom_samples:
                rom_angles = np.array([[s.yaw_deg, s.pitch_deg, s.roll_deg] for s in rom_samples])
                rom_angles = np.degrees(np.unwrap(np.radians(rom_angles), axis=0))
                for index, axis in enumerate(("yaw", "pitch", "roll")):
                    pretests.setdefault(
                        f"head_range_of_movement_task_{axis}_range_deg_session_mean", []
                    ).append(float(np.ptp(rom_angles[:, index])))
        row["head_duration_s"] = duration
        row["head_samples"] = sum(len(s.samples) for s in recordings)
        for label, values in (("head_speed_deg_s", speeds), ("head_acceleration_deg_s2", accelerations)):
            row[f"{label}_mean"] = float(np.mean(values)) if values else None
            row[f"{label}_max"] = float(np.max(values)) if values else None
        for i, axis in enumerate(("yaw", "pitch", "roll")):
            row[f"head_{axis}_range_deg"] = (
                float(np.max(np.array(ranges)[:, i])) if ranges else None
            )
        row.update({key: float(np.mean(values)) for key, values in pretests.items()})
        for yaw_key in ("head_initial_rest_yaw_mean_deg_session_mean",
                        "head_initial_rest_yaw_absolute_mean_deg_session_mean",
                        "head_initial_rest_yaw_relative_mean_deg_session_mean",
                        "head_initial_rest_yaw_calibration_mean_deg_session_mean"):
            if yaw_key in pretests:
                row[yaw_key] = _circular_mean_deg(pretests[yaw_key])
        rows.append(row)
    return rows


def table_sources(sessions: list[Session], label: str) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    for session in sessions:
        names = sources.setdefault(session.participant, [])
        if label.startswith("Fitts"):
            files = [session.fitts_filename]
        elif label in ("Initial head positions", "Range of movement stats"):
            phase = "initial_rest.hold" if label == "Initial head positions" else "range_of_movement."
            matches = any(
                (s.metadata.get("task_phase") or "") == phase if phase == "initial_rest.hold"
                else (s.metadata.get("task_phase") or "").startswith(phase)
                for s in session.samples
            )
            files = [session.head_filename] if matches else []
        elif label == "Head movement stats":
            files = [session.head_filename]
        else:
            files = [session.head_filename, session.fitts_filename]
        for filename in files:
            if filename and filename not in names:
                names.append(filename)
    return sources


def column_label(key: str) -> str:
    """Short, wrapping display headings; CSVs keep the full machine-readable names."""
    if key in ("head_files", "head_samples", "head_duration_s"):
        return {"head_files": "Head files", "head_samples": "Head samples",
                "head_duration_s": "Head duration (s)"}[key]
    if key.startswith("head_initial_rest_yaw_") and key.endswith("_mean_deg_session_mean"):
        kind = key.removeprefix("head_initial_rest_yaw_").removesuffix("_mean_deg_session_mean")
        if kind in ("absolute", "relative", "calibration"):
            return f"Yaw {kind} (deg)"
    label = key.removesuffix("_session_mean")
    calibrated_yaw = key == "head_initial_rest_yaw_mean_deg_session_mean"
    for prefix in ("head_initial_rest_", "head_range_of_movement_", "head_"):
        if label.startswith(prefix):
            label = label.removeprefix(prefix)
            break
    label = label.replace("directions_", "").replace("task_", "")
    label = label.replace("offset_from_rest_deg", "offset_deg")
    label = label.replace("angle_mean_deg", "mean_deg")
    label = label.replace("effective_", "eff_").replace("movement_time", "MT")
    label = label.replace("acceleration", "accel").replace("index_of_difficulty", "ID")
    label = label.replace("_std", "_SD")
    label = label.replace("exclude_first_targets_per_file", "exclude_N_per_file")
    label = label.replace("deg_s2", "(deg/s2)").replace("deg_s", "(deg/s)")
    label = label.replace("_deg", " (deg)").replace("_px", " (px)")
    label = label.replace("_bits_per_s", " (bits/s)").replace("_s", " s") if label.endswith("_s") else label
    label = label.replace("_", " ")
    return label + " (cal.)" if calibrated_yaw else label


def participant_tables(rows: list[dict]) -> dict[str, list[dict]]:
    """Present disjoint metric groups, retaining one row per participant in each."""
    session_columns = (
        "sessions", "head_files", "fitts_files", "head_duration_s", "head_samples",
    )
    movement_columns = (
        "head_speed_deg_s_mean", "head_speed_deg_s_max",
        "head_acceleration_deg_s2_mean", "head_acceleration_deg_s2_max",
        "head_yaw_range_deg", "head_pitch_range_deg", "head_roll_range_deg",
    )
    target_columns = (
        "exclude_first_targets_per_file", "targets_recorded", "targets_excluded",
        "targets_analysed", "hits", "error_rate",
    )
    fitts_columns = (
        "mean_movement_time_s",
        "effective_amplitude_px", "effective_width_px", "index_of_difficulty_bits",
        "throughput_bits_per_s", "path_trial_count", "path_tre", "path_tac",
        "path_mdc", "path_odc", "path_mv_px", "path_me_px", "path_mo_px",
    )
    initial = [f"head_initial_rest_yaw_{kind}_mean_deg_session_mean"
               for kind in ("absolute", "relative", "calibration")]
    initial += [f"head_initial_rest_{axis}_mean_deg_session_mean" for axis in ("pitch", "roll")]
    initial += [f"head_initial_rest_{axis}_std_deg_session_mean" for axis in ("yaw", "pitch", "roll")]
    initial += ["head_initial_rest_sample_count_session_mean", "head_initial_rest_duration_s_session_mean"]
    rom = [f"head_range_of_movement_task_{axis}_range_deg_session_mean"
           for axis in ("yaw", "pitch", "roll")]
    rom += [f"head_range_of_movement_directions_{direction}_{measure}_session_mean"
            for measure in ("angle_mean_deg", "offset_from_rest_deg")
            for direction in ("left", "right", "up", "down")]
    return {
        label: [{"participant": row["participant"], **{key: row.get(key) for key in columns}} for row in rows]
        for label, columns in (
            ("Session summary", session_columns), ("Initial head positions", initial),
            ("Range of movement stats", rom), ("Head movement stats", movement_columns),
            ("Fitts targets and hits", target_columns), ("Fitts stats", fitts_columns),
        )
    }


def field_description(key: str) -> str:
    """Definitions match the shared statistics helpers and their aggregation."""
    definitions = {
        "participant": "Participant label assigned at import.",
        "sessions": "Number of imported sessions for this participant, including head-only or Fitts-only sessions.",
        "head_files": "Number of sessions containing a head recording.",
        "fitts_files": "Number of sessions containing a Fitts recording.",
        "head_samples": "Total number of head samples across all recordings.",
        "head_duration_s": "Sum of head recording durations (last minus first timestamp), in seconds.",
        "exclude_first_targets_per_file": "First N chronological completed targets excluded from each Fitts file; head data is unaffected.",
        "targets_recorded": "Total completed targets before exclusions, including misses.",
        "targets_excluded": "Total targets removed by the first-N-per-file setting.",
        "targets_analysed": "Retained targets after exclusions, including misses; pooled across Fitts files.",
        "hits": "Number of retained targets with a successful hit.",
        "error_rate": "Retained misses divided by retained targets; a fraction from 0 to 1, not a percentage.",
        "mean_movement_time_s": "Mean recorded movement time (MT) across all retained trials, in seconds.",
        "effective_amplitude_px": "Effective amplitude (Ae): mean actual start-to-click Euclidean distance, in pixels.",
        "effective_width_px": "Effective width (We): 4.133 times the sample SD of click error projected onto each start-to-target axis, pooled across retained trials, in pixels. Zero with fewer than two trials.",
        "index_of_difficulty_bits": "Effective index of difficulty (IDe): log2(Ae / We + 1), in bits; the shared helper returns zero when We is zero.",
        "throughput_bits_per_s": "Throughput (TP): pooled effective index of difficulty divided by mean movement time, in bits/s; zero for non-positive MT.",
        "path_trial_count": "Retained trials with at least two path points and a nonzero start-to-target axis; these contribute to the path-measure means.",
        "path_tre": "Target re-entry (TRE): mean number of entries after the first entry into the circular target, per usable trial.",
        "path_tac": "Task-axis crossing (TAC): mean number of sign changes in perpendicular path displacement, per usable trial; zeros are ignored.",
        "path_mdc": "Movement-direction change (MDC): mean number of direction reversals perpendicular to the task axis, per usable trial; zero steps are ignored.",
        "path_odc": "Orthogonal-direction change (ODC): mean number of direction reversals along the task axis, per usable trial; zero steps are ignored.",
        "path_mv_px": "Movement variability (MV): sample SD of perpendicular path displacement about its own mean, in pixels, averaged over usable trials.",
        "path_me_px": "Movement error (ME): mean absolute perpendicular distance from the task axis, in pixels, averaged over usable trials.",
        "path_mo_px": "Movement offset (MO): mean signed perpendicular distance from the task axis, in pixels, averaged over usable trials. Positive is the left-hand normal (-uy, ux) in stored screen coordinates, not necessarily screen-up.",
        "session": "Imported session label.",
        "head_file": "Original head recording filename within the import.",
        "samples": "Number of head samples in this recording.",
        "fitts_samples": "Head samples explicitly labelled task_phase == fitts_test.",
        "max_abs_relative_pitch_deg": "Maximum absolute neutral-relative Euler pitch in the recording, in degrees.",
        "near_gimbal_samples": "Samples with absolute neutral-relative Euler pitch at least 80 deg; a warning of proximity to the +/-90 deg singularity.",
    }
    if key in definitions:
        return definitions[key]
    if key.startswith("head_initial_rest_"):
        measure = key.removeprefix("head_initial_rest_").removesuffix("_session_mean")
        initial = {
            "yaw_absolute_mean_deg": "Circular mean yaw during initial-rest hold in the provider frame; not necessarily a compass heading.",
            "yaw_relative_mean_deg": "Circular mean initial-rest yaw after removing only mean calibration heading, not full neutral tilt. Unavailable without calibration.",
            "yaw_calibration_mean_deg": "Yaw of the mean saved calibration quaternion (or saved neutral when calibration samples are absent), in provider coordinates.",
            "sample_count": "Number of head samples during initial-rest hold.",
            "duration_s": "Last minus first host timestamp during initial-rest hold, in seconds; unavailable without enough host timestamps.",
        }
        if measure in initial:
            description = initial[measure]
        elif measure in ("pitch_mean_deg", "roll_mean_deg"):
            axis = measure.split("_")[0]
            description = f"Mean initial-rest {axis}, in degrees: gravity-referenced for xIMU3/AirPods with correct mounting/fit; camera-referenced for webcam systems."
        elif measure in ("yaw_std_deg", "pitch_std_deg", "roll_std_deg"):
            axis = measure.split("_")[0]
            description = f"Population SD of initial-rest {axis}, in degrees." + (
                " Uses unwrapped calibration-heading-relative yaw; unavailable without calibration." if axis == "yaw" else ""
            )
        else:
            raise ValueError(f"Missing initial-position field definition: {key}")
        return description + " Participant value is the mean of available session values (circular for mean yaw)."
    if key.startswith("head_range_of_movement_task_"):
        axis = key.removeprefix("head_range_of_movement_task_").split("_")[0]
        return (
            f"Unwrapped provider-frame {axis} max-minus-min across labelled ROM move and hold samples only, "
            "in degrees; mean of available session spans. Not the full-neutral-relative range in Head movement stats."
        )
    if key.startswith("head_range_of_movement_directions_"):
        direction = key.removeprefix("head_range_of_movement_directions_").split("_")[0]
        axis = "yaw" if direction in ("left", "right") else "pitch"
        measure = (
            f"Signed difference between mean {axis} in the {direction} hold and initial-rest mean {axis}"
            if "_offset_from_rest_" in key else f"Mean {axis} during the {direction} ROM hold"
        )
        reference = (
            " Yaw is calibration-heading-relative when calibration is available, otherwise provider-frame."
            if axis == "yaw" else " Pitch retains provider-frame tilt (gravity-referenced for IMUs, camera-referenced for webcam)."
        )
        return measure + ", in degrees; mean of available sessions. Only labelled hold samples contribute." + reference
    if key.startswith(("head_speed_", "head_acceleration_")):
        measure = (
            "Angular speed: shortest quaternion rotation between successive samples divided by elapsed time, in deg/s"
            if key.startswith("head_speed_") else
            "Angular acceleration magnitude: absolute change in angular speed divided by elapsed time, in deg/s2"
        )
        aggregate = "Mean" if key.endswith("_mean") else "Maximum"
        return (
            f"{aggregate} across all usable within-recording intervals. {measure}. "
            "Includes preparation and ROM; no across-file derivatives. Non-positive intervals and intervals "
            "shorter than half the positive median sample interval are excluded before speed calculation."
        )
    if key.endswith("_range_deg"):
        axis = key.removeprefix("head_").removeprefix("fitts_").split("_")[0]
        scope = "Fitts-labelled samples only" if key.startswith("fitts_") else "all recording samples"
        aggregate = " Participant value is the largest within-recording span, not pooled extrema." if key.startswith("head_") else ""
        return (
            f"Unwrapped {axis} Euler max-minus-min from inverse(neutral) * sample, in degrees, using {scope}. "
            "Neutral-relative components can couple when neutral is tilted; not gravity-referenced tilt ranges." + aggregate
        )
    raise ValueError(f"Missing field definition: {key}")


def format_number(value) -> str:
    if value is None:
        return ""
    return f"{value:.1f}" if isinstance(value, (float, int)) and not isinstance(value, bool) else str(value)


def initial_pose_reference_diagram(mesh_path: Path | None = None) -> str:
    """Illustrative zero poses, not participant measurements."""
    vertices, _, edges = load_obj_wireframe(mesh_path or OBJ_PATH)
    vertices = centre_and_scale(vertices)
    cards = []
    definitions = (
        ("Pitch = 0.0 deg", "Side view: forward axis is horizontal.",
         "IMU: gravity-level. Camera: level in the camera frame.", (2, 1), 0),
        ("Roll = 0.0 deg", "Front view: left/right axis is horizontal.",
         "IMU: no lateral tilt to gravity. Camera: level in the image.", (0, 1), 0),
        ("Absolute / calibration yaw = 0.0 deg", "Top view: facing provider heading zero.",
         "Calibration mean yaw uses this same provider reference.", (0, 2), 0),
        ("Relative yaw = 0.0 deg", "Top view: facing the mean calibration heading.",
         "Illustrative heading only; not a participant measurement.", (0, 2), 35),
    )
    for title, description, note, axes, yaw in definitions:
        rotation = head_rotation_to_mesh_frame(rotation_from_quaternion(
            *qm.from_euler_zyx(np.radians(yaw), 0, 0)
        ))
        rotated = vertices @ rotation.T
        projected = np.column_stack((110 + 65 * rotated[:, axes[0]], 100 - 65 * rotated[:, axes[1]]))
        lines = "".join(
            f'<line x1="{projected[a, 0]:.1f}" y1="{projected[a, 1]:.1f}" '
            f'x2="{projected[b, 0]:.1f}" y2="{projected[b, 1]:.1f}"/>'
            for a, b in edges
        )
        if axes == (0, 2):
            end_x, end_y = 110 + 80 * np.sin(np.radians(yaw)), 100 - 80 * np.cos(np.radians(yaw))
            guides = (
                '<path d="M110 185 V15" stroke="#777" stroke-dasharray="4 3"/>'
                f'<path d="M110 100 L{end_x:.1f} {end_y:.1f}" stroke="#006b8f" stroke-width="3"/>'
                '<text x="5" y="205" font-size="11">Dashed: provider heading zero</text>'
                '<text x="5" y="220" font-size="11">Blue: this yaw measure is zero</text>'
            )
        else:
            guides = (
                '<path d="M15 100 H205" stroke="#006b8f" stroke-width="2" stroke-dasharray="4 3"/>'
                '<path d="M190 25 V75 l-4 -8 M190 75 l4 -8" stroke="#555"/>'
                '<text x="140" y="20" font-size="11">IMU gravity</text>'
                '<text x="5" y="205" font-size="11">Dashed: zero-tilt horizontal</text>'
                '<text x="5" y="220" font-size="11">Camera: image/frame horizontal</text>'
            )
        guides = guides.replace("<path ", '<path fill="none" ')
        cards.append(
            '<figure style="margin:8px;flex:0 1 240px;max-width:100%">'
            f'<figcaption><b>{title}</b><br>{description}</figcaption>'
            f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{title} reference diagram" '
            'viewBox="0 0 220 230" style="width:100%;max-width:220px">'
            f'<g fill="none" stroke="#555" stroke-width="0.6">{lines}</g>'
            f'{guides}</svg>'
            f'<p style="font-size:11px">{note}</p></figure>'
        )
    return (
        '<h4>Angle zero references (schematic head mesh)</h4>'
        '<p>These are reference poses, not participant data. IMU tilt assumes correct sensor mounting/fit. '
        'Camera axes follow the camera, so a tilted camera does not define gravity-level. '
        'xIMU3 yaw may be magnetic when enabled and reliable; AirPods yaw has an arbitrary heading reference. '
        'Positive directions: yaw left, pitch up, roll left ear down. '
        'Participant plots use a fixed oblique viewing angle to expose the signed head axes; '
        'this viewing angle is not part of the reported pose.</p>'
        '<div style="display:flex;flex-wrap:wrap">' + "".join(cards) + '</div>'
    )


def initial_pose_wireframes(
    rows: list[dict], mesh_path: Path | None = None, *, zero_axes: tuple[str, ...] = (),
) -> str:
    """Initial-rest tilt with saved calibration mean yaw, not initial-rest yaw."""
    if set(zero_axes) - {"yaw", "pitch", "roll"}:
        raise ValueError("Plot zero axes must be yaw, pitch or roll")
    cards = []
    geometry = None
    for row in rows:
        angles = [row.get("head_initial_rest_yaw_calibration_mean_deg_session_mean")]
        angles += [row.get(f"head_initial_rest_{axis}_mean_deg_session_mean") for axis in ("pitch", "roll")]
        if all(value is None for value in angles):
            participant = html.escape(row["participant"])
            cards.append(f"<figure><figcaption>{participant}</figcaption><p>No initial head pose recorded.</p></figure>")
            continue
        angles = [0.0 if axis in zero_axes else value
                  for axis, value in zip(("yaw", "pitch", "roll"), angles)]
        participant = html.escape(row["participant"])
        if any(value is None for value in angles):
            cards.append(f"<figure><figcaption>{participant}</figcaption><p>No initial head pose recorded "
                         "with a saved calibration heading.</p></figure>")
            continue
        if geometry is None:
            vertices, _, edges = load_obj_wireframe(mesh_path or OBJ_PATH)
            geometry = centre_and_scale(vertices), edges
        vertices, edges = geometry
        rotation = head_rotation_to_mesh_frame(rotation_from_quaternion(
            *qm.from_euler_zyx(*np.radians(angles))
        ))
        view = head_rotation_to_mesh_frame(rotation_from_quaternion(
            *qm.from_euler_zyx(*np.radians([20, -10, 0]))
        ))
        rotation = view @ rotation
        rotated = vertices @ rotation.T
        scale = 240 / np.maximum(0.2, 4 - rotated[:, 2])
        projected = np.column_stack((120 + rotated[:, 0] * scale, 110 - rotated[:, 1] * scale))
        lines = "".join(
            f'<line x1="{projected[a, 0]:.1f}" y1="{projected[a, 1]:.1f}" '
            f'x2="{projected[b, 0]:.1f}" y2="{projected[b, 1]:.1f}"/>'
            for a, b in edges
        )
        axes_markup = []
        for axis, direction, color in (
            ("X", [0, 0, 1], "#a62920"), ("Y", [1, 0, 0], "#146b36"), ("Z", [0, 1, 0], "#175fa8"),
        ):
            endpoints = np.array([direction, -np.array(direction)]) * 1.3 @ rotation.T
            axis_scale = 240 / np.maximum(0.2, 4 - endpoints[:, 2])
            points = np.column_stack((120 + endpoints[:, 0] * axis_scale, 110 - endpoints[:, 1] * axis_scale))
            for sign, (x, y) in zip(("+", "-"), points):
                dash = ' stroke-dasharray="3 2"' if sign == "-" else ""
                axes_markup.append(
                    f'<path d="M120 110 L{x:.1f} {y:.1f}" fill="none" stroke="{color}" '
                    f'stroke-width="1.8"{dash}/>'
                    f'<text x="{x:.1f}" y="{y - 4:.1f}" fill="{color}" font-size="11" '
                    f'font-weight="bold" text-anchor="middle">{sign}{axis}</text>'
                )
        caption = " / ".join(format_number(angle) for angle in angles)
        cards.append(
            f'<figure style="margin:8px;max-width:100%"><figcaption><b>{participant}</b><br>'
            f'Calibration yaw / initial pitch / roll: {caption} deg'
            + ("<br>Display-only zero: " + ", ".join(zero_axes) if zero_axes else "") + '</figcaption>'
            f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{participant} initial head pose" '
            f'viewBox="0 0 240 285" style="width:240px;max-width:100%">'
            f'<g fill="none" stroke="#333" stroke-width="0.7">{lines}</g>{"".join(axes_markup)}'
            '<text x="5" y="220" font-size="10">Head axes: X forward, Y left, Z up</text>'
            '<text x="5" y="237" font-size="10">Yaw: + left / - right (about Z)</text>'
            '<text x="5" y="254" font-size="10">Pitch: + up / - down</text>'
            '<text x="5" y="271" font-size="10">Roll: + left ear down / - right ear down</text>'
            '</svg></figure>'
        )
    return '<div style="display:flex;flex-wrap:wrap">' + "".join(cards) + "</div>"


def summary_csv(rows: list[dict]) -> str:
    output = io.StringIO(newline="")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    # Treat user labels as text when opened in spreadsheet software.
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else format_number(v)
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
    pretest = head_pretest_summary(session)
    pretest["cursor_initial_rest"] = pretest["cursor_range_of_movement"] = None
    pretest["available"] = bool(pretest["head_initial_rest"] or pretest["head_range_of_movement"])
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
        "pretest_html": (
            "Initial yaw is calibration-heading-relative (unavailable without saved calibration); "
            "Pitch/roll are gravity-referenced tilt estimates for xIMU3/AirPods (subject to mounting/fit "
            "and sensor fusion), but camera-referenced for webcam systems, not gravity-referenced.<br>" +
            format_pretest_summary_html(pretest, decimal_places=1)
        ),
        "session_summary": participant_rows([session])[0],
    }


def payload_json(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
