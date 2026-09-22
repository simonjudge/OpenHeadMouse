"""Tests for providers/airpods/airpods_provider.py using the mock UDP sender from test_udp.py."""

import queue
import time

import pytest

from providers.airpods.airpods_provider import AirPodsProvider
from tests.test_udp import MockUDPSender


def test_airpods_provider_receives_mock_packets():
    """End-to-end: MockUDPSender -> AirPodsProvider."""
    provider = AirPodsProvider(udp_host="127.0.0.1", udp_port=5005)
    provider.start()

    try:
        time.sleep(0.1)  # let the provider bind

        sender = MockUDPSender(host="127.0.0.1", port=5005)
        for i in range(5):
            sender.send(timestamp=float(i), quaternion=(1.0, 0.0, 0.0, 0.0))

        time.sleep(0.2)  # let UDP packets arrive
        sender.close()

        samples = []
        while True:
            try:
                samples.append(provider.output_queue.get_nowait())
            except queue.Empty:
                break

        assert len(samples) >= 3  # at least some packets got through (UDP is lossy)
        for s in samples:
            assert s.quaternion is not None
    finally:
        provider.stop()


def test_airpods_provider_malformed_json_ignored():
    """Provider continues running even if a packet is malformed."""
    provider = AirPodsProvider(udp_host="127.0.0.1", udp_port=5006)
    provider.start()

    try:
        time.sleep(0.1)

        sock = provider._sock if provider._sock else None
        if sock is None:
            # Manually send malformed packet for testing
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(b"not json at all", ("127.0.0.1", 5006))
            sock.close()

        time.sleep(0.1)
        # Provider should still be alive; no exception raised
        assert provider.state.value in ("running", "tracking_lost")
    finally:
        provider.stop()
