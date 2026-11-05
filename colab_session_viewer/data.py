"""Load recordings and prepare Qt-free playback and participant statistics."""

from __future__ import annotations

import csv
import io
import json
import html
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np

from core import quaternion_math as qm
from core.recorder import RecordedSample, Recorder
from core.session_stats import SessionStats
from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult
from colab_session_viewer.dwell import (
    DWELL_BEHAVIOUR_FIELDS, DWELL_FIELDS, DWELL_LABELS, NODWELL_EQUIVALENTS,
    selection_settings, selection_summary,
)
from session_viewer.mesh_visualizer import (
    ANGLE_COLORS, OBJ_PATH, calculate_neutral_quaternion, centre_and_scale, reference_pose_source,
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
    selection_method: str = "unknown"
    dwell_time_s: float | None = None

    @classmethod
    def load(cls, participant: str, name: str, *, head_path=None, fitts_path=None,
             selection_method="unknown", dwell_time_s=None):
        participant, name = participant.strip(), name.strip()
        if not participant or not name:
            raise ValueError("Participant and session names are required")
        if head_path is None and fitts_path is None:
            raise ValueError("Choose a head recording, a Fitts recording, or both")
        session = cls(participant, name)
        session.selection_method, session.dwell_time_s = selection_settings(selection_method, dwell_time_s)
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
        """Return selected sample clock and optional host-aligned seconds arrays.

        SessionStats chooses the consistent clock. When all host times exist,
        aligned = clock + median(host_time-clock); otherwise aligned is None.
        No resampling or scaling of time occurs.
        """
        samples = self.samples
        times = (SessionStats(samples).times() if len(samples) > 1
                 else np.array([s.timestamp for s in samples]))
        hosts = [s.host_time for s in samples]
        aligned = (times + float(np.median(np.asarray(hosts) - times))
                   if samples and all(h is not None for h in hosts) else None)
        return times, aligned

    def fitts_times(self):
        """Return start/end seconds, host-clock flag and full playable time span.

        Use host endpoints only when every trial has both, else saved trial
        times. On the host clock include timed pre-test phases in the span.
        Reject unordered starts, non-finite endpoints and negative durations.
        """
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
        """Select head samples inside the inclusive Fitts/pre-test host-clock span.

        Require both streams' shared host timestamps, overlapping extents and
        at least two selected samples. Never align unrelated recordings by
        subtracting their independent first timestamps.
        """
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
    """Return all but the first N time-ordered targets of this file, including misses.

    N is a non-negative integer list offset, not a repetition/condition index.
    No mutation, geometry matching or outcome-based filtering occurs.
    """
    if isinstance(exclude, bool) or not isinstance(exclude, int) or exclude < 0:
        raise ValueError("Excluded targets must be a non-negative integer")
    # List position, not repetition_index: remove only the first N targets per file.
    return session.trials[exclude:]


def _target_sample_mask(session: Session, exclude: int) -> np.ndarray:
    """Keep head samples outside each excluded target's inclusive start/end interval.

    Compare shared host timestamps; missing clocks raise ValueError rather
    than silently shifting time bases. No targets excluded means keep all.
    """
    retained_trials(session, exclude)
    keep = np.ones(len(session.samples), dtype=bool)
    if not session.samples or not session.trials[:exclude]:
        return keep
    starts, ends, host, _ = session.fitts_times()
    if host:
        if any(s.host_time is None for s in session.samples):
            raise ValueError("Cannot exclude target intervals from head statistics without shared host timestamps")
        times = np.array([s.host_time for s in session.samples])
    else:
        raise ValueError("Cannot exclude target intervals from head statistics without shared host timestamps")
    for start, end in zip(starts[:exclude], ends[:exclude]):
        keep &= ~((times >= start) & (times <= end))
    return keep


def _movement_series(session: Session, keep: np.ndarray):
    """Return speed/acceleration series over contiguous retained sample-index runs.

    Each run delegates geodesic speed and scalar speed-change acceleration to
    SessionStats on the recording's consistent seconds clock. Concatenate
    usable values without averaging or forward-fill. Runs shorter than two
    samples contribute nothing; no derivative crosses an exclusion gap.
    """
    if len(session.samples) < 2:
        return (np.array([]), np.array([])), (np.array([]), np.array([]))
    times = SessionStats(session.samples).times()
    indices = np.flatnonzero(keep)
    runs = np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    speeds, accelerations = [], []
    for run in runs:
        if len(run) < 2:
            continue
        stats = SessionStats([replace(session.samples[i], timestamp=float(times[i])) for i in run])
        speeds.append(stats.angular_speed_series())
        accelerations.append(stats.angular_acceleration_series())

    def combine(series):
        """Concatenate each run's times and values, or return two empty arrays."""
        return tuple(np.concatenate([s[i] for s in series]) if series else np.array([]) for i in (0, 1))

    return combine(speeds), combine(accelerations)


def _numeric_leaves(values: dict, prefix: str):
    """Flatten finite numeric summary leaves, excluding booleans and unavailable values.

    Prefix nested keys for participant aggregation; no averaging happens here.
    """
    for key, value in values.items():
        label = f"{prefix}_{key}"
        if isinstance(value, dict):
            yield from _numeric_leaves(value, label)
        elif isinstance(value, (float, int)) and not isinstance(value, bool):
            _finite([value], label)
            yield label, value


def planned_id(trial: TrialResult) -> float:
    """Return planned Shannon ID = log2(D/W + 1), in bits.

    D is Euclidean distance between saved start and target centres; W is saved
    target width in pixels, not effective width or configured circle radius.
    Click accuracy is irrelevant. Reject non-finite geometry/non-positive W.
    """
    distance = math.dist(trial.start_pos, trial.target_pos)
    if not np.isfinite(distance) or not np.isfinite(trial.width_px) or trial.width_px <= 0:
        raise ValueError("Planned ID requires finite target coordinates and a positive target width")
    return math.log2(distance / trial.width_px + 1)


def fitts_condition_rows(sessions: list[Session], exclude_first_targets: int = 0) -> list[dict]:
    """Inventory saved conditions, one row per Fitts file, independent of head data.

    List distinct block/amplitude/width settings. Within each saved
    (block,label,amplitude,width), count distinct target positions per repetition
    and observed repetition indices. These are observed counts, not proof of
    intended run settings. Recorded count is before exclusions; analysed count
    uses first-N removal only, never optional matching.
    """
    rows = []
    for session in sorted(sessions, key=lambda s: (s.participant, s.name)):
        if not session.fitts_filename:
            continue
        analysed = {id(t) for t in retained_trials(session, exclude_first_targets)}
        groups = {}
        for trial in session.trials:
            groups.setdefault(
                (trial.block_type, trial.condition_label, trial.amplitude_px, trial.width_px), [],
            ).append(trial)
        target_counts, repetition_counts = set(), set()
        for trials in groups.values():
            repetitions = {}
            for trial in trials:
                repetitions.setdefault(trial.repetition_index, set()).add(tuple(trial.target_pos))
            target_counts.update(len(positions) for positions in repetitions.values())
            repetition_counts.add(len(repetitions))

        def values(items):
            return ", ".join(f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in sorted(items))

        rows.append({
            "participant": session.participant, "session": session.name,
            "fitts_file": session.fitts_filename,
            "selection_method": session.selection_method,
            "dwell_time_s": session.dwell_time_s,
            "test_block_types": values({t.block_type for t in session.trials}),
            "test_amplitudes_px": values({t.amplitude_px for t in session.trials}),
            "test_widths_px": values({t.width_px for t in session.trials}),
            "condition_targets_per_repetition_observed": values(target_counts),
            "test_repetitions_observed": values(repetition_counts),
            "condition_trials_recorded": len(session.trials),
            "condition_trials_analysed": len(analysed),
        })
    return rows


def _fitts_comparison_row(participant: str, trials: list[TrialResult], available: int) -> dict:
    """Recalculate all Fitts/path measures on this participant's selected targets.

    Report selected/omitted counts and mean/min/max planned ID. Reuse pooled
    FittsStats formulas rather than averaging original participant summaries.
    Target selection itself is done by the matching functions, not here.
    """
    summary = FittsStats(trials).summary()
    difficulty = [planned_id(t) for t in trials]
    row = {
        "participant": participant,
        "matching_targets_available": available,
        "matching_targets_selected": len(trials),
        "matching_targets_omitted": available - len(trials),
        "planned_id_mean_bits": float(np.mean(difficulty)),
        "planned_id_min_bits": min(difficulty), "planned_id_max_bits": max(difficulty),
    }
    row.update(participant_tables([{
        "participant": participant,
        "planned_id_mean_bits": row["planned_id_mean_bits"],
        **{k.removeprefix("overall_"): v for k, v in summary.items() if k.startswith("overall_")},
        **{f"path_{k}": v for k, v in FittsStats.accuracy_summary(trials).items()},
    }])["Fitts stats"][0])
    return row


def _condition_test_quotas(pools: dict, counts: dict, tests: dict) -> dict:
    """Allocate each shared condition's fixed count across this participant's tests.

    Reserve one slot per test using augmenting-path assignment, prioritising
    tests with fewer eligible conditions. Then fill each condition's remaining
    slots by the smallest current test quota (name breaks ties), bounded by
    available targets. Raise if all tests cannot contribute; never silently
    drop one. This count allocation does not inspect ID, clicks or timing.
    """
    capacities = {}
    for key, trials in pools.items():
        capacities[key] = {}
        for name, _, _ in trials:
            capacities[key][name] = capacities[key].get(name, 0) + 1
    slots = [(key, index) for key, count in counts.items() for index in range(count)]
    owners = {}

    def cover(test, visited):
        """Reserve a condition slot, reassigning its owner along an augmenting path."""
        for slot in slots:
            if slot in visited or test not in capacities[slot[0]]:
                continue
            visited.add(slot)
            if slot not in owners or cover(owners[slot], visited):
                owners[slot] = test
                return True
        return False

    # Reserve one shared-condition slot per test, reassigning earlier reservations
    # if necessary; filling quotas first could needlessly exclude a rarer test.
    for test in sorted(tests, key=lambda name: (sum(name in c for c in capacities.values()), name)):
        if not cover(test, set()):
            raise ValueError(f"Cannot retain every test with shared conditions: {test}")
    quotas = {key: {name: 0 for name in capacity} for key, capacity in capacities.items()}
    for (key, _), name in owners.items():
        quotas[key][name] += 1
    for key, count in counts.items():
        for _ in range(count - sum(quotas[key].values())):
            name = min(
                (name for name in capacities[key] if quotas[key][name] < capacities[key][name]),
                key=lambda name: (quotas[key][name], name),
            )
            quotas[key][name] += 1
    return quotas


def _condition_matched_fitts_rows(sessions: list[Session], exclude: int, include_dwell: bool = False) -> list[dict]:
    """Balance counts within exact shared (block, saved amplitude, width) settings.

    After first-N removal per file, each shared condition contributes the
    minimum available participant count, maximising equal counts. Reserve
    every test through _condition_test_quotas. The smallest pool per condition
    supplies sorted reference IDs (participant label breaks ties); greedily
    choose nearest unused IDs within each test's remaining quota, without an
    ID cutoff. This is not global ID-error minimisation or changed geometry.
    Recalculate measures on selected targets; report maximum absolute ID gap
    from reference targets, not maximum pairwise participant gap.
    """
    participants = sorted({s.participant for s in sessions})
    tests = {p: {} for p in participants}
    conditions = {}
    for session in sorted(sessions, key=lambda s: (s.participant, s.name)):
        if not session.fitts_filename and not session.trials:
            continue
        trials = retained_trials(session, exclude)
        if not trials:
            raise ValueError(f"No targets remain in {session.participant} / {session.name}; cannot retain every test")
        tests[session.participant][session.name] = trials
        for index, trial in enumerate(trials):
            _finite([trial.amplitude_px, trial.width_px], "Matching condition")
            planned_id(trial)
            key = trial.block_type, trial.amplitude_px, trial.width_px
            conditions.setdefault(key, {}).setdefault(session.participant, []).append((session.name, index, trial))
    shared = {key: values for key, values in conditions.items() if set(values) == set(participants)}
    if not shared:
        raise ValueError("No shared block type/amplitude/width conditions across all participants")
    shared = dict(sorted(shared.items()))
    counts = {key: min(len(pool) for pool in values.values()) for key, values in shared.items()}
    quotas = {
        p: _condition_test_quotas({key: values[p] for key, values in shared.items()}, counts, tests[p])
        for p in participants
    }
    selected = {p: [] for p in participants}
    contributions = {p: {name: 0 for name in tests[p]} for p in participants}
    gaps = {p: [] for p in participants}
    for key, pools in shared.items():
        anchor = min(participants, key=lambda p: (len(pools[p]), p))
        available = {p: list(pools[p]) for p in participants}
        ordered = sorted(pools[anchor], key=lambda item: (planned_id(item[2]), item[0], item[1]))
        for name, index, trial in ordered:
            difficulty = planned_id(trial)
            matches = {anchor: (name, index, trial)}
            for participant in participants:
                if participant != anchor:
                    matches[participant] = min(
                        (item for item in available[participant] if quotas[participant][key][item[0]] > 0),
                        key=lambda item: (abs(planned_id(item[2]) - difficulty), item[0], item[1]),
                    )
            for participant, match in matches.items():
                test, _, chosen = match
                available[participant].remove(match)
                quotas[participant][key][test] -= 1
                contributions[participant][test] += 1
                selected[participant].append(chosen)
                gaps[participant].append(abs(planned_id(chosen) - difficulty))
    rows = []
    for participant in participants:
        missing = [name for name, count in contributions[participant].items() if not count]
        if missing:
            raise ValueError(
                f"Cannot retain every test with shared conditions: {participant} / " + ", ".join(missing)
            )
        row = _fitts_comparison_row(
            participant, selected[participant], sum(len(trials) for trials in tests[participant].values()),
        )
        row.update(
            matching_method="target_conditions",
            matching_conditions=len(shared),
            matching_tests_retained=len(contributions[participant]),
            matching_targets_by_test="; ".join(f"{name}: {count}" for name, count in contributions[participant].items()),
            matching_max_id_gap_bits=max(gaps[participant]),
        )
        if include_dwell:
            row.update(selection_summary([s for s in sessions if s.participant == participant], selected[participant]))
        rows.append(row)
    return rows


def matched_fitts_rows(
    sessions: list[Session], exclude_first_targets: int = 0, *, method: str = "strict_id",
    include_dwell: bool = False,
) -> list[dict]:
    """Build the optional comparison only; never subset original session data.

    Apply first-N exclusions before selection. strict_id uses the smallest
    participant pool as anchor and greedily selects nearest unused targets of
    the same block/width; require each complete set's max ID-min ID <= 0.1 bits.
    Saved amplitude is not a strict-mode stratum. target_conditions instead
    delegates count balancing/all-test retention without an ID cutoff.
    Both return equal selected counts and recomputed pooled measures. Optional
    dwell measures use these same selected targets. No selection uses clicks,
    hits or MT; impossible comparisons raise ValueError, not a fallback pool.
    """
    participants = sorted({s.participant for s in sessions})
    if len(participants) < 2:
        raise ValueError("Matched-ID comparison needs at least two participants")
    if method == "target_conditions":
        return _condition_matched_fitts_rows(sessions, exclude_first_targets, include_dwell)
    if method != "strict_id":
        raise ValueError(f"Unknown Fitts matching method: {method}")
    pools = {p: [] for p in participants}
    for session in sorted(sessions, key=lambda s: (s.participant, s.name)):
        pools[session.participant].extend(retained_trials(session, exclude_first_targets))
    anchor = min(participants, key=lambda p: (len(pools[p]), p))
    available = {p: list(enumerate(pools[p])) for p in participants}
    selected = {p: [] for p in participants}
    # Stable geometry-only ordering prevents outcomes or import order choosing matches.
    ordered = sorted(
        available[anchor],
        key=lambda item: (item[1].block_type, item[1].width_px, planned_id(item[1]), item[0]),
    )
    for index, trial in ordered:
        difficulty = planned_id(trial)
        candidates = {anchor: (index, trial)}
        for participant in participants:
            if participant == anchor:
                continue
            matches = [
                (abs(planned_id(t) - difficulty), i, t)
                for i, t in available[participant]
                if t.block_type == trial.block_type and t.width_px == trial.width_px
                and abs(planned_id(t) - difficulty) <= .1
            ]
            if not matches:
                break
            _, i, match = min(matches, key=lambda m: (m[0], m[1]))
            candidates[participant] = (i, match)
        if len(candidates) != len(participants):
            continue
        ids = [planned_id(t) for _, t in candidates.values()]
        if max(ids) - min(ids) > .1:
            continue
        for participant, (i, match) in candidates.items():
            selected[participant].append(match)
            available[participant] = [(j, t) for j, t in available[participant] if j != i]
    if not selected[anchor]:
        raise ValueError("No shared planned-ID matches within 0.1 bits with the same task type and target width")
    rows = [_fitts_comparison_row(p, selected[p], len(pools[p])) for p in participants]
    if include_dwell:
        for row in rows:
            participant = row["participant"]
            row.update(selection_summary([s for s in sessions if s.participant == participant], selected[participant]))
    return rows


def calibration_mean_yaw(session: Session) -> float | None:
    """Return saved calibration yaw in degrees, or None without calibration.

    Prefer the hemisphere-aligned normalized quaternion mean of saved
    calibration samples, otherwise use the saved neutral quaternion. Extract
    yaw with to_euler_zyx; never substitute initial rest or playback recenter.
    Invalid calibration quaternions raise ValueError.
    """
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
    """Return degrees(atan2(mean(sin(angle)), mean(cos(angle)))) for degree inputs.

    Each angle has equal weight; this avoids ordinary mean errors at +/-180.
    No concentration/undefined-direction threshold is imposed.
    """
    radians = np.radians(values)
    return float(np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())))


