"""Shared geometry/quaternion helpers for the MediaPipe canonical face mesh.

Downloads the official MediaPipe canonical face model and provides the
rotation/quaternion math used to orient it. Consumed by the pyqtgraph-based
session viewer (session_viewer/session_viewer.py).
"""

from __future__ import annotations

import ssl
import urllib.request
from pathlib import Path

import numpy as np

MEDIAPIPE_OBJ_URL = (
    "https://raw.githubusercontent.com/google-ai-edge/mediapipe/"
    "master/mediapipe/modules/face_geometry/data/canonical_face_model.obj"
)
MODELS_DIR = Path(__file__).parent.parent / "models"
OBJ_PATH = MODELS_DIR / "canonical_face_model.obj"


def download_mediapipe_model(path: Path = OBJ_PATH) -> Path:
    """Download the official MediaPipe canonical face model if not present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        print(f"Downloading MediaPipe face model to {path}...")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(MEDIAPIPE_OBJ_URL, context=ctx) as response:
                with open(path, 'wb') as f:
                    f.write(response.read())
        except Exception as e:
            print(f"Failed to download: {e}")
            raise
    return path


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


def calculate_neutral_quaternion(recorded_samples: list, recorder=None) -> np.ndarray:
    """Calculate neutral (reference) quaternion for visualization.

    Priority:
    1. Use quaternion from a 'recenter' event if available (from recorder._events)
    2. Otherwise calculate mean quaternion (in same hemisphere)
    3. Default to identity if no samples

    Args:
        recorded_samples: List of RecordedSample objects
        recorder: Optional Recorder instance that may contain events from loaded JSON
    """
    # Check for recenter event in recorder's events list
    if recorder is not None and hasattr(recorder, '_events'):
        for event in recorder._events:
            if event.get('event_type') == 'recenter':
                data = event.get('data', {})
                if 'quaternion' in data:
                    q = np.array(data['quaternion'], dtype=np.float64)
                    # Ensure it's normalized
                    return q / np.linalg.norm(q)

    if not recorded_samples:
        return np.array([1.0, 0.0, 0.0, 0.0])  # Identity quaternion

    # Calculate mean quaternion keeping all in same hemisphere
    quaternions = []
    for sample in recorded_samples:
        q = sample.quaternion if isinstance(sample.quaternion, np.ndarray) else np.array(sample.quaternion)
        quaternions.append(q / np.linalg.norm(q))  # Normalize

    # Use first as reference, flip others if in opposite hemisphere
    q_ref = quaternions[0]
    q_avg = q_ref.copy()

    for q in quaternions[1:]:
        # If dot product is negative, quaternion is in opposite hemisphere
        if np.dot(q_ref, q) < 0:
            q = -q
        q_avg += q

    q_avg = q_avg / len(quaternions)
    # Normalize result
    q_neutral = q_avg / np.linalg.norm(q_avg)

    return q_neutral


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


