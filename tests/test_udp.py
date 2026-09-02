"""Mock UDP sender for debugging/testing provider streams.

Emits the same JSON schema the AirPods Swift helper will use (see
providers/airpods/airpods_provider.py, built in a later milestone), so the
UDP receive/parse path can be developed and tested without the native
helper or real hardware.

Shared AirPods UDP JSON schema (one packet per sample):
    {
        "timestamp": <float>,       # seconds, CMHeadphoneMotionManager's native clock
        "quaternion": {"w": <float>, "x": <float>, "y": <float>, "z": <float>},
        "source": "airpods"
    }
"""

from __future__ import annotations

import json
import socket
import time


def build_packet(timestamp: float, quaternion, source: str = "airpods") -> bytes:
    w, x, y, z = (float(c) for c in quaternion)
    payload = {
        "timestamp": timestamp,
        "quaternion": {"w": w, "x": x, "y": y, "z": z},
        "source": source,
    }
    return json.dumps(payload).encode("utf-8")


class MockUDPSender:
    """Sends synthetic AirPods-schema JSON packets to a UDP endpoint."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5005):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, timestamp: float, quaternion) -> None:
        packet = build_packet(timestamp, quaternion)
        self._sock.sendto(packet, (self.host, self.port))

    def close(self) -> None:
        self._sock.close()


def test_build_packet_round_trips_through_json():
    packet = build_packet(1.5, (1.0, 0.0, 0.0, 0.0))
    decoded = json.loads(packet.decode("utf-8"))
    assert decoded["timestamp"] == 1.5
    assert decoded["quaternion"] == {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}
    assert decoded["source"] == "airpods"


def test_mock_sender_delivers_n_packets_in_order():
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))  # let OS choose a free port
    recv_sock.settimeout(2.0)
    port = recv_sock.getsockname()[1]

    sender = MockUDPSender(host="127.0.0.1", port=port)
    n = 20
    try:
        for i in range(n):
            sender.send(timestamp=float(i), quaternion=(1.0, 0.0, 0.0, 0.0))

        received = []
        for _ in range(n):
            data, _ = recv_sock.recvfrom(4096)
            received.append(json.loads(data.decode("utf-8")))

        assert len(received) == n
        assert [pkt["timestamp"] for pkt in received] == list(map(float, range(n)))
    finally:
        sender.close()
        recv_sock.close()