def _validate_opening_exclusion(exclude_s: float, label: str) -> None:
    if (isinstance(exclude_s, bool) or not isinstance(exclude_s, (int, float))
            or not np.isfinite(exclude_s) or exclude_s < 0):
        raise ValueError(f"{label} opening exclusion must be a finite, non-negative number of seconds")


def _initial_rest_after_opening(session: Session, exclude_s: float) -> list:
    """Remove only samples before hold_start + exclude_s in each initial-rest hold.

    Use the latest recorded hold-start event/phase start preceding the first
    hold sample, falling back to that sample's time. Prefer host time when
    complete, otherwise provider time. Keep cutoff endpoints and every
    non-rest sample; exhausted holds contribute no pose samples. Zero skips
    no samples. Calibration/reference data are never modified.
    """
    _validate_opening_exclusion(exclude_s, "Initial-rest")
    if exclude_s == 0:
        return session.samples
    phase = "initial_rest.hold"
    events = [
        e for e in getattr(session.recorder, "_events", [])
        if e.get("event_type") == "task_phase" and e.get("data") == {"phase": phase, "action": "start"}
    ]
    records = [r for r in pretest_records(session.metadata) if r.get("phase") == phase]
    selected = []
    index = 0
    while index < len(session.samples):
        sample = session.samples[index]
        if sample.metadata.get("task_phase") != phase:
            selected.append(sample)
            index += 1
            continue
        end = index + 1
        while end < len(session.samples) and session.samples[end].metadata.get("task_phase") == phase:
            end += 1
        hold = session.samples[index:end]
        use_host = all(s.host_time is not None for s in hold)
        key = "host_time" if use_host else "timestamp"
        first = getattr(sample, key)
        starts = [e[key] for e in events if e.get(key) is not None and e[key] <= first]
        if use_host:
            starts += [
                r["start_host_time"] for r in records
                if r.get("start_host_time") is not None and r["start_host_time"] <= first
            ]
        if not starts and use_host:
            # A head-only file may retain phase events only on the provider clock.
            starts = [e["timestamp"] for e in events
                      if e.get("timestamp") is not None and e["timestamp"] <= sample.timestamp]
            if starts:
                key, first = "timestamp", sample.timestamp
        start = max(starts) if starts else first
        selected.extend(s for s in hold if getattr(s, key) >= start + exclude_s)
        index = end
    return selected


