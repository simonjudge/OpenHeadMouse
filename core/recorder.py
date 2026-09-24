"""Session recording: save quaternions, gyro rates, and angles to JSON/CSV.

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

    @property
    def sample_count(self) -> int:
        """Return the number of recorded samples."""
        with self._lock:
            return len(self._samples)

    @property
    def has_samples(self) -> bool:
        """Whether at least one sample has been recorded."""
        return self.sample_count > 0

    def record_sample(
        self,
        timestamp: float,
        quaternion: np.ndarray,
        metadata: Mapping[str, Any] | None = None,
        *,
        raw_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a pose sample and optional provider metadata.

        ``raw_metadata`` is accepted as an alias for providers that expose
        that name (for example, ``ProviderSample.raw_metadata``). Metadata is
        stored under the additive ``metadata`` field in saved sessions.
        """
        if metadata is not None and raw_metadata is not None:
            raise ValueError("pass metadata or raw_metadata, not both")
        sample_metadata = metadata if metadata is not None else raw_metadata
        q = np.asarray(quaternion, dtype=np.float64)
        yaw, pitch, roll = qm.to_euler_zyx(q)
        sample = RecordedSample(
            timestamp=float(timestamp),
            quaternion_w=float(q[0]),
            quaternion_x=float(q[1]),
            quaternion_y=float(q[2]),
            quaternion_z=float(q[3]),
            yaw_deg=float(np.degrees(yaw)),
            pitch_deg=float(np.degrees(pitch)),
            roll_deg=float(np.degrees(roll)),
            metadata=_json_compatible(sample_metadata or {}),
        )
        with self._lock:
            self._samples.append(sample)

    def record_event(self, timestamp: float, event_type: str, data: dict = None) -> None:
        """Record an event (e.g., recenter button pressed)."""
        event = {"timestamp": timestamp, "event_type": event_type, "data": data or {}}
        with self._lock:
            self._events.append(event)
        print(f"[Event] {event_type} at {timestamp:.3f}s")

    def save(self, filename: str | None = None) -> Path:
        """Write recorded samples to disk. Returns the output file path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = f"session_{int(time.time())}.{self.format}"
        filepath = self.output_dir / filename

        with self._lock:
            samples = self._samples.copy()

        if self.format == "json":
            self._save_json(filepath, samples)
        elif self.format == "csv":
            self._save_csv(filepath, samples)
        else:
            raise ValueError(f"Unknown format: {self.format}")

        return filepath

    def _save_json(self, filepath: Path, samples: list[RecordedSample]) -> None:
        with self._lock:
            events = self._events.copy()

        data = {
            "format_version": "1.0",
            "sample_count": len(samples),
            "events": events,
            "samples": [asdict(s) for s in samples],
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def _save_csv(self, filepath: Path, samples: list[RecordedSample]) -> None:
        import csv

        if not samples:
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
        if any(sample.metadata for sample in samples):
            fieldnames.append("metadata")
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for sample in samples:
                row = asdict(sample)
                if "metadata" in fieldnames:
                    row["metadata"] = json.dumps(sample.metadata, separators=(",", ":"))
                else:
                    row.pop("metadata", None)
                writer.writerow(row)

    def load(self, filepath: Path) -> list[RecordedSample]:
        """Load a previously-saved session. Returns list of RecordedSample.

        Also stores events in self._events if loading from JSON format.
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

        return samples

    def _load_csv(self, filepath: Path) -> list[RecordedSample]:
        import csv

        samples = []
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
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
                )
                samples.append(sample)
        return samples


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
