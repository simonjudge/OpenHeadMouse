"""Shared geometry/quaternion helpers for the MediaPipe canonical face mesh.

Downloads the official MediaPipe canonical face model and provides the
rotation/quaternion math used to orient it. Consumed by the pyqtgraph-based
session viewer (session_viewer/session_viewer.py).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from core.model_assets import MESH_SHA256, MESH_URL, download_verified

MEDIAPIPE_OBJ_URL = MESH_URL
MODELS_DIR = Path(__file__).parent.parent / "models"
OBJ_PATH = MODELS_DIR / "canonical_face_model.obj"


def download_mediapipe_model(path: Path = OBJ_PATH) -> Path:
    """Download the official MediaPipe canonical face model if not present."""
    return download_verified(MEDIAPIPE_OBJ_URL, path, MESH_SHA256)


def load_obj_wireframe(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return OBJ vertices, triangular faces and unique undirected edges."""
    vertices, faces = [], []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                vertices.append([float(v) for v in line.split()[1:4]])
            elif line.startswith("f "):
                idx = [int(token.split("/")[0]) - 1 for token in line.split()[1:]]
                for j in range(1, len(idx) - 1):
                    faces.append([idx[0], idx[j], idx[j + 1]])

    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)
    edges = set()
    for a, b, c in faces:
        edges.update((tuple(sorted((a, b))), tuple(sorted((b, c))), tuple(sorted((c, a)))))
    edges = np.asarray(sorted(edges), dtype=int)
    return vertices, faces, edges


def centre_and_scale(vertices: np.ndarray) -> np.ndarray:
    """Centre approximately between the eyes and scale to a compact display."""
    v = vertices.copy()
    centre = (v.min(axis=0) + v.max(axis=0)) / 2
    v -= centre
    scale = np.max(np.ptp(v, axis=0)) / 2
    v /= scale
    return v


def rotation_from_quaternion(qw: float, qx: float, qy: float, qz: float) -> np.ndarray:
    """Convert quaternion [w, x, y, z] to a 3x3 rotation matrix."""
    q = np.asarray([qw, qx, qy, qz], dtype=float)
    norm = np.linalg.norm(q)
    if norm == 0:
        return np.eye(3)
    w, x, y, z = q / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
    ])


# Canonical head frame (forward, left, up) expressed in MediaPipe face-model axes; the same P as
# providers/uniface_provider.py (R_head = P @ R_mesh @ P.T). The canonical mesh is authored in
# MediaPipe coordinates, so head-frame rotations from any provider are mapped back with
# R_mesh = P.T @ R_head @ P before being applied to its vertices.
_MESH_TO_HEAD_FRAME = np.array(
    [
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
)


def head_rotation_to_mesh_frame(r_head: np.ndarray) -> np.ndarray:
    """Re-express a head-frame rotation matrix in the canonical mesh's coordinate frame."""
    return _MESH_TO_HEAD_FRAME.T @ np.asarray(r_head, dtype=float) @ _MESH_TO_HEAD_FRAME


def calculate_neutral_quaternion(recorded_samples: list, recorder=None, reference_samples=None) -> np.ndarray:
    """Calculate neutral (reference) quaternion for visualization.

    Priority:
    1. Use quaternion from a 'recenter' event if available (from recorder._events):
       the last one at or before the first sample (the pose in effect when the
       shown data starts), else the first one
    2. Use the saved calibration quaternion (including Fitts initial rest).
    3. Average phase-labelled initial-rest hold samples.
    4. Average at most the first 30 samples within the first second of use.
    5. Default to identity only for an empty session.

    Args:
        recorded_samples: List of RecordedSample objects
        recorder: Optional Recorder instance that may contain events from loaded JSON
    """
    # Check for recenter event in recorder's events list
    if recorder is not None and hasattr(recorder, '_events'):
        recenters = [
            e for e in recorder._events
            if e.get('event_type') == 'recenter' and 'quaternion' in e.get('data', {})
        ]
        if recenters:
            chosen = recenters[0]
            first = recorded_samples[0] if recorded_samples else None
            if first is not None:
                # Prefer the shared host clock; provider clocks may differ between runs.
                key = 'host_time' if getattr(first, 'host_time', None) is not None else 'timestamp'
                start = getattr(first, key)
                before = [e for e in recenters if e.get(key) is not None and e[key] <= start]
                if before:
                    chosen = max(before, key=lambda e: e[key])
            q = np.array(chosen['data']['quaternion'], dtype=np.float64)
            if q.shape != (4,) or not np.all(np.isfinite(q)) or np.linalg.norm(q) == 0:
                raise ValueError("The session recenter event contains an invalid neutral quaternion")
            return q / np.linalg.norm(q)

    calibration = getattr(recorder, "calibration", None)
    if calibration and calibration.get("neutral_quaternion") is not None:
        q = np.asarray(calibration["neutral_quaternion"], dtype=float)
        if q.shape != (4,) or not np.all(np.isfinite(q)) or np.linalg.norm(q) == 0:
            raise ValueError("The session calibration contains an invalid neutral quaternion")
        return q / np.linalg.norm(q)

    calibration_samples = getattr(recorder, "calibration_samples", None)
    if calibration_samples:
        from core.quaternion_math import mean_quaternion

        return mean_quaternion(sample.quaternion for sample in calibration_samples)

    source_samples = reference_samples if reference_samples is not None else recorded_samples
    if not source_samples:
        return np.array([1.0, 0.0, 0.0, 0.0])  # Identity quaternion

    from core.quaternion_math import mean_quaternion

    rest = [
        sample for sample in source_samples
        if (getattr(sample, "metadata", None) or {}).get("task_phase") == "initial_rest.hold"
    ]
    reference = rest or [
        sample for sample in source_samples[:30]
        if 0 <= sample.timestamp - source_samples[0].timestamp <= 1.0
    ]
    return mean_quaternion(sample.quaternion for sample in reference)


def rotation_from_euler(yaw: float, pitch: float, roll: float, degrees: bool = True) -> np.ndarray:
    """Convert Euler angles (yaw, pitch, roll) to a 3x3 rotation matrix.

    Intrinsic Z-Y-X composition: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)
    (applied in order: yaw first, then pitch, then roll)
    """
    if degrees:
        yaw, pitch, roll = np.deg2rad([yaw, pitch, roll])
    cx, sx = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    cz, sz = np.cos(roll), np.sin(roll)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx
