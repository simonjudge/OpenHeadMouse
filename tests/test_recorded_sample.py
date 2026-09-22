"""Test that RecordedSample.quaternion property works correctly."""

import numpy as np
import pytest

from core.recorder import RecordedSample


def test_recorded_sample_quaternion_property():
    """RecordedSample should reconstruct quaternion array from components."""
    sample = RecordedSample(
        timestamp=1.0,
        quaternion_w=0.7071,
        quaternion_x=0.0,
        quaternion_y=0.7071,
        quaternion_z=0.0,
        yaw_deg=0.0,
        pitch_deg=45.0,
        roll_deg=0.0,
    )

    q = sample.quaternion
    assert isinstance(q, np.ndarray)
    assert len(q) == 4
    assert np.isclose(q[0], 0.7071)
    assert np.isclose(q[1], 0.0)
    assert np.isclose(q[2], 0.7071)
    assert np.isclose(q[3], 0.0)


def test_recorded_sample_quaternion_property_multiple_calls():
    """Multiple calls to .quaternion should return equivalent arrays."""
    sample = RecordedSample(
        timestamp=2.5,
        quaternion_w=1.0,
        quaternion_x=0.0,
        quaternion_y=0.0,
        quaternion_z=0.0,
        yaw_deg=10.0,
        pitch_deg=5.0,
        roll_deg=0.0,
    )

    q1 = sample.quaternion
    q2 = sample.quaternion
    assert np.allclose(q1, q2)
