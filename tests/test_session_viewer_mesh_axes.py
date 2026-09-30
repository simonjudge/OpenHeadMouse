"""Tests that the session viewer's wireframe rotates about the correct mesh axes."""

import numpy as np
import pytest

from core import quaternion_math as qm
from providers.uniface_provider import _CAMERA_TO_HEAD_FRAME
from session_viewer.mesh_visualizer import head_rotation_to_mesh_frame, rotation_from_quaternion

# Canonical mesh frame: X = lateral, Y = up, Z = out of the face.
MESH_LATERAL, MESH_UP, MESH_OUT = np.eye(3)


def _mesh_rotation(yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0):
    q = qm.from_euler_zyx(*np.radians([yaw_deg, pitch_deg, roll_deg]))
    return head_rotation_to_mesh_frame(rotation_from_quaternion(*q))


@pytest.mark.parametrize(
    "angles, fixed_axis",
    [
        ({"yaw_deg": 30.0}, MESH_UP),        # turning the head spins about the vertical axis
        ({"pitch_deg": 30.0}, MESH_LATERAL),  # nodding spins about the ear-to-ear axis
        ({"roll_deg": 30.0}, MESH_OUT),       # tilting spins about the nose axis
    ],
)
def test_each_euler_angle_rotates_mesh_about_expected_axis(angles, fixed_axis):
    R = _mesh_rotation(**angles)
    np.testing.assert_allclose(R @ fixed_axis, fixed_axis, atol=1e-9)
    assert not np.allclose(R, np.eye(3))


def test_inverts_uniface_camera_to_head_mapping():
    r_mesh = rotation_from_quaternion(*qm.from_euler_zyx(0.3, -0.2, 0.1))
    r_head = _CAMERA_TO_HEAD_FRAME @ r_mesh @ _CAMERA_TO_HEAD_FRAME.T
    np.testing.assert_allclose(head_rotation_to_mesh_frame(r_head), r_mesh, atol=1e-12)