def head_pretest_summary(
    session: Session, *, initial_rest_exclude_s: float = 0.0, exclude_first_targets: int = 0,
) -> dict:
    """Summarise held poses, optionally skipping the opening of initial rest only.

    Saved calibration and directional holds are unchanged; offsets from rest use
    the retained rest samples. The default keeps playback summaries unmodified.
    Excluded target host intervals are also removed before summarise_pretest.
    When calibration exists, replace yaw by the yaw of inverse(calibration
    heading)*sample quaternion, leaving recorded pitch/roll fields unchanged.
    Rest recorded/relative yaw means are circular; relative yaw SD is population
    SD of unwrapped degrees. Pitch/roll use the shared arithmetic mean and
    population SD. Exhausted holds expose count 0 and unavailable pose values;
    no calibration means unavailable relative yaw, not an inferred heading.
    """
    calibration_yaw = calibration_mean_yaw(session)
    retained = _initial_rest_after_opening(session, initial_rest_exclude_s)
    target_keep = _target_sample_mask(session, exclude_first_targets)
    target_ids = {id(s) for s, keep in zip(session.samples, target_keep) if keep}
    retained = [s for s in retained if id(s) in target_ids]
    samples = retained
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
            for s in retained if s.metadata.get("task_phase") == "initial_rest.hold"
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
    elif any(s.metadata.get("task_phase") == "initial_rest.hold" for s in session.samples):
        summary["head_initial_rest"] = {
            "sample_count": 0, "duration_s": None,
            **{f"{axis}_{measure}_deg": None for axis in ("yaw", "pitch", "roll") for measure in ("mean", "std")},
            "yaw_absolute_mean_deg": None, "yaw_relative_mean_deg": None,
            "yaw_calibration_mean_deg": calibration_yaw,
        }
    return summary


