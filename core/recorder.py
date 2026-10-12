"""Session recording: save quaternions, gyro rates, and angles to JSON/CSV.

Optional initial-calibration data (the user's at-rest head pose captured
before tracking starts) is stored separately from movement samples: under a
top-level ``calibration`` key in JSON, or as ``phase == "calibration"`` rows
in CSV. Complete calibration and task streams may additionally use
``metadata.task_phase`` to identify each sample's experimental phase.

Subscribes to a provider's output queue in a background thread, accumulates
samples, and on stop() writes a timestamped session file (JSON or CSV format,
config-controlled) with full kinematic data for later playback and analysis.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from core import quaternion_math as qm
from core.clock import HOST_CLOCK_NAME, host_time as _host_time


@dataclass
class RecordedSample:
    """One row of recorded session data."""

    timestamp: float
    quaternion_w: float
    quaternion_x: float
    quaternion_y: float
    quaternion_z: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    # Provider-specific values are deliberately nested so adding metadata does
    # not change the meaning of the established pose fields.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Shared host clock (core.clock.host_time) at record time; None in legacy files.
    host_time: float | None = None

    @property
    def quaternion(self) -> np.ndarray:
        """Return quaternion as a numpy array [w, x, y, z]."""
        return np.array([self.quaternion_w, self.quaternion_x, self.quaternion_y, self.quaternion_z])


class Recorder:
    def __init__(
        self,
        output_dir: str | Path = "sessions/",
        format: str = "json",  # "json" or "csv"
    ):
        self.output_dir = Path(output_dir)
        self.format = format
        self._samples: list[RecordedSample] = []
        self._lock = threading.Lock()
        self._events: list[dict] = []  # Track recenter and other events with timestamps
        self._calibration: dict | None = None
        self._calibration_samples: list[RecordedSample] = []

    @property
    def sample_count(self) -> int:
        """Return the number of recorded samples."""
        with self._lock:
            return len(self._samples)

    @property
    def has_samples(self) -> bool:
        """Whether at least one sample has been recorded."""
        return self.sample_count > 0

    @property
    def has_calibration(self) -> bool:
        with self._lock:
            return self._calibration is not None

    @property
    def calibration(self) -> dict | None:
        """Calibration summary (without samples), or None."""
        with self._lock:
            return dict(self._calibration) if self._calibration is not None else None

    @property
    def calibration_samples(self) -> list[RecordedSample]:
        with self._lock:
            return list(self._calibration_samples)

    def set_calibration(
        self,
        samples: list[tuple[float, np.ndarray]],
        neutral_quaternion: np.ndarray,
        duration_s: float | None = None,
    ) -> None:
        """Store an initial-calibration capture, replacing any previous one.

        ``samples`` are ``(timestamp, quaternion)`` pairs of the absolute
        provider pose; ``neutral_quaternion`` is the at-rest pose derived
        from them.
        """
        recorded = [_make_sample(t, q) for t, q in samples]
        neutral = np.asarray(neutral_quaternion, dtype=np.float64)
        n_yaw, n_pitch, n_roll = np.degrees(qm.to_euler_zyx(neutral))
        if recorded:
            relative = np.array(
                [np.degrees(qm.to_euler_zyx(qm.relative_rotation(neutral, s.quaternion))) for s in recorded]
            )
            yaw_std, pitch_std, roll_std = (float(v) for v in relative.std(axis=0))
            start, end = recorded[0].timestamp, recorded[-1].timestamp
        else:
            yaw_std = pitch_std = roll_std = 0.0
            start = end = None
        summary = {
            "start_timestamp": start,
            "end_timestamp": end,
            "duration_s": float(duration_s) if duration_s is not None else None,
            "sample_count": len(recorded),
            "neutral_quaternion": [float(v) for v in neutral],
            "neutral_yaw_deg": float(n_yaw),
            "neutral_pitch_deg": float(n_pitch),
            "neutral_roll_deg": float(n_roll),
            "yaw_std_deg": yaw_std,
            "pitch_std_deg": pitch_std,
            "roll_std_deg": roll_std,
        }
        with self._lock:
            self._calibration = summary
            self._calibration_samples = recorded

    def record_sample(
        self,
        timestamp: float,
        quaternion: np.ndarray,
        metadata: Mapping[str, Any] | None = None,
        *,
        raw_metadata: Mapping[str, Any] | None = None,
        host_time: float | None = None,
    ) -> None:
        """Record a pose sample and optional provider metadata.

        ``raw_metadata`` is accepted as an alias for providers that expose
        that name (for example, ``ProviderSample.raw_metadata``). Metadata is
        stored under the additive ``metadata`` field in saved sessions.
        ``host_time`` defaults to ``core.clock.host_time()`` at call time.
        """
        if metadata is not None and raw_metadata is not None:
            raise ValueError("pass metadata or raw_metadata, not both")
        sample_metadata = metadata if metadata is not None else raw_metadata
        sample = _make_sample(timestamp, quaternion, sample_metadata)
        sample.host_time = float(host_time) if host_time is not None else _host_time()
        with self._lock:
            self._samples.append(sample)

    def record_event(self, timestamp: float, event_type: str, data: dict = None) -> None:
        """Record an event (e.g., recenter button pressed)."""
        event = {
            "timestamp": timestamp,
            "host_time": _host_time(),
            "event_type": event_type,
            "data": data or {},
        }
        with self._lock:
            self._events.append(event)
        print(f"[Event] {event_type} at {timestamp:.3f}s")

    def clear(self) -> None:
        """Discard recorded samples, events and calibration (start a new session)."""
        with self._lock:
            self._samples = []
            self._events = []
            self._calibration = None
            self._calibration_samples = []

    def save(self, filename: str | None = None) -> Path:
        """Write recorded samples to disk. Returns the output file path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = f"session_{int(time.time())}.{self.format}"
        filepath = self.output_dir / filename

        with self._lock:
            samples = self._samples.copy()
            calibration = dict(self._calibration) if self._calibration is not None else None
            calibration_samples = self._calibration_samples.copy()

        if self.format == "json":
            self._save_json(filepath, samples, calibration, calibration_samples)
        elif self.format == "csv":
            self._save_csv(filepath, samples, calibration_samples if calibration else None)
        else:
            raise ValueError(f"Unknown format: {self.format}")

        return filepath

    def _save_json(
        self,
        filepath: Path,
        samples: list[RecordedSample],
        calibration: dict | None = None,
        calibration_samples: list[RecordedSample] | None = None,
    ) -> None:
        with self._lock:
            events = self._events.copy()

        data = {
            # 1.1 only adds the optional "calibration" section.
            "format_version": "1.1" if calibration is not None else "1.0",
            "sample_count": len(samples),
            "clock": {"host_time": HOST_CLOCK_NAME},
            "events": events,
        }
        if calibration is not None:
            data["calibration"] = {
                **calibration,
                "samples": [asdict(s) for s in calibration_samples or []],
            }
        data["samples"] = [asdict(s) for s in samples]
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def _save_csv(
        self,
        filepath: Path,
        samples: list[RecordedSample],
        calibration_samples: list[RecordedSample] | None = None,
    ) -> None:
        import csv

        # A "phase" column is only added when calibration data exists, so
        # legacy CSV output is unchanged otherwise.
        with_phase = calibration_samples is not None
        rows = [("calibration", s) for s in calibration_samples or []] + [("movement", s) for s in samples]

        if not rows:
            with open(filepath, "w") as f:
                f.write("")
            return

        fieldnames = [
            "timestamp",
            "quaternion_w",
            "quaternion_x",
            "quaternion_y",
            "quaternion_z",
            "yaw_deg",
            "pitch_deg",
            "roll_deg",
        ]
        # Keep the legacy CSV header unchanged unless metadata is actually
        # present. This allows existing CSV consumers to continue unchanged.
        if any(sample.metadata for _, sample in rows):
            fieldnames.append("metadata")
        with_host_time = any(sample.host_time is not None for _, sample in rows)
        if with_host_time:
            fieldnames.append("host_time")
        if with_phase:
            fieldnames.append("phase")
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for phase, sample in rows:
                row = asdict(sample)
                if with_phase:
                    row["phase"] = phase
                if "metadata" in fieldnames:
                    row["metadata"] = json.dumps(sample.metadata, separators=(",", ":"))
                else:
                    row.pop("metadata", None)
                if not with_host_time:
                    row.pop("host_time", None)
                elif row["host_time"] is None:
                    row["host_time"] = ""
                writer.writerow(row)

    def load(self, filepath: Path) -> list[RecordedSample]:
        """Load a previously-saved session. Returns list of RecordedSample.

        Also stores events in self._events if loading from JSON format.
        Only movement samples are returned; calibration data (if any) is
        available via ``calibration`` / ``calibration_samples``.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Session file not found: {filepath}")

        if filepath.suffix == ".json":
            return self._load_json(filepath)
        elif filepath.suffix == ".csv":
            return self._load_csv(filepath)
        else:
            raise ValueError(f"Unknown format: {filepath.suffix}")

    def _load_json(self, filepath: Path) -> list[RecordedSample]:
        with open(filepath) as f:
            data = json.load(f)
        samples = [
            RecordedSample(
                **{**s, "metadata": s.get("metadata", {})},
            )
            for s in data["samples"]
        ]

        # Store events if present in the JSON
        if "events" in data:
            with self._lock:
                self._events = data["events"]

        calibration = data.get("calibration")
        with self._lock:
            if calibration is not None:
                calibration = dict(calibration)
                self._calibration_samples = [
                    RecordedSample(**{**s, "metadata": s.get("metadata", {})})
                    for s in calibration.pop("samples", [])
                ]
                self._calibration = calibration
            else:
                self._calibration = None
                self._calibration_samples = []

        return samples

    def _load_csv(self, filepath: Path) -> list[RecordedSample]:
        import csv

        samples = []
        calibration_samples = []
        has_phase = False
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            has_phase = "phase" in (reader.fieldnames or [])
            for row in reader:
                sample = RecordedSample(
                    timestamp=float(row["timestamp"]),
                    quaternion_w=float(row["quaternion_w"]),
                    quaternion_x=float(row["quaternion_x"]),
                    quaternion_y=float(row["quaternion_y"]),
                    quaternion_z=float(row["quaternion_z"]),
                    yaw_deg=float(row["yaw_deg"]),
                    pitch_deg=float(row["pitch_deg"]),
                    roll_deg=float(row["roll_deg"]),
                    metadata=json.loads(row["metadata"]) if row.get("metadata") else {},
                    host_time=float(row["host_time"]) if row.get("host_time") else None,
                )
                if row.get("phase") == "calibration":
                    calibration_samples.append(sample)
                else:
                    samples.append(sample)
        if has_phase:
            neutral = qm.mean_quaternion(s.quaternion for s in calibration_samples)
            self.set_calibration([(s.timestamp, s.quaternion) for s in calibration_samples], neutral)
        else:
            with self._lock:
                self._calibration = None
                self._calibration_samples = []
        return samples


def _make_sample(
    timestamp: float,
    quaternion: np.ndarray,
    metadata: Mapping[str, Any] | None = None,
) -> RecordedSample:
    q = np.asarray(quaternion, dtype=np.float64)
    yaw, pitch, roll = qm.to_euler_zyx(q)
    return RecordedSample(
        timestamp=float(timestamp),
        quaternion_w=float(q[0]),
        quaternion_x=float(q[1]),
        quaternion_y=float(q[2]),
        quaternion_z=float(q[3]),
        yaw_deg=float(np.degrees(yaw)),
        pitch_deg=float(np.degrees(pitch)),
        roll_deg=float(np.degrees(roll)),
        metadata=_json_compatible(metadata or {}),
    )


def _json_compatible(value: Any) -> Any:
    """Convert common numpy/provider values into JSON-compatible values."""
    if isinstance(value, np.ndarray):
        return [_json_compatible(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value
