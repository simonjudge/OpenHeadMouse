"""Tests for providers/base_provider.py and providers/mock_provider.py."""

import queue
import struct
import time
from types import SimpleNamespace

import numpy as np
import pytest

from providers.base_provider import BaseProvider, ProviderSample, ProviderState
from providers.mock_provider import MockProvider
from providers.xiao_ble_provider import XiaoBleProvider


def test_mock_provider_emits_unit_quaternions():
    provider = MockProvider(rate_hz=100.0)
    samples = []
    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while len(samples) < 20:
            samples.append(provider.output_queue.get(timeout=max(0.0, deadline - time.monotonic())))
    finally:
        provider.stop()

    assert len(samples) == 20
    for s in samples:
        assert isinstance(s, ProviderSample)
        assert np.isclose(np.linalg.norm(s.quaternion), 1.0, atol=1e-6)


@pytest.mark.parametrize("rate_hz", [50.0, 100.0])
def test_mock_provider_uses_configured_sample_period(monkeypatch, rate_hz):
    provider = MockProvider(rate_hz=rate_hz)
    now = 0.0
    periods = []

    def sleep(period):
        nonlocal now
        periods.append(period)
        now += period
        if len(periods) == 3:
            provider._stop_event.set()

    monkeypatch.setattr(
        "providers.mock_provider.time",
        SimpleNamespace(monotonic=lambda: now, sleep=sleep),
    )
    provider._run()
    assert periods == [1.0 / rate_hz] * 3
    assert [provider.output_queue.get_nowait().timestamp for _ in periods] == pytest.approx(
        [0.0, 1.0 / rate_hz, 2.0 / rate_hz]
    )


def test_mock_provider_state_transitions():
    provider = MockProvider(rate_hz=50.0)
    assert provider.state == ProviderState.STOPPED
    provider.start()
    time.sleep(0.05)
    assert provider.state == ProviderState.RUNNING
    provider.stop()
    assert provider.state == ProviderState.STOPPED


class _CountingProvider(BaseProvider):
    """Minimal concrete provider for exercising the base class's queue overflow behavior."""

    def _run(self):
        i = 0
        while not self._stop_event.is_set():
            self._emit(ProviderSample(timestamp=float(i), quaternion=np.array([1.0, 0.0, 0.0, 0.0])))
            i += 1
            if i >= 10:
                break


def test_base_provider_drops_oldest_on_queue_overflow():
    provider = _CountingProvider(queue_maxsize=3)
    provider.start()
    provider.stop(timeout=2.0)

    remaining = []
    while True:
        try:
            remaining.append(provider.output_queue.get_nowait())
        except queue.Empty:
            break

    assert len(remaining) == 3
    # oldest samples (timestamps 0..6) should have been dropped; last 3 (7,8,9) retained
    assert [s.timestamp for s in remaining] == [7.0, 8.0, 9.0]


def _xiao_packet(tick, accel=(0, 0, 1000), gyro=(0, 0, 0)):
    return struct.pack("<Ihhhhhh", tick, *accel, *gyro)


def test_xiao_ble_provider_parses_sensor_packet_and_emits_unit_quaternion():
    provider = XiaoBleProvider()
    sample = provider._process_packet(
        _xiao_packet(10_000, gyro=(0, 0, 9000)),
        received_at=5.0,
    )

    assert sample is not None
    assert sample.timestamp == pytest.approx(5.0)
    assert sample.raw_metadata["device"] == "xiao_ble"
    np.testing.assert_allclose(np.linalg.norm(sample.quaternion), 1.0, atol=1e-6)

    next_sample = provider._process_packet(
        _xiao_packet(20_000, gyro=(0, 0, 9000)),
        received_at=5.01,
    )
    assert next_sample is not None
    assert next_sample.quaternion[3] > 0
    assert provider.state == ProviderState.RUNNING


def test_xiao_ble_provider_unwraps_sensor_timestamp_rollover():
    provider = XiaoBleProvider()
    first = provider._process_packet(_xiao_packet(0xFFFFFF00), received_at=10.0)
    second = provider._process_packet(_xiao_packet(0x00002610), received_at=10.01)

    assert first is not None
    assert second is not None
    assert second.timestamp - first.timestamp == pytest.approx(0.01)


def test_xiao_ble_provider_applies_configured_device_to_head_rotation():
    provider = XiaoBleProvider(device_to_head=np.diag([1.0, -1.0, -1.0]))
    sample = provider._process_packet(
        _xiao_packet(10_000, accel=(-342, 0, 940)),
        received_at=5.0,
    )

    assert sample is not None
    assert sample.quaternion[2] < 0


def test_xiao_ble_provider_discards_duplicate_and_invalid_packets():
    provider = XiaoBleProvider()
    packet = _xiao_packet(1_000)
    assert provider._process_packet(packet, received_at=1.0) is not None
    assert provider._process_packet(packet, received_at=1.001) is None

    with pytest.raises(ValueError, match="expected 16 bytes"):
        provider._process_packet(b"\x00", received_at=1.002)
    with pytest.raises(ValueError, match="accelerometer vector"):
        provider._process_packet(_xiao_packet(2_000, accel=(0, 0, 0)), received_at=1.002)


def test_xiao_ble_provider_rejects_reflections_and_invalid_mounting_matrix():
    with pytest.raises(ValueError, match="proper 3x3 rotation matrix"):
        XiaoBleProvider(device_to_head=np.diag([1.0, 1.0, -1.0]))


def test_make_provider_builds_xiao_ble_from_provider_config():
    from main import make_provider

    provider = make_provider(
        "xiao_ble",
        {"providers": {"xiao_ble": {"device_name": "OpenHeadMouse-XIAO"}}},
    )

    assert isinstance(provider, XiaoBleProvider)
    assert provider.device_name == "OpenHeadMouse-XIAO"
