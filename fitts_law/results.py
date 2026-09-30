"""ISO 9241-411 compliant results: trial logging, effective-width/throughput
stats, in the same style as core/session_stats.py (one method per metric,
plain dict returns, no I/O in the stats class).
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
    """Computes ISO 9241-411 effective-width/throughput stats from a list of trials."""

    def __init__(self, trials: list[TrialResult]):
        self.trials = trials

    def _grouped(self) -> dict[tuple[str, str], list[TrialResult]]:
        groups: dict[tuple[str, str], list[TrialResult]] = {}
        for t in self.trials:
            groups.setdefault((t.block_type, t.condition_label), []).append(t)
        return groups

    @staticmethod
    def _axis_signed_error(trial: TrialResult) -> float:
        """Signed deviation of the click from the target center, projected onto
        the start->target movement axis (the ISO accuracy measure)."""
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
        """We = 4.133 * SD of the axis-projected click error (ISO 9241-411)."""
        if len(trials) < 2:
            return 0.0
        errors = [FittsStats._axis_signed_error(t) for t in trials]
        return 4.133 * float(np.std(errors, ddof=1))

    @staticmethod
    def effective_amplitude(trials: list[TrialResult]) -> float:
        """Ae = mean actual start->click distance."""
        distances = [
            math.dist(t.start_pos, t.click_pos) for t in trials
        ]
        return float(np.mean(distances)) if distances else 0.0

    @staticmethod
    def index_of_difficulty(ae: float, we: float) -> float:
        if we <= 0:
            return 0.0
        return math.log2(ae / we + 1)

    @staticmethod
    def error_rate(trials: list[TrialResult]) -> float:
        if not trials:
            return 0.0
        return sum(1 for t in trials if not t.hit) / len(trials)

    @staticmethod
    def throughput_bits_per_s(trials: list[TrialResult]) -> float:
        """TP = IDe / mean(movement_time_s), the ISO 9241-411 throughput definition."""
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
        """Sampled cursor path as an (N, 2) array, ending at the click point."""
        points = [(p[1], p[2]) for p in (trial.trajectory or [])]
        if not points or tuple(points[-1]) != tuple(trial.click_pos):
            points.append(tuple(trial.click_pos))
        return np.asarray(points, dtype=float)

    @staticmethod
    def task_axis_coords(trial: TrialResult, points: np.ndarray | None = None) -> np.ndarray | None:
        """Path points rotated into task-axis coordinates.

        Column 0 is x' (distance along start->target), column 1 is y' (signed
        perpendicular distance from the task axis). None if start == target.
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
        signs = np.sign(values)
        signs = signs[signs != 0]
        return int(np.count_nonzero(signs[1:] != signs[:-1])) if len(signs) > 1 else 0

    @staticmethod
    def target_reentry(trial: TrialResult, points: np.ndarray | None = None) -> int:
        """TRE: times the pointer re-enters the target after first entering it."""
        if points is None:
            points = FittsStats.path_points(trial)
        inside = np.linalg.norm(points - np.asarray(trial.target_pos, dtype=float), axis=1) <= trial.width_px / 2
        entries = int(inside[0]) + int(np.count_nonzero(inside[1:] & ~inside[:-1]))
        return max(0, entries - 1)

    @staticmethod
    def task_axis_crossing(coords: np.ndarray) -> int:
        """TAC: times the path crosses the task axis (sign changes of y')."""
        return FittsStats._sign_changes(coords[:, 1])

    @staticmethod
    def movement_direction_change(coords: np.ndarray) -> int:
        """MDC: direction changes perpendicular to the task axis (sign changes of dy')."""
        return FittsStats._sign_changes(np.diff(coords[:, 1]))

    @staticmethod
    def orthogonal_direction_change(coords: np.ndarray) -> int:
        """ODC: direction changes along the task axis (sign changes of dx')."""
        return FittsStats._sign_changes(np.diff(coords[:, 0]))

    @staticmethod
    def movement_variability(coords: np.ndarray) -> float:
        """MV: sample SD of y' about its mean (px)."""
        return float(np.std(coords[:, 1], ddof=1)) if len(coords) > 1 else 0.0

    @staticmethod
    def movement_error(coords: np.ndarray) -> float:
        """ME: mean absolute deviation of the path from the task axis (px)."""
        return float(np.mean(np.abs(coords[:, 1])))

    @staticmethod
    def movement_offset(coords: np.ndarray) -> float:
        """MO: mean signed deviation of the path from the task axis (px)."""
        return float(np.mean(coords[:, 1]))

    @staticmethod
    def path_measures(trial: TrialResult, points: np.ndarray | None = None) -> dict | None:
        """All seven MacKenzie et al. (2001) measures for one trial's path.

        `points` may be a partial path (e.g. up to a playback position).
        Returns None when there is no usable path (fewer than 2 points).
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