def _head_movement_mask(session: Session, exclude_s: float) -> np.ndarray:
    """Keep samples at/after first consistent-clock time + opening seconds.

    This mask applies to recording movement estimates only, not rest pose,
    calibration, directional exercise or dwell windows. Zero retains all.
    """
    _validate_opening_exclusion(exclude_s, "Head-recording")
    if not session.samples:
        return np.zeros(0, dtype=bool)
    if exclude_s == 0:
        return np.ones(len(session.samples), dtype=bool)
    times = (SessionStats(session.samples).times() if len(session.samples) > 1
             else np.array([session.samples[0].timestamp]))
    return times - times[0] >= exclude_s


def head_range_diagnostics(
    session: Session, *, head_opening_exclude_s: float = 0.0, exclude_first_targets: int = 0,
) -> dict | None:
    """Return relative Euler max-min spans for retained recording/task samples.

    Compute inverse(original reference quaternion)*each recorded quaternion,
    convert to degree yaw/pitch/roll and unwrap before applying recording-opening
    and target masks. Range = max-min; task-only ranges further require the
    fitts_test label. Flag abs(relative pitch)>=80 degrees as near gimbal lock.
    Do not infer a new reference after exclusions or automatically trim drift.
    No retained samples gives None; no task samples gives unavailable task spans.
    """
    retained = _head_movement_mask(session, head_opening_exclude_s) & _target_sample_mask(session, exclude_first_targets)
    if not retained.any():
        return None
    neutral = calculate_neutral_quaternion(session.samples, session.recorder)
    quaternions = np.array([s.quaternion for s in session.samples])
    angles = np.degrees(qm.to_euler_zyx(qm.relative_rotation(neutral, quaternions)))
    unwrapped = np.degrees(np.unwrap(np.radians(angles), axis=0))
    angles = angles[retained]
    unwrapped = unwrapped[retained]
    fitts = np.array([s.metadata.get("task_phase") == "fitts_test" for s in session.samples])[retained]
    row = {"participant": session.participant, "session": session.name,
           "head_file": session.head_filename, "samples": len(angles),
           "fitts_samples": int(fitts.sum()),
           "max_abs_relative_pitch_deg": float(np.abs(angles[:, 1]).max()),
           "near_gimbal_samples": int(np.count_nonzero(np.abs(angles[:, 1]) >= 80))}
    for i, axis in enumerate(("yaw", "pitch", "roll")):
        row[f"{axis}_range_deg"] = float(np.ptp(unwrapped[:, i]))
        row[f"fitts_{axis}_range_deg"] = float(np.ptp(unwrapped[fitts, i])) if fitts.any() else None
    return row


