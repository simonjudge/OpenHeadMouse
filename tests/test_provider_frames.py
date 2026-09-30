"""Cross-provider frame consistency: the same physical head motion must give the same
yaw/pitch/roll signs whichever provider measured it (see COORDINATE_FRAMES.md)."""

import json

import numpy as np
import pytest

from core import quaternion_math as qm
from session_viewer.mesh_visualizer import rotation_from_quaternion

ANGLE_DEG = 20.0


def _axis_angle(axis, angle_deg):
    axis = np.asarray(axis, dtype=float)
    half = np.radians(angle_deg) / 2.0
    return np.concatenate(([np.cos(half)], np.sin(half) * axis / np.linalg.norm(axis)))


# Each device's native axes, and the right-hand rotation that produces each physical motion.
#   AirPods (CMHeadphoneMotionManager): X right, Y forward, Z up
#   xIMU3 (NED/FRD body):               X forward, Y right, Z down
#   UniFace (MediaPipe face model):     X subject's left, Y up, Z out of face
PHYSICAL_MOTIONS = {
    "airpods": {
        "turn_left": ([0, 0, 1], +1),
        "look_up": ([1, 0, 0], +1),
        "left_ear_down": ([0, 1, 0], -1),
    },
    "ximu3": {
        "turn_left": ([0, 0, 1], -1),
        "look_up": ([0, 1, 0], +1),
        "left_ear_down": ([1, 0, 0], -1),
    },
    "uniface": {
        "turn_left": ([0, 1, 0], +1),
        "look_up": ([1, 0, 0], -1),
        "left_ear_down": ([0, 0, 1], -1),
    },
}

EXPECTED_INDEX = {"turn_left": 0, "look_up": 1, "left_ear_down": 2}  # yaw, pitch, roll


def _airpods_to_head(q_raw):
    from providers.airpods.airpods_provider import AirPodsProvider

    provider = AirPodsProvider()
    w, x, y, z = q_raw
    packet = {"timestamp": 0.0, "quaternion": {"w": w, "x": x, "y": y, "z": z}}
    provider._process_packet(json.dumps(packet).encode())
    return np.asarray(provider.output_queue.get_nowait().quaternion)


def _ximu3_to_head(q_raw):
    pytest.importorskip("ximu3")
    from providers import ximu3_provider

    return qm.change_basis(q_raw, ximu3_provider._XIMU3_TO_HEAD_FRAME)


def _uniface_to_head(q_raw):
    from providers.uniface_provider import _transformation_matrix_to_head_quaternion

    matrix = np.eye(4)
    matrix[:3, :3] = rotation_from_quaternion(*q_raw)
    return _transformation_matrix_to_head_quaternion(matrix)


CONVERTERS = {"airpods": _airpods_to_head, "ximu3": _ximu3_to_head, "uniface": _uniface_to_head}


@pytest.mark.parametrize("provider", sorted(PHYSICAL_MOTIONS))
@pytest.mark.parametrize("motion", sorted(EXPECTED_INDEX))
def test_physical_motion_gives_same_euler_signs_for_every_provider(provider, motion):
    axis, sign = PHYSICAL_MOTIONS[provider][motion]
    q_head = CONVERTERS[provider](_axis_angle(axis, sign * ANGLE_DEG))
    angles = np.degrees(qm.to_euler_zyx(q_head))
    expected = np.zeros(3)
    expected[EXPECTED_INDEX[motion]] = ANGLE_DEG
    np.testing.assert_allclose(angles, expected, atol=1e-6)


def test_change_basis_rejects_reflections():
    with pytest.raises(ValueError):
        qm.change_basis(qm.IDENTITY_QUATERNION, np.diag([1.0, 1.0, -1.0]))
