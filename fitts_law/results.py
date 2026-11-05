"""Fitts results: trial logging and ISO-style effective-width/throughput
stats, in the same style as core/session_stats.py (one method per metric,
plain dict returns, no I/O in the stats class).

Legacy summaries pool conditions and use Euclidean effective amplitude.
They are descriptive results, not sequence-level ISO 9241-411 estimates.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

from core.clock import HOST_CLOCK_NAME

_DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "fitts_sessions"


@dataclass
class TrialResult:
    """One completed reach-and-click trial.

    `trial_index` is this trial's position in the overall session (0-based);
    `repetition_index` is its position within its own condition's repeated
    block (0-based) - both are saved so a post-hoc analysis can, e.g., discard
    the first repetition of each condition as a practice/warm-up trial without
    losing any other information. `trajectory` is the raw mouse path sampled
    during the trial ([t_offset_s, x, y] from trial start to click), saved so
    post-hoc analysis isn't limited to the endpoint alone.

    `start_time`/`end_time` are `time.monotonic()` readings (used for MT);
    `start_host_time`/`end_host_time` are the same instants on the shared
    host clock (core.clock.host_time), used to synchronise playback with a
    head-movement recording. They are None in files saved before 1.1.
    """

    condition_label: str
    block_type: str  # "circle" | "edge" | "random_path" | "typing"
    amplitude_px: float
    width_px: float
    start_pos: tuple[float, float]
    target_pos: tuple[float, float]
    click_pos: tuple[float, float]
    start_time: float
    end_time: float
    movement_time_s: float
    hit: bool
    trial_index: int = 0
    repetition_index: int = 0
    trajectory: list[list[float]] = None
    start_host_time: float | None = None
    end_host_time: float | None = None

    def __post_init__(self):
        if self.trajectory is None:
            self.trajectory = []


class FittsResultsLogger:
    """Accumulates TrialResults and saves/loads them as JSON."""

    def __init__(self, output_dir: str | Path = _DEFAULT_OUTPUT_DIR):
        self.output_dir = Path(output_dir)
        self.trials: list[TrialResult] = []
        self.metadata: dict = {
            "access_method": None,
            "access_method_other": None,
            "notes": None,
            "screen_width_px": None,
            "screen_height_px": None,
            "clock": {"host_time": HOST_CLOCK_NAME},
        }

    def add_trial(self, trial: TrialResult) -> None:
        self.trials.append(trial)

    def save(self, filepath: str | Path | None = None) -> Path:
        """Write all accumulated trials to disk as JSON. Returns the file path.

        `filepath` may be a bare filename (saved under `self.output_dir`) or a
        full path (e.g. chosen via a "Save As..." file dialog), used as-is.
        """
        if filepath is None:
            filepath = f"fitts_session_{int(time.time())}.json"
        filepath = Path(filepath)
        if filepath.parent == Path("."):
            filepath = self.output_dir / filepath
        filepath.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "format_version": "1.1",
            "trial_count": len(self.trials),
            "metadata": self.metadata,
            "trials": [asdict(t) for t in self.trials],
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        return filepath

    def load(self, filepath: str | Path) -> list[TrialResult]:
        filepath = Path(filepath)
        with open(filepath) as f:
            data = json.load(f)
        self.metadata = data.get("metadata", {
            "access_method": None,
            "access_method_other": None,
            "notes": None,
        })
        known = {f.name for f in fields(TrialResult)}
        trials = [
            TrialResult(**{
                **{k: v for k, v in t.items() if k in known},
                "start_pos": tuple(t["start_pos"]),
                "target_pos": tuple(t["target_pos"]),
                "click_pos": tuple(t["click_pos"]),
            })
            for t in data["trials"]
        ]
        self.trials = trials
        return trials


class FittsStats:
    """Calculate pointing and path measures from the supplied targets.

    All targets, including misses, contribute unless the caller filters them.
    Distances are pixels, times seconds, ID bits and throughput bits/second.
    Effective width uses the usual 4.133 factor, but Euclidean effective
    amplitude and pooling across conditions are legacy descriptive choices,
    not sequence-level task-axis ISO 9241-411 throughput.
    """

    def __init__(self, trials: list[TrialResult]):
        self.trials = trials

    def _grouped(self) -> dict[tuple[str, str], list[TrialResult]]:
        """Group targets by saved (block_type, condition_label), without filtering."""
        groups: dict[tuple[str, str], list[TrialResult]] = {}
        for t in self.trials:
            groups.setdefault((t.block_type, t.condition_label), []).append(t)
        return groups

    @staticmethod
    def _axis_signed_error(trial: TrialResult) -> float:
        """Project click error onto the presented movement axis, in pixels.

        For start S, target T and click C, return (C-T) dot (T-S)/|T-S|.
        Positive is an overshoot along that axis. A zero-length axis returns 0.
        """
        sx, sy = trial.start_pos
        tx, ty = trial.target_pos
        cx, cy = trial.click_pos
        axis = np.array([tx - sx, ty - sy], dtype=float)
        norm = np.linalg.norm(axis)
        if norm == 0:
            return 0.0
        axis_unit = axis / norm
        error = np.array([cx - tx, cy - ty], dtype=float)
        return float(np.dot(error, axis_unit))

    @staticmethod
    def effective_width(trials: list[TrialResult]) -> float:
        """Return We = 4.133 * sample_SD(projected click errors), in pixels.

        SD divides squared deviations by n-1 (ddof=1); misses are included.
        Fewer than two targets return 0 under the existing degenerate convention.
        """
        if len(trials) < 2:
            return 0.0
        errors = [FittsStats._axis_signed_error(t) for t in trials]
        return 4.133 * float(np.std(errors, ddof=1))

    @staticmethod
    def effective_amplitude(trials: list[TrialResult]) -> float:
        """Return Ae = arithmetic mean(|click-start|), in pixels.

        This uses Euclidean distances, including misses, not task-axis
        projections or previous-endpoint corrections. Empty input returns 0.
        """
        distances = [
            math.dist(t.start_pos, t.click_pos) for t in trials
        ]
        return float(np.mean(distances)) if distances else 0.0

    @staticmethod
    def index_of_difficulty(ae: float, we: float) -> float:
        """Return effective Shannon IDe = log2(Ae/We + 1), in bits.

        We <= 0 returns 0 as a legacy degenerate estimate, not evidence of
        zero difficulty/performance. This is not geometry-based planned ID.
        """
        if we <= 0:
            return 0.0
        return math.log2(ae / we + 1)

    @staticmethod
    def error_rate(trials: list[TrialResult]) -> float:
        """Return misses / target count as a fraction in [0, 1]; empty input is 0."""
        if not trials:
            return 0.0
        return sum(1 for t in trials if not t.hit) / len(trials)

    @staticmethod
    def throughput_bits_per_s(trials: list[TrialResult]) -> float:
        """Return pooled TP = IDe / arithmetic mean(recorded MT), in bits/s.

        Recalculate Ae, We and IDe from this target pool; do not average
        individual throughputs or condition throughputs. Empty input or
        non-positive mean MT returns 0. No dwell subtraction occurs here.
        """
        if not trials:
            return 0.0
        ae = FittsStats.effective_amplitude(trials)
        we = FittsStats.effective_width(trials)
        ide = FittsStats.index_of_difficulty(ae, we)
        mean_mt = float(np.mean([t.movement_time_s for t in trials]))
        if mean_mt <= 0:
            return 0.0
        return ide / mean_mt

    # -- Path accuracy measures (MacKenzie, Kauppinen & Silfverberg, CHI 2001) --

    @staticmethod
    def path_points(trial: TrialResult) -> np.ndarray:
        """Return recorded cursor (x,y) samples, appending the click if needed.

        Sample times are discarded. The saved start position is not prepended;
        without trajectory samples the result contains just the click.
        """
        points = [(p[1], p[2]) for p in (trial.trajectory or [])]
        if not points or tuple(points[-1]) != tuple(trial.click_pos):
            points.append(tuple(trial.click_pos))
        return np.asarray(points, dtype=float)

    @staticmethod
    def task_axis_coords(trial: TrialResult, points: np.ndarray | None = None) -> np.ndarray | None:
        """Path points rotated into task-axis coordinates.

        Column 0 is x' (distance along start->target), column 1 is y' (signed
        perpendicular distance from the task axis). None if start == target.
        For u=(target-start)/|target-start| and r=point-start, x'=r dot u
        and y'=-r.x*u.y+r.y*u.x. Coordinates/distances are pixels.
        """
        if points is None:
            points = FittsStats.path_points(trial)
        start = np.asarray(trial.start_pos, dtype=float)
        axis = np.asarray(trial.target_pos, dtype=float) - start
        norm = np.linalg.norm(axis)
        if norm == 0:
            return None
        ux, uy = axis / norm
        rel = points - start
        x_axis = rel[:, 0] * ux + rel[:, 1] * uy
        y_axis = -rel[:, 0] * uy + rel[:, 1] * ux
        return np.column_stack([x_axis, y_axis])

    @staticmethod
    def _sign_changes(values: np.ndarray) -> int:
        """Count sign reversals after removing exact zeros (no noise threshold)."""
        signs = np.sign(values)
        signs = signs[signs != 0]
        return int(np.count_nonzero(signs[1:] != signs[:-1])) if len(signs) > 1 else 0

    @staticmethod
    def target_reentry(trial: TrialResult, points: np.ndarray | None = None) -> int:
        """Return TRE = max(number of outside-to-inside entries - 1, 0).

        A point is inside when distance to target <= width/2; an initially
        inside point counts as the first entry. This sampled circular-region
        estimate does not detect crossings between samples or keyboard shapes.
        """
        if points is None:
            points = FittsStats.path_points(trial)
        inside = np.linalg.norm(points - np.asarray(trial.target_pos, dtype=float), axis=1) <= trial.width_px / 2
        entries = int(inside[0]) + int(np.count_nonzero(inside[1:] & ~inside[:-1]))
        return max(0, entries - 1)

    @staticmethod
    def task_axis_crossing(coords: np.ndarray) -> int:
        """Return TAC: sign changes of perpendicular coordinates y', ignoring zeros."""
        return FittsStats._sign_changes(coords[:, 1])

    @staticmethod
    def movement_direction_change(coords: np.ndarray) -> int:
        """Return MDC: sign changes of consecutive y' differences, ignoring zeros.

        Counts reversals perpendicular to the task axis, without smoothing.
        """
        return FittsStats._sign_changes(np.diff(coords[:, 1]))

    @staticmethod
    def orthogonal_direction_change(coords: np.ndarray) -> int:
        """Return ODC: sign changes of consecutive x' differences, ignoring zeros.

        Counts reversals along the task axis, without smoothing.
        """
        return FittsStats._sign_changes(np.diff(coords[:, 0]))

    @staticmethod
    def movement_variability(coords: np.ndarray) -> float:
        """Return MV = sqrt(sum((y'-mean(y'))^2)/(n-1)), in pixels; n<2 gives 0."""
        return float(np.std(coords[:, 1], ddof=1)) if len(coords) > 1 else 0.0

    @staticmethod
    def movement_error(coords: np.ndarray) -> float:
        """Return ME = mean(abs(y')), the unsigned task-axis deviation in pixels."""
        return float(np.mean(np.abs(coords[:, 1])))

    @staticmethod
    def movement_offset(coords: np.ndarray) -> float:
        """Return MO = mean(y'), the signed task-axis offset in pixels."""
        return float(np.mean(coords[:, 1]))

    @staticmethod
    def path_measures(trial: TrialResult, points: np.ndarray | None = None) -> dict | None:
        """All seven MacKenzie et al. (2001) measures for one trial's path.

        `points` may be a partial path (e.g. up to a playback position).
        Returns None with fewer than two points or a zero start-to-target axis.
        Points are not resampled or smoothed; path measures use equal sample
        weights rather than elapsed-time weights.
        """
        if points is None:
            points = FittsStats.path_points(trial)
        if len(points) < 2:
            return None
        coords = FittsStats.task_axis_coords(trial, points)
        if coords is None:
            return None
        return {
            "tre": FittsStats.target_reentry(trial, points),
            "tac": FittsStats.task_axis_crossing(coords),
            "mdc": FittsStats.movement_direction_change(coords),
            "odc": FittsStats.orthogonal_direction_change(coords),
            "mv_px": FittsStats.movement_variability(coords),
            "me_px": FittsStats.movement_error(coords),
            "mo_px": FittsStats.movement_offset(coords),
        }

    @staticmethod
    def accuracy_summary(trials: list[TrialResult]) -> dict:
        """Mean of each path measure over trials with a usable path.

        TRE/TAC/MDC/ODC are mean counts per trial; MV/ME/MO are mean px.
        Each usable target has equal weight regardless of MT or sample count.
        Misses remain included. With none usable, measures are None and
        trial_count is 0; a fractional TRE below 1 is therefore valid.
        """
        measures = [m for m in (FittsStats.path_measures(t) for t in trials) if m is not None]
        keys = ("tre", "tac", "mdc", "odc", "mv_px", "me_px", "mo_px")
        summary = {k: (float(np.mean([m[k] for m in measures])) if measures else None) for k in keys}
        summary["trial_count"] = len(measures)
        return summary

    def summary(self, exclude_repetitions: int = 0) -> dict:
        """Per-condition breakdown plus an overall pooled throughput.

        `exclude_repetitions` drops the first N repetitions of each condition
        (by `repetition_index`) before computing stats - e.g. pass 1 to treat
        each condition's first pass as an untimed practice trial.

        Within each group, and again over all retained targets pooled together,
        compute Ae, We, IDe, arithmetic mean MT, error fraction and TP using
        the helpers above. Overall values are not means of group values.
        The notebook's first-N-target exclusion is a separate caller-side
        filter, not this repetition-index filter. Empty overall pools return
        zeros here; notebook adapters replace unavailable measures with None.
        """
        per_condition = {}
        all_filtered: list[TrialResult] = []
        for (block_type, label), trials in self._grouped().items():
            trials = [t for t in trials if t.repetition_index >= exclude_repetitions]
            if not trials:
                continue
            all_filtered.extend(trials)
            ae = self.effective_amplitude(trials)
            we = self.effective_width(trials)
            ide = self.index_of_difficulty(ae, we)
            mean_mt = float(np.mean([t.movement_time_s for t in trials]))
            per_condition[f"{block_type}:{label}"] = {
                "block_type": block_type,
                "condition_label": label,
                "trial_count": len(trials),
                "amplitude_px": trials[0].amplitude_px,
                "width_px": trials[0].width_px,
                "effective_amplitude_px": ae,
                "effective_width_px": we,
                "index_of_difficulty_bits": ide,
                "mean_movement_time_s": mean_mt,
                "error_rate": self.error_rate(trials),
                "throughput_bits_per_s": self.throughput_bits_per_s(trials),
            }

        overall_ae = self.effective_amplitude(all_filtered)
        overall_we = self.effective_width(all_filtered)
        overall_ide = self.index_of_difficulty(overall_ae, overall_we)
        overall_mean_mt = float(np.mean([t.movement_time_s for t in all_filtered])) if all_filtered else 0.0
        overall_throughput = self.throughput_bits_per_s(all_filtered) if all_filtered else 0.0
        return {
            "conditions": per_condition,
            "overall_effective_amplitude_px": overall_ae,
            "overall_effective_width_px": overall_we,
            "overall_index_of_difficulty_bits": overall_ide,
            "overall_mean_movement_time_s": overall_mean_mt,
            "overall_throughput_bits_per_s": overall_throughput,
            "overall_error_rate": self.error_rate(all_filtered),
            "trial_count": len(all_filtered),
        }