def participant_rows(
    sessions: list[Session], exclusions: dict[str, int] | None = None, *, initial_rest_exclude_s: float = 0.0,
    head_opening_exclude_s: float = 0.0,
    include_dwell: bool = False,
) -> list[dict]:
    """Calculate all participant measures, pooling or averaging at the stated level.

    Fitts: concatenate all files' first-N-retained targets, including misses;
    recompute pooled FittsStats and usable-path means. Planned ID is the
    arithmetic mean of per-target geometry IDs. Optional selection_summary
    applies per-target dwell timing and spatial estimates to that same pool.
    No optional matching affects these main-table measures.

    Head movement: pool usable speed/acceleration values from contiguous
    retained runs for arithmetic means/maxima (not session means or time
    weights). Each axis range is the largest within-recording relative span,
    not a span across files. Counts/durations describe raw recording totals
    separately from retained movement counts.

    Pre-test: head_pretest_summary filters hold-opening/target exclusions;
    numeric leaves are averaged equally over available session summaries.
    Yaw means use circular session averaging. Exercise max-min spans include
    both move and hold samples and are unwrapped, then averaged across sessions.
    Missing measures stay unavailable; existing legacy SD/degenerate conventions
    remain in the shared helpers.
    """
    _validate_opening_exclusion(head_opening_exclude_s, "Head-recording")
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
            "initial_rest_exclude_s": initial_rest_exclude_s,
            "head_opening_exclude_s": head_opening_exclude_s,
            "head_files": sum(bool(s.head_filename) for s in recordings),
            "fitts_files": sum(bool(s.fitts_filename) for s in recordings),
            "exclude_first_targets_per_file": exclude,
            "targets_recorded": total, "targets_excluded": total - len(trials),
            "targets_analysed": len(trials), "hits": sum(t.hit for t in trials),
        }
        iso = FittsStats(trials).summary()
        row["planned_id_mean_bits"] = float(np.mean([planned_id(t) for t in trials])) if trials else None
        for key, value in iso.items():
            if key.startswith("overall_"):
                row[key.removeprefix("overall_")] = value if trials else None
        row.update({f"path_{k}": v for k, v in FittsStats.accuracy_summary(trials).items()})
        if include_dwell:
            row.update(selection_summary(recordings, trials))
        speeds, accelerations, ranges = [], [], []
        duration = 0.0
        movement_samples = 0
        pretests: dict[str, list[float]] = {}
        for session in recordings:
            if session.samples:
                diagnostics = head_range_diagnostics(
                    session, head_opening_exclude_s=head_opening_exclude_s, exclude_first_targets=exclude,
                )
                if diagnostics is not None:
                    ranges.append([diagnostics[f"{axis}_range_deg"] for axis in ("yaw", "pitch", "roll")])
                if len(session.samples) > 1:
                    duration += SessionStats(session.samples).session_duration()
                keep = _head_movement_mask(session, head_opening_exclude_s) & _target_sample_mask(session, exclude)
                movement_samples += int(keep.sum())
                speed_series, acceleration_series = _movement_series(session, keep)
                speeds.extend(speed_series[1])
                accelerations.extend(acceleration_series[1])
            pretest = head_pretest_summary(
                session, initial_rest_exclude_s=initial_rest_exclude_s, exclude_first_targets=exclude,
            )
            for section, values in pretest.items():
                if section.startswith("head_") and isinstance(values, dict):
                    for key, value in _numeric_leaves(values, section):
                        pretests.setdefault(f"{key}_session_mean", []).append(value)
            rom_samples = [
                s for s, keep in zip(session.samples, _target_sample_mask(session, exclude))
                if keep and (s.metadata.get("task_phase") or "").startswith("range_of_movement.")
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
        row["head_movement_samples"] = movement_samples
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
        if label == "Dwell behaviour stats":
            files = [session.fitts_filename, session.head_filename]
        elif label.startswith("Fitts"):
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
    if key in DWELL_LABELS:
        return DWELL_LABELS[key]
    if key == "index_of_difficulty_bits":
        return "Effective ID (IDe, bits)"
    if key in ("test_block_types", "test_amplitudes_px", "test_widths_px",
               "condition_targets_per_repetition_observed", "test_repetitions_observed"):
        return {
            "test_block_types": "Block types",
            "test_amplitudes_px": "Amplitude settings (px)",
            "test_widths_px": "Width settings (px)",
            "condition_targets_per_repetition_observed": "Targets per condition/repetition (observed)",
            "test_repetitions_observed": "Repetitions per condition (observed)",
        }[key]
    if key in ("planned_id_mean_bits", "planned_id_min_bits", "planned_id_max_bits"):
        return {"planned_id_mean_bits": "Planned ID average (bits)",
                "planned_id_min_bits": "Planned ID minimum (bits)",
                "planned_id_max_bits": "Planned ID maximum (bits)"}[key]
    if key == "initial_rest_exclude_s":
        return "Initial-rest opening excluded (s)"
    if key == "head_opening_exclude_s":
        return "Head-recording opening excluded (s)"
    if key == "head_movement_samples":
        return "Analysed head samples"
    if key in ("head_files", "head_samples", "head_duration_s"):
        return {"head_files": "Head files", "head_samples": "Recorded head samples",
                "head_duration_s": "Recorded head duration (s)"}[key]
    if key.startswith("head_initial_rest_yaw_") and key.endswith("_mean_deg_session_mean"):
        kind = key.removeprefix("head_initial_rest_yaw_").removesuffix("_mean_deg_session_mean")
        if kind in ("absolute", "relative", "calibration"):
            return {
                "absolute": "Recorded yaw (deg)",
                "relative": "Yaw from calibration heading (deg)",
                "calibration": "Calibration heading (deg)",
            }[kind]
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
    label = label.replace("_std", "_spread (SD)").replace("_mean", "_average")
    label = label.replace("exclude_first_targets_per_file", "exclude_N_per_file")
    label = label.replace("deg_s2", "(deg/s2)").replace("deg_s", "(deg/s)")
    label = label.replace("_deg", " (deg)").replace("_px", " (px)")
    label = label.replace("_bits_per_s", " (bits/s)").replace("_s", " s") if label.endswith("_s") else label
    label = label.replace("_", " ")
    return "Yaw from calibration heading (deg)" if calibrated_yaw else label


def participant_tables(rows: list[dict]) -> dict[str, list[dict]]:
    """Project calculated rows into ordered tables without recalculating measures.

    Each table keeps one participant row. Optional MTnodwell/throughput follow
    original counterparts; dwell behaviour/counts form a separate table.
    The UI reuses this schema for single-session and matched-target rows.
    """
    session_columns = (
        "sessions", "head_files", "fitts_files", "head_duration_s", "head_samples",
    )
    movement_columns = (
        "head_opening_exclude_s", "head_movement_samples",
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
        "effective_amplitude_px", "effective_width_px", "planned_id_mean_bits", "index_of_difficulty_bits",
        "throughput_bits_per_s", "path_trial_count", "path_tre", "path_tac",
        "path_mdc", "path_odc", "path_mv_px", "path_me_px", "path_mo_px",
    )
    if any("mtnodwell_s" in row for row in rows):
        fitts_columns = tuple(
            column for key in fitts_columns
            for column in ((key, NODWELL_EQUIVALENTS[key]) if key in NODWELL_EQUIVALENTS else (key,))
        )
    initial = [f"head_initial_rest_yaw_{kind}_mean_deg_session_mean"
               for kind in ("absolute", "relative", "calibration")]
    initial += [f"head_initial_rest_{axis}_mean_deg_session_mean" for axis in ("pitch", "roll")]
    initial += [f"head_initial_rest_{axis}_std_deg_session_mean" for axis in ("yaw", "pitch", "roll")]
    initial += ["head_initial_rest_sample_count_session_mean", "head_initial_rest_duration_s_session_mean"]
    initial += ["initial_rest_exclude_s"]
    rom = [f"head_range_of_movement_task_{axis}_range_deg_session_mean"
           for axis in ("yaw", "pitch", "roll")]
    rom += [f"head_range_of_movement_directions_{direction}_{measure}_session_mean"
            for measure in ("angle_mean_deg", "offset_from_rest_deg")
            for direction in ("left", "right", "up", "down")]
    tables = {
        label: [{"participant": row["participant"], **{key: row.get(key) for key in columns}} for row in rows]
        for label, columns in (
            ("Session summary", session_columns), ("Initial head positions", initial),
            ("Range of movement stats", rom), ("Head movement stats", movement_columns),
            ("Fitts targets and hits", target_columns), ("Fitts stats", fitts_columns),
        )
    }
    if any("mtnodwell_s" in row for row in rows):
        tables["Dwell behaviour stats"] = [
            {"participant": row["participant"], **{key: row.get(key) for key in DWELL_BEHAVIOUR_FIELDS}}
            for row in rows
        ]
    return tables


def field_description(key: str) -> str:
    """Definitions match the shared statistics helpers and their aggregation."""
    if key in DWELL_FIELDS:
        return DWELL_FIELDS[key]
    definitions = {
        "participant": "The participant name or label chosen when importing the recordings.",
        "selection_method": "Selection method labelled at import for this file: external_switch, dwell, mouse, other (non-dwell), or unknown. Never inferred from participant identity or head input device.",
        "dwell_time_s": "Configured dwell duration in seconds, supplied at import. Required and positive for dwell; blank for other selection methods. Used for MTnodwell, not inferred from cursor residence.",
        "test_block_types": "Distinct block types saved in this Fitts file, independent of the head recording.",
        "test_amplitudes_px": "Distinct amplitude settings saved in this Fitts file. For circle tasks these are configured circle radii, capped to fit the screen, not actual movement distances.",
        "test_widths_px": "Distinct target widths saved in this Fitts file, in pixels.",
        "test_repetitions_observed": "Distinct observed repetition counts across this file's conditions, from repetition_index. This is separate from targets per condition/repetition. Incomplete files cannot establish the intended repetition setting.",
        "condition_block_type": "Saved task type, such as circle, edge, random_path or typing.",
        "condition_label": "Condition label saved for these trials. Width and amplitude columns use numeric trial fields, not text parsed from this label.",
        "condition_amplitude_px": "Amplitude setting saved on each trial, in pixels. For circle tasks the generator uses this as circle radius, capped to fit the screen. It is not necessarily the actual distance between successive targets.",
        "condition_width_px": "Target width saved on each trial, in pixels. Circular target hit testing uses this diameter.",
        "condition_targets_per_repetition_observed": "Distinct target-centre positions observed in each saved repetition of this condition. Lists distinct counts if repetitions differ. This is an observed count, not proof of the original circle-target setting: incomplete recordings or repeated positions can reduce it.",
        "condition_repetitions_observed": "Number of distinct repetition_index values recorded for this condition. Missing or incomplete repetitions cannot be recovered from these files.",
        "condition_trials_recorded": "Number of completed trials saved in this Fitts file, before exclusions. This is not a guaranteed planned trial count.",
        "condition_trials_analysed": "Recorded trials in this Fitts file remaining after the global first-target exclusion. Planned-ID matching does not change this inventory.",
        "condition_distance_min_px": "Smallest saved start-centre to target-centre distance for this condition, in pixels, before exclusions. This is presented movement distance, not configured circle radius.",
        "condition_distance_max_px": "Largest saved start-centre to target-centre distance for this condition, in pixels, before exclusions.",
        "matching_targets_available": "Targets available after the global first-target exclusion, before planned-ID matching.",
        "matching_targets_selected": "Targets retained for the optional balanced planned-ID comparison. Each participant contributes the same count. Matching uses saved target centres and width, never clicks or movement times.",
        "matching_targets_omitted": "Available targets not selected by the chosen matching method. These omissions apply only to this comparison table, not the original tables, playback or head statistics.",
        "matching_method": "target_conditions: balance shared block type/amplitude/width target counts, retain every test and choose nearest available planned IDs within test quotas, without a hard ID cutoff. This is condition/count matching, not exact ID equality.",
        "matching_conditions": "Number of shared block type/amplitude/width combinations. Each combination contributes the largest equal target count available across all participants, after global target exclusions.",
        "matching_tests_retained": "Number of this participant's tests contributing targets. Closest-condition matching requires every imported Fitts test to contribute; it reports an error instead of silently dropping a test.",
        "matching_targets_by_test": "Selected target counts by session/test. Within each shared condition, test quotas are balanced as evenly as availability allows before nearest-ID selection. These counts do not modify any other analysis.",
        "matching_max_id_gap_bits": "Largest absolute planned-ID difference between this participant's selected targets and reference targets. Each condition's reference is the participant with the fewest available targets (label breaks ties). There is no cutoff in closest-condition mode; a nonzero difference is retained and reported, not corrected.",
        "planned_id_mean_bits": "Average target difficulty from the recorded geometry: log2(start-to-target distance / target width + 1). In the main Fitts table, uses all targets across all files after global first-target exclusions, exactly like the other Fitts measures; matching never removes targets from that table. Only the separate matched-target table uses a matched subset. ID uses target width, whereas IDe uses effective width from click spread, so ID can be much higher than IDe. Circle radius is not necessarily the movement distance.",
        "planned_id_min_bits": "Smallest planned Shannon ID among selected trials. Strict-ID matching limits each matched set's span to 0.1 bits. Closest-condition matching balances block type/amplitude/width counts and retains all tests without an ID cutoff.",
        "planned_id_max_bits": "Largest planned Shannon ID among selected trials. Neither matching method changes target distances or guarantees identical directions or identical measured effective ID.",
        "sessions": "Number of imported sessions for this participant. Includes sessions with only a head recording or only a pointing-task recording.",
        "head_files": "Number of imported sessions that contain a head recording.",
        "fitts_files": "Number of imported sessions that contain a pointing-task recording.",
        "head_samples": "Total number of recorded head poses across this participant's recordings.",
        "head_opening_exclude_s": "Opening duration excluded from each head recording's movement statistics, in seconds from its first sample. Affects ranges, speed and acceleration, but does not change the reference pose, saved calibration, playback, rest or movement-exercise results. Zero includes the full recording.",
        "head_movement_samples": "Number of head poses retained for session movement statistics after the recording-opening and target-interval exclusions. With none remaining, movement estimates are blank; speed and acceleration require consecutive retained samples.",
        "head_duration_s": "Total time covered by the head recordings, in seconds. Each recording runs from its first sample to its last; gaps between files are not included.",
        "exclude_first_targets_per_file": "Number of completed targets to leave out at the start of every pointing-task file, in time order. The notebook uses the same setting for all participants. Their start-to-click intervals (including endpoints) are also left out of head statistics, using shared host timestamps. All source data and playback frames remain available; excluded chart periods have grey background shading while traces retain their colours.",
        "targets_recorded": "Number of completed targets before any are left out. Includes clicks that missed.",
        "targets_excluded": "Number of targets left out by the setting that skips the first targets in each file.",
        "targets_analysed": "Number of targets used in the results after exclusions. Includes clicks that missed, combined across pointing-task files.",
        "hits": "Number of analysed targets where the click landed inside the target.",
        "error_rate": "Share of analysed targets where the click missed: misses divided by analysed targets. Shown as a fraction from 0 to 1, not a percentage.",
        "mean_movement_time_s": "Average recorded time to reach and click a target (movement time, MT), in seconds, across analysed targets.",
        "effective_amplitude_px": "Effective amplitude (Ae): average straight-line distance from the cursor's starting position to its final click, in pixels, across analysed targets.",
        "effective_width_px": "Effective width (We): how widely the clicks are spread along the line from start to target, in pixels. Calculated as 4.133 times the sample standard deviation (SD) of click errors along that line, combining analysed targets. Zero if fewer than two targets are available.",
        "index_of_difficulty_bits": "Effective index of difficulty (IDe): pointing difficulty based on actual movement distance and click spread, in bits. Calculated as log2(Ae / We + 1); reported as zero when effective width (We) is zero.",
        "throughput_bits_per_s": "Throughput (TP): a combined measure of pointing speed and accuracy; higher is better. The effective index of difficulty for all analysed targets is divided by their average movement time, in bits/s. Zero if average movement time is zero or negative.",
        "path_trial_count": "Number of analysed targets with enough cursor-path data for path measures: at least two points and different start and target positions.",
        "path_tre": "Target re-entry (TRE): average number of times the cursor enters the circular target again after its first entry, per usable path. The first entry is not counted. Trials with no re-entry contribute zero, so this average count can be below 1; it is not a percentage or a total.",
        "path_tac": "Task-axis crossing (TAC): average number of times the cursor crosses the straight line from start to target, per usable path. Points exactly on the line are ignored when counting crossings.",
        "path_mdc": "Movement-direction change (MDC): average number of side-to-side reversals across the line from start to target, per usable path. Steps with no sideways movement are ignored.",
        "path_odc": "Orthogonal-direction change (ODC): average number of forward/backward reversals along the line from start to target, per usable path. Steps with no movement along the line are ignored.",
        "path_mv_px": "Movement variability (MV): how much the path wobbles sideways around its own average position, in pixels. Calculated as the sample SD of distances across the line from start to target, then averaged across usable paths.",
        "path_me_px": "Movement error (ME): average sideways distance of the cursor from the straight line from start to target, in pixels. Distance is always positive; averaged across usable paths.",
        "path_mo_px": "Movement offset (MO): average sideways position of the cursor relative to the line from start to target, in pixels. The sign tells which side of the line; averaged across usable paths. Positive follows (-uy, ux), where (ux, uy) points from start to target in screen coordinates; it does not always mean screen-up.",
        "session": "The session name or label chosen when importing the recording.",
        "head_file": "Name of the imported head-recording file.",
        "fitts_file": "Name of the imported pointing-task recording file.",
        "initial_rest_exclude_s": "Opening duration left out of each labelled initial-rest hold, in seconds. Measured from the saved hold-start time, or the first labelled hold sample if that time is unavailable. The preceding move-to-rest phase is never included. Affects initial-rest measurements, summary meshes and offsets from rest, not saved calibration, playback, directional holds or whole-recording statistics.",
        "samples": "Number of head poses retained in this recording after the head-recording opening and target-interval exclusions.",
        "fitts_samples": "Number of retained head samples labelled as part of the pointing task (task_phase = fitts_test), after the head-recording opening and target-interval exclusions.",
        "max_abs_relative_pitch_deg": "Largest retained relative pitch away from zero in either direction, in degrees, after the head-recording opening exclusion. Uses the original reference pose, not the recorded pose.",
        "near_gimbal_samples": "Number of retained samples where relative pitch is 80 degrees or more away from zero, after the head-recording opening exclusion. Near +/-90 degrees, yaw and roll become difficult to separate, so their angle ranges may be misleading.",
    }
    if key in definitions:
        return definitions[key]
    if key.startswith("head_initial_rest_"):
        measure = key.removeprefix("head_initial_rest_").removesuffix("_session_mean")
        initial = {
            "yaw_absolute_mean_deg": "Average recorded yaw during initial rest, in degrees. This is the tracker's heading, not necessarily a compass heading. Averaging accounts for angles wrapping at +/-180 degrees.",
            "yaw_relative_mean_deg": "Average initial-rest yaw measured from the calibration heading, in degrees. Only heading is removed, not calibration pitch or roll. Averaging accounts for angles wrapping at +/-180 degrees. Blank without saved calibration.",
            "yaw_mean_deg": "Average initial-rest yaw measured from the calibration heading, in degrees; the same value as yaw_relative_mean_deg. Only heading is removed, not calibration pitch or roll. Blank without saved calibration.",
            "yaw_calibration_mean_deg": "Calibration heading: yaw of the average saved calibration pose, in degrees. Uses the saved calibration quaternion if calibration samples are missing. This is calibration data, not an average of initial-rest samples.",
            "sample_count": "Number of recorded head poses during the labelled initial-rest hold.",
            "duration_s": "Time from the first to the last sample during initial rest, in seconds, using the recording computer's timestamps. Blank without enough of these timestamps.",
        }
        if measure in initial:
            description = initial[measure]
        elif measure in ("pitch_mean_deg", "roll_mean_deg"):
            axis = measure.split("_")[0]
            description = f"Average recorded {axis} during initial rest, in degrees. For xIMU3/AirPods, tilt is estimated relative to gravity and depends on correct mounting/fit. For webcam trackers, tilt is relative to the camera, not gravity."
        elif measure in ("yaw_std_deg", "pitch_std_deg", "roll_std_deg"):
            axis = measure.split("_")[0]
            description = f"Spread (SD): how much {axis} varies during initial rest, in degrees; a smaller value means a steadier hold. Calculated using population standard deviation." + (
                " Yaw is measured from the calibration heading; jumps at +/-180 degrees are removed before calculating spread. Blank without saved calibration." if axis == "yaw" else ""
            )
        else:
            raise ValueError(f"Missing initial-position field definition: {key}")
        return (
            description + " Only initial_rest.hold samples after the chosen opening exclusion are used; "
            "initial_rest.move samples and excluded target intervals are never included. "
            "The recording-opening exclusion does not affect initial pose. Calibration heading is kept from the saved calibration, "
            "not recalculated after exclusion. The participant result averages the available session values; "
            "average yaw accounts for wrapping at +/-180 degrees."
        )
    if key.startswith("head_range_of_movement_task_"):
        axis = key.removeprefix("head_range_of_movement_task_").split("_")[0]
        return (
            f"Movement range: largest minus smallest recorded {axis}, in degrees, during the movement exercise only "
            "(both moving and holding), excluding any skipped target intervals. "
            "Jumps at +/-180 degrees are removed before measuring the range. "
            "The participant result averages the available session ranges. Unlike Head movement stats, this uses "
            "recorded angles, not angles measured from the reference pose."
        )
    if key.startswith("head_range_of_movement_directions_"):
        direction = key.removeprefix("head_range_of_movement_directions_").split("_")[0]
        axis = "yaw" if direction in ("left", "right") else "pitch"
        measure = (
            f"Difference between average {axis} while holding {direction} and average {axis} during initial rest. "
            "The sign shows the direction of the change"
            if "_offset_from_rest_" in key else f"Average {axis} while holding the {direction} position in the movement exercise"
        )
        reference = (
            " Yaw is measured from the calibration heading when saved calibration is available; otherwise it is recorded yaw."
            if axis == "yaw" else " Pitch is recorded tilt: estimated relative to gravity for IMUs, or relative to the camera for webcam trackers."
        )
        return measure + ", in degrees. Only samples labelled as holding that position are used, excluding skipped target intervals. The participant result averages the available session values." + reference
    if key.startswith(("head_speed_", "head_acceleration_")):
        measure = (
            "Angular speed: how fast the head turns, in deg/s. Uses the shortest 3D rotation between consecutive head poses divided by the time between them"
            if key.startswith("head_speed_") else
            "Angular acceleration: how quickly turning speed changes, in deg/s2. Uses the size of the speed change divided by elapsed time, so speeding up and slowing down both give positive values"
        )
        aggregate = "Average" if key.endswith("_mean") else "Largest value"
        return (
            f"{aggregate} across usable sample intervals from all head recordings. {measure}. "
            "Excludes the selected opening duration of each head recording and skipped target intervals, "
            "including differences that cross any excluded interval. "
            "Otherwise includes preparation and the movement exercise, not just pointing. Changes across file boundaries "
            "are not used. Before calculating speed, intervals with zero/negative duration or less than half the "
            "recording's typical (median positive) sample interval are skipped."
        )
    if key.endswith("_range_deg"):
        axis = key.removeprefix("head_").removeprefix("fitts_").split("_")[0]
        scope = "samples labelled as part of the pointing task only" if key.startswith("fitts_") else "all samples in the recording"
        aggregate = " The participant result is the largest single-recording range, not a range spanning different files." if key.startswith("head_") else ""
        return (
            f"Movement range: largest minus smallest relative {axis}, in degrees, using {scope}, "
            "after the selected head-recording opening and target-interval exclusions. "
            "The reference pose is removed as a 3D rotation before extracting yaw, pitch and roll "
            "(inverse(reference) * recorded pose). Jumps at +/-180 degrees are removed before measuring the range. "
            "A tilted reference pose can mix changes between these angles; this is not a measure of tilt from gravity." + aggregate
        )
    raise ValueError(f"Missing field definition: {key}")


def format_number(value) -> str:
    """Format numbers to one decimal place without altering stored precision.

    None becomes blank; booleans/other values use str(). Table-specific
    inapplicable dashes are added by the UI, not embedded in numeric CSV data.
    """
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
         "IMU: gravity-level. Camera: level relative to the camera.", (2, 1), 0, "pitch"),
        ("Roll = 0.0 deg", "Front view: left/right axis is horizontal.",
         "IMU: no sideways tilt from gravity. Camera: level in the image.", (0, 1), 0, "roll"),
        ("Recorded yaw / calibration heading = 0.0 deg", "Top view: facing the tracker's heading zero.",
         "Calibration heading uses this same tracker reference.", (0, 2), 0, "yaw"),
        ("Yaw from calibration heading = 0.0 deg", "Top view: facing the calibration heading.",
         "Example heading only; not a participant measurement.", (0, 2), 35, "yaw"),
    )
    for title, description, note, axes, yaw, angle in definitions:
        color = ANGLE_COLORS[angle]
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
                f'<path d="M110 100 L{end_x:.1f} {end_y:.1f}" stroke="{color}" stroke-width="3"/>'
                '<text x="5" y="205" font-size="11">Dashed: tracker heading zero</text>'
                '<text x="5" y="220" font-size="11">Green: this yaw measure is zero</text>'
            )
        else:
            guides = (
                f'<path d="M15 100 H205" stroke="{color}" stroke-width="2" stroke-dasharray="4 3"/>'
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
        'Participant plots use a front-on view: zero yaw, pitch and roll faces straight out of the screen.</p>'
        '<div style="display:flex;flex-wrap:wrap">' + "".join(cards) + '</div>'
    )


