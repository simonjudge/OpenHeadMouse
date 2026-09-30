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


def test_delivery_burst_does_not_inflate_velocity():
    """A pair of samples arriving microseconds apart must not create a spike."""
    samples = make_test_samples(20)
    for i in range(20):
        samples[i].timestamp = i * 0.05
    baseline = SessionStats(samples).angular_velocity_stats()["max"]

    samples[10].timestamp = samples[9].timestamp + 0.0002
    burst = SessionStats(samples).angular_velocity_stats()["max"]

    assert burst == pytest.approx(baseline, rel=0.5)
    assert SessionStats(samples).sampling_stats()["rejected_count"] == 1


def test_yaw_wrap_does_not_create_velocity_spike():
    samples = []
    for i, yaw_deg in enumerate([179.0, 179.5, -179.5, -179.0]):
        q = qm.from_euler_zyx(np.radians(yaw_deg), 0.0, 0.0)
        samples.append(RecordedSample(
            timestamp=i * 0.05,
            quaternion_w=q[0], quaternion_x=q[1], quaternion_y=q[2], quaternion_z=q[3],
            yaw_deg=yaw_deg, pitch_deg=0.0, roll_deg=0.0,
        ))

    assert SessionStats(samples).angular_velocity_stats()["max"] == pytest.approx(20.0, abs=1.0)


def test_times_fall_back_to_host_time_for_mixed_clocks():
    samples = make_test_samples(6)
    for i, sample in enumerate(samples):
        sample.host_time = 1000.0 + i * 0.05
    samples[3].timestamp = samples[3].timestamp + 2.0e9  # provider clock changed mid-file

    times = SessionStats(samples).times()

    assert np.all(np.diff(times) > 0)
    assert times[-1] - times[0] == pytest.approx(0.25)


def test_per_sample_series_align_with_samples():
    samples = make_test_samples(12)
    stats = SessionStats(samples)

    assert len(stats.angular_speed_per_sample()) == len(samples)
    assert len(stats.angular_acceleration_per_sample()) == len(samples)
    assert stats.angular_speed_per_sample().max() == pytest.approx(
        stats.angular_velocity_stats()["max"]
    )
