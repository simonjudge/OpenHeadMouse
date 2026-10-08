"""Tests for the pure math in providers/uniface_provider.py.

The camera-capture / MediaPipe inference loop (UniFaceProvider._run) needs a
real webcam and is verified manually (see the module's __main__ console
test), not here. What's tested here is the axis-remapping math, which is
pure and hardware-independent:
  - the MediaPipe-camera-frame -> head-frame permutation is a proper
    rotation (orthogonal, determinant +1 — no accidental reflection)
  - it preserves rotation angle (a similarity transform by a proper rotation
    can only relabel the rotation axis, never change the angle) — this is a
    sign-agnostic invariant, so it can't hide a sign-convention bug, but it
    does catch outright math errors (wrong matrix, reflection, bad formula)
  - identity input maps to identity output
  - the rotation-matrix -> quaternion conversion matches scipy
"""

import numpy as np
from scipy.spatial.transform import Rotation

from providers import uniface_provider
from providers.uniface_provider import (
    _CAMERA_TO_HEAD_FRAME,
    _resolve_model_path,
    _rotation_matrix_to_quaternion,
    _transformation_matrix_to_head_quaternion,
)

RNG = np.random.default_rng(seed=7)


def test_model_path_resolves_from_application_bundle(monkeypatch, tmp_path):
    module_path = tmp_path / "providers" / "uniface_provider.py"
    module_path.parent.mkdir()
    bundled_model = tmp_path / "models" / "face_landmarker.task"
    bundled_model.parent.mkdir()
    bundled_model.touch()
    monkeypatch.setattr(uniface_provider, "__file__", str(module_path))
    monkeypatch.chdir(tmp_path / "providers")

    assert _resolve_model_path("models/face_landmarker.task") == bundled_model


def test_model_path_preserves_existing_custom_path(tmp_path):
    custom_model = tmp_path / "custom.task"
    custom_model.touch()

    assert _resolve_model_path(str(custom_model)) == custom_model


def _rotation_angle(r: np.ndarray) -> float:
    trace = np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.arccos(trace))


def test_camera_to_head_permutation_is_a_proper_rotation():
    p = _CAMERA_TO_HEAD_FRAME
    assert np.allclose(p @ p.T, np.eye(3), atol=1e-10)
    assert np.isclose(np.linalg.det(p), 1.0, atol=1e-10)


def test_rotation_matrix_to_quaternion_matches_scipy():
    for _ in range(20):
        r = Rotation.from_euler("xyz", RNG.uniform(-np.pi, np.pi, size=3))
        matrix = r.as_matrix()
        result = _rotation_matrix_to_quaternion(matrix)

        x, y, z, w = r.as_quat()
        expected = np.array([w, x, y, z])

        assert np.allclose(result, expected, atol=1e-8) or np.allclose(result, -expected, atol=1e-8)


def test_identity_camera_rotation_maps_to_identity_head_quaternion():
    q = _transformation_matrix_to_head_quaternion(np.eye(4))
    assert np.allclose(q, [1.0, 0.0, 0.0, 0.0], atol=1e-10) or np.allclose(q, [-1.0, 0.0, 0.0, 0.0], atol=1e-10)


def test_transformation_matrix_to_head_quaternion_preserves_rotation_angle():
    for _ in range(20):
        r_mp = Rotation.from_euler("xyz", RNG.uniform(-np.pi, np.pi, size=3)).as_matrix()
        q_head = _transformation_matrix_to_head_quaternion(r_mp)
        r_head = Rotation.from_quat([q_head[1], q_head[2], q_head[3], q_head[0]]).as_matrix()

        assert np.isclose(_rotation_angle(r_mp), _rotation_angle(r_head), atol=1e-6)


def test_transformation_matrix_handles_4x4_with_translation_row():
    r_mp = Rotation.from_euler("xyz", [0.2, -0.3, 0.1]).as_matrix()
    matrix4x4 = np.eye(4)
    matrix4x4[:3, :3] = r_mp
    matrix4x4[:3, 3] = [10.0, -5.0, 3.0]  # translation should be ignored

    q = _transformation_matrix_to_head_quaternion(matrix4x4)
    assert np.isclose(np.linalg.norm(q), 1.0, atol=1e-10)