def initial_pose_wireframes(
    rows: list[dict], mesh_path: Path | None = None, *, zero_axes: tuple[str, ...] = (),
) -> str:
    """Draw a display-only pose from calibration yaw and initial-rest pitch/roll.

    Use participant summary angles (degrees), optionally replace checked axes
    with zero for display, construct a canonical quaternion and map its rotation
    into mesh coordinates. This is a composite pose, not a pure rest/calibration
    estimate; it does not change statistical rows or calculate anatomical axes.
    Missing required angles produce a labelled unavailable plot.
    """
    if set(zero_axes) - {"yaw", "pitch", "roll"}:
        raise ValueError("Plot zero axes must be yaw, pitch or roll")
    cards = []
    geometry = None
    for row in rows:
        if row.get("head_initial_rest_sample_count_session_mean") == 0:
            participant = html.escape(row["participant"])
            cards.append(f"<figure><figcaption>{participant}</figcaption>"
                         "<p>No initial-rest samples remain after exclusion.</p></figure>")
            continue
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
            ("X", [0, 0, 1], ANGLE_COLORS["roll"]),
            ("Y", [1, 0, 0], ANGLE_COLORS["pitch"]),
            ("Z", [0, 1, 0], ANGLE_COLORS["yaw"]),
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
                if sign == "+" and axis in ("Y", "Z"):
                    name, value = ("Pitch", angles[1]) if axis == "Y" else ("Yaw", angles[0])
                    anchor = "start" if x >= 120 else "end"
                    label_x = x + 4 if x >= 120 else x - 4
                    axes_markup.append(
                        f'<text x="{label_x:.1f}" y="{y + 12:.1f}" fill="{color}" font-size="11" '
                        f'text-anchor="{anchor}">{name} {value:+.1f} deg</text>'
                    )
        caption = " / ".join(format_number(angle) for angle in angles)
        cards.append(
            f'<figure style="margin:8px;max-width:100%"><figcaption><b>{participant}</b><br>'
            f'Calibration heading / initial-rest pitch / roll: {caption} deg'
            + ("<br>Display-only zero: " + ", ".join(zero_axes) if zero_axes else "") + '</figcaption>'
            f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{participant} initial head pose" '
            f'viewBox="-60 0 360 285" style="width:360px;max-width:100%">'
            f'<g fill="none" stroke="#333" stroke-width="0.7">{lines}</g>{"".join(axes_markup)}'
            '<text x="5" y="220" font-size="10">Head axes: X forward, Y left, Z up</text>'
            '<text x="5" y="237" font-size="10">Yaw: + left / - right (about Z)</text>'
            '<text x="5" y="254" font-size="10">Pitch: + up / - down</text>'
            '<text x="5" y="271" font-size="10">Roll: + left ear down / - right ear down</text>'
            '</svg></figure>'
        )
    return '<div style="display:flex;flex-wrap:wrap">' + "".join(cards) + "</div>"


