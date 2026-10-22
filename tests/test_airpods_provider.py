"""Tests for providers/airpods/airpods_provider.py using the mock UDP sender from test_udp.py."""

import time

import pytest

from providers.airpods.airpods_provider import AirPodsProvider
from tests.test_udp import MockUDPSender


@pytest.fixture
def listening_provider():
    provider = AirPodsProvider(udp_host="127.0.0.1", udp_port=0)
    provider.start()
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if provider._sock is not None and provider._sock.getsockname()[1] != 0:
                yield provider, provider._sock.getsockname()[1]
                return
            time.sleep(0.01)
        pytest.fail("AirPods mock listener did not bind within three seconds")
    finally:
        provider.stop()


def test_airpods_provider_receives_mock_packets(listening_provider):
    """End-to-end: MockUDPSender -> AirPodsProvider on an OS-assigned port."""
    provider, port = listening_provider
    sender = MockUDPSender(host="127.0.0.1", port=port)
    try:
        for i in range(5):
            sender.send(timestamp=float(i), quaternion=(1.0, 0.0, 0.0, 0.0))
        samples = [provider.output_queue.get(timeout=2.0) for _ in range(5)]
        assert [sample.timestamp for sample in samples] == list(map(float, range(5)))
        assert all(sample.quaternion is not None for sample in samples)
    finally:
        sender.close()


def test_airpods_provider_rejects_malformed_json():
    provider = AirPodsProvider()
    with pytest.raises(ValueError, match="Failed to parse AirPods UDP packet"):
        provider._process_packet(b"not json at all")
