"""Tests for core.quaternion_math, validated against scipy.spatial.transform.Rotation.

Note: scipy's Rotation uses scalar-last [x, y, z, w] quaternions; this module
uses scalar-first [w, x, y, z]. Tests explicitly reorder when crossing the
boundary.
"""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from core import quaternion_math as qm

RNG = np.random.default_rng(seed=42)


def _wxyz_to_scipy(q_wxyz):
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w])


def _random_unit_quaternion():
    q = RNG.normal(size=4)
    return qm.normalize(q)


def test_normalize_produces_unit_length():
    for _ in range(20):
        q = RNG.normal(size=4) * RNG.uniform(0.1, 10)
        nq = qm.normalize(q)
        assert np.isclose(np.linalg.norm(nq), 1.0)


def test_normalize_batch():
    qs = RNG.normal(size=(10, 4))
    nqs = qm.normalize(qs)
    norms = np.linalg.norm(nqs, axis=-1)
    assert np.allclose(norms, 1.0)


def test_conjugate_and_inverse_equal_for_unit_quaternion():
    q = _random_unit_quaternion()
    assert np.allclose(qm.conjugate(q), qm.inverse(q))


def test_multiply_matches_scipy_composition():
    for _ in range(20):
        q1 = _random_unit_quaternion()
        q2 = _random_unit_quaternion()
        result = qm.multiply(q1, q2)

        r1 = _wxyz_to_scipy(q1)
        r2 = _wxyz_to_scipy(q2)
        expected_scipy = (r1 * r2).as_quat()  # [x, y, z, w]
        expected = np.array([expected_scipy[3], expected_scipy[0], expected_scipy[1], expected_scipy[2]])

        # quaternions have double cover (q == -q); compare up to sign
        assert np.allclose(result, expected, atol=1e-8) or np.allclose(result, -expected, atol=1e-8)


def test_neutral_relative_to_itself_is_identity():
    q_neutral = _random_unit_quaternion()
    q_rel = qm.relative_rotation(q_neutral, q_neutral)
    assert np.allclose(q_rel, qm.IDENTITY_QUATERNION, atol=1e-8) or np.allclose(
        q_rel, -qm.IDENTITY_QUATERNION, atol=1e-8
    )


def test_relative_rotation_matches_scipy_inverse_composition():
    for _ in range(20):
        q_neutral = _random_unit_quaternion()
        q_current = _random_unit_quaternion()
        result = qm.relative_rotation(q_neutral, q_current)

        r_neutral = _wxyz_to_scipy(q_neutral)
        r_current = _wxyz_to_scipy(q_current)
        expected_scipy = (r_neutral.inv() * r_current).as_quat()
        expected = np.array([expected_scipy[3], expected_scipy[0], expected_scipy[1], expected_scipy[2]])

        assert np.allclose(result, expected, atol=1e-8) or np.allclose(result, -expected, atol=1e-8)


@pytest.mark.parametrize(
    "yaw_deg,pitch_deg,roll_deg",
    [
        (0, 0, 0),
        (30, 0, 0),
        (0, 20, 0),
        (0, 0, 15),
        (45, -20, 10),
        (-60, 30, -25),
    ],
)
def test_euler_round_trip(yaw_deg, pitch_deg, roll_deg):
    yaw, pitch, roll = np.radians([yaw_deg, pitch_deg, roll_deg])
    q = qm.from_euler_zyx(yaw, pitch, roll)
    recovered = qm.to_euler_zyx(q)
    expected = np.array([yaw, pitch, roll])
    assert np.allclose(recovered, expected, atol=1e-6)


@pytest.mark.parametrize(
    "yaw_deg,pitch_deg,roll_deg",
    [
        (30, 0, 0),
        (0, 20, 0),
        (0, 0, 15),
        (45, -20, 10),
    ],
)
def test_euler_matches_scipy_zyx_convention(yaw_deg, pitch_deg, roll_deg):
    yaw, pitch, roll = np.radians([yaw_deg, pitch_deg, roll_deg])
    q = qm.from_euler_zyx(yaw, pitch, roll)

    r_scipy = _wxyz_to_scipy(q)
    # scipy's intrinsic "ZYX" uses the textbook rotation-about-+Y/+X sign
    # convention; this module deliberately negates pitch/roll (see
    # quaternion_math module docstring) so "pitch up"/"roll left-ear-down"
    # read as positive. Yaw is unaffected; pitch/roll are negated vs. scipy.
    yaw_s, pitch_s, roll_s = r_scipy.as_euler("ZYX", degrees=False)

    assert np.isclose(yaw, yaw_s, atol=1e-6)
    assert np.isclose(pitch, -pitch_s, atol=1e-6)
    assert np.isclose(roll, -roll_s, atol=1e-6)


def test_euler_boundary_near_gimbal_lock_does_not_nan():
    # pitch approaching +90 degrees (edge of / beyond typical cervical ROM)
    yaw, pitch, roll = np.radians([10, 89.9, 5])
    q = qm.from_euler_zyx(yaw, pitch, roll)
    result = qm.to_euler_zyx(q)
    assert not np.any(np.isnan(result))


def test_sign_convention_yaw_left_is_positive():
    # A small positive yaw should rotate the forward vector (+X) toward +Y (left).
    yaw = np.radians(10)
    q = qm.from_euler_zyx(yaw, 0.0, 0.0)
    r = _wxyz_to_scipy(q)
    forward = np.array([1.0, 0.0, 0.0])
    rotated = r.apply(forward)
    assert rotated[1] > 0  # moved toward +Y (left)


def test_sign_convention_pitch_up_is_positive():
    # A small positive pitch should rotate the forward vector (+X) toward +Z (up).
    pitch = np.radians(10)
    q = qm.from_euler_zyx(0.0, pitch, 0.0)
    r = _wxyz_to_scipy(q)
    forward = np.array([1.0, 0.0, 0.0])
    rotated = r.apply(forward)
    assert rotated[2] > 0  # moved toward +Z (up)


def test_sign_convention_roll_left_ear_down_is_positive():
    # A small positive roll should rotate the left axis (+Y) toward -Z (down).
    roll = np.radians(10)
    q = qm.from_euler_zyx(0.0, 0.0, roll)
    r = _wxyz_to_scipy(q)
    left = np.array([0.0, 1.0, 0.0])
    rotated = r.apply(left)
    assert rotated[2] < 0  # left ear moved down (toward -Z)
