"""Tests for core/session_stats.py."""

import numpy as np
import pytest

from core import quaternion_math as qm
from core.recorder import RecordedSample
from core.session_stats import SessionStats


def make_test_samples(n=10):
    """Generate n test samples with varying angles."""
    samples = []
    for i in range(n):
        yaw = np.radians(10 * np.sin(i * 0.5))
        pitch = np.radians(5 * np.cos(i * 0.5))
        roll = 0.0
        q = qm.from_euler_zyx(yaw, pitch, roll)
        sample = RecordedSample(
            timestamp=float(i) / 30.0,  # 30 Hz sampling
            quaternion_w=q[0],
            quaternion_x=q[1],
            quaternion_y=q[2],
            quaternion_z=q[3],
            yaw_deg=np.degrees(yaw),
            pitch_deg=np.degrees(pitch),
            roll_deg=0.0,
        )
        samples.append(sample)
    return samples


def test_session_stats_requires_at_least_2_samples():
    with pytest.raises(ValueError):
        SessionStats([make_test_samples(1)[0]])


def test_cervical_rom_min_max():
    samples = make_test_samples(10)
    stats = SessionStats(samples)
    rom = stats.cervical_rom()

    assert "yaw_min" in rom
    assert "yaw_max" in rom
    assert "pitch_min" in rom
    assert "pitch_max" in rom
    assert rom["yaw_range"] == rom["yaw_max"] - rom["yaw_min"]


def test_angular_velocity_stats():
    samples = make_test_samples(20)
    stats = SessionStats(samples)
    vel = stats.angular_velocity_stats()

    assert vel["max"] >= vel["min"] >= 0
    assert vel["mean"] >= 0


def test_angular_acceleration_stats():
    samples = make_test_samples(30)
    stats = SessionStats(samples)
    acc = stats.angular_acceleration_stats()

    assert acc["max"] >= acc["min"] >= 0
    assert acc["mean"] >= 0


def test_session_duration():
    samples = make_test_samples(10)
    stats = SessionStats(samples)
    duration = stats.session_duration()

    expected = samples[-1].timestamp - samples[0].timestamp
    assert np.isclose(duration, expected, rtol=1e-6)