def summary_csv(rows: list[dict]) -> str:
    """Export ordered calculated fields using one-decimal format_number values.

    Preserve first-seen key order across rows; None becomes an empty cell.
    Prefix formula-like user text with an apostrophe for spreadsheet safety.
    No metric is recomputed or rescaled during export.
    """
    output = io.StringIO(newline="")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    # Treat user labels as text when opened in spreadsheet software.
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else format_number(v)
                         for k, v in row.items()})
    return output.getvalue()


def playback_payload(
    session: Session, mesh_path: Path | None = None, *, exclude_first_targets: int = 0,
    initial_rest_exclude_s: float = 0.0, head_opening_exclude_s: float = 0.0,
) -> dict:
    """Prepare full-resolution playback and its calculated plot/summary values.

    Retain excluded frames/targets with flags; summary filters are delegated to
    participant_rows/head_pretest_summary. Head poses are inverse(reference)*q;
    the checkbox also receives raw rotations. Head speed/acceleration plots use
    SessionStats forward-filled per-sample series on the displayed stream, not
    the filtered statistical series. Align provider clock to host by median
    offset for synchronised playback; all output times subtract span origin.

    Cursor speed is Euclidean consecutive sampled displacement / positive dt
    (px/s); first samples and non-positive intervals are 0, and trials are never
    bridged. Distance-to-target is Euclidean pixels. Full path uses path_points,
    with click appended, for completed-trial accuracy; JS calculates partial
    paths at seek time. No spatial/time resampling or dwell subtraction occurs.
    """
    retained_trials(session, exclude_first_targets)
    target_keep = _target_sample_mask(session, exclude_first_targets)
    movement_keep = _head_movement_mask(session, head_opening_exclude_s) & target_keep
    rest_ids = {id(s) for s in _initial_rest_after_opening(session, initial_rest_exclude_s)}
    included = {
        id(s): (bool(movement), id(s) in rest_ids and bool(target))
        for s, movement, target in zip(session.samples, movement_keep, target_keep)
    }
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
                "recorded_rotation": head_rotation_to_mesh_frame(
                    rotation_from_quaternion(*sample.quaternion)
                ).tolist(),
                "recorded_angles": np.degrees(qm.to_euler_zyx(sample.quaternion)).tolist(),
                "angles": angles, "quaternion": sample.quaternion.tolist(),
                "raw_angles": [sample.yaw_deg, sample.pitch_deg, sample.roll_deg],
                "speed": float(speed), "acceleration": float(accel),
                "phase": phase_description(sample.metadata.get("task_phase")),
                "movement_excluded": not included[id(sample)][0],
                "initial_pose_excluded": not included[id(sample)][1],
                "excluded": not all(included[id(sample)]),
            })
        head_graphs = [
            {"label": label, "color": color, "points": [[f["t"], value(f), f["excluded"]] for f in frames]}
            for label, color, value in (
                ("Relative yaw (deg)", ANGLE_COLORS["yaw"], lambda f: f["angles"][0]),
                ("Relative pitch (deg)", ANGLE_COLORS["pitch"], lambda f: f["angles"][1]),
                ("Angular speed (deg/s)", "#1f77b4", lambda f: f["speed"]),
                ("Angular acceleration (deg/s2)", "#ff7f0e", lambda f: f["acceleration"]),
            )
        ]
    origin = span[0]
    trials, speed_points, distance_points = [], [], []
    for trial_index, (trial, start, end) in enumerate(zip(session.trials, starts, ends)):
        item = asdict(trial)
        item["start"], item["end"] = float(start - origin), float(end - origin)
        item["excluded"] = trial_index < exclude_first_targets
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
            speed_points.append([t, float(speed), item["excluded"]])
            distance_points.append([t, float(np.linalg.norm(point[1:] - trial.target_pos)), item["excluded"]])
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
    pretest = head_pretest_summary(
        session, initial_rest_exclude_s=initial_rest_exclude_s, exclude_first_targets=exclude_first_targets,
    )
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
        "angle_colors": ANGLE_COLORS,
        "reference_pose_source": reference_pose_source(samples, session.recorder, session.samples),
        "cursor_graphs": [{"label": "Cursor speed (px/s)", "points": speed_points},
                          {"label": "Distance to target (px)", "points": distance_points}] if trials else [],
        "pretest_html": (
            "Grey chart backgrounds mark excluded time periods; coloured traces retain all data. Recording-opening exclusion applies to session "
            "movement statistics; hold-opening exclusion applies to initial pose and offsets from rest. "
            "Excluded target intervals are omitted from head statistics as well as pointing results. "
            "Saved calibration and the reference pose are unchanged.<br>" +
            "Initial-rest yaw is measured from the calibration heading (blank without saved calibration). "
            "Pitch/roll are recorded tilt: estimated relative to gravity for xIMU3/AirPods (depending on mounting/fit "
            "and sensor processing), or relative to the camera for webcam trackers, not gravity.<br>" +
            format_pretest_summary_html(pretest, decimal_places=1)
        ),
        "session_summary": participant_rows(
            [session], {session.participant: exclude_first_targets},
            initial_rest_exclude_s=initial_rest_exclude_s, head_opening_exclude_s=head_opening_exclude_s,
        )[0],
    }


def payload_json(payload: dict) -> str:
    return json.dumps(payload, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
