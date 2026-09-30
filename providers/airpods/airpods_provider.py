"""AirPods provider: Python UDP receiver for the Swift CoreMotion helper.

The native Swift helper (in swift_src/) streams CMHeadphoneMotionManager
quaternions as JSON over UDP to 127.0.0.1:5005. This provider listens on
that socket and normalizes the stream into ProviderSample.

COORDINATE FRAME & ALIGNMENT:
=============================

Canonical head frame (right-handed, shared across all providers; see
COORDINATE_FRAMES.md):
  +X: Forward (out of the nose)
  +Y: Left (toward the left ear)
  +Z: Up (toward the top of the head)

CMHeadphoneMotionManager frame (as worn):
  +X: Right (toward the right ear)   - attitude.pitch (nod) is about this axis
  +Y: Forward (out of the nose)      - attitude.roll (tilt) is about this axis
  +Z: Up                             - attitude.yaw (turn) is about this axis

Conversion (a proper rotation, applied in :meth:`AirPodsProvider._process_packet`):
  head_x = +raw_y, head_y = -raw_x, head_z = +raw_z
  i.e. q_head = [w, y, -x, z].

Earlier versions swapped x/y without the sign change (a reflection), which
inverted pitch relative to the other providers.

Verify with ``python test_orientation.py --provider airpods --duration 20``:
turn head LEFT -> yaw POSITIVE, nod UP -> pitch POSITIVE, tilt LEFT ear down
-> roll POSITIVE.

Swift Helper & Normalization:
  The Swift helper (swift_src/main.swift) receives quaternions from
  CMHeadphoneMotionManager, normalizes them to unit length, and transmits
  them unchanged in [w, x, y, z] scalar-first format. The frame conversion
  happens here in Python.

Reference: CMHeadphoneMotionManager documentation (Apple Developer),
           COORDINATE_FRAMES.md (this project)

UDP JSON schema (shared with tests/test_udp.py):
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

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

# Rows are the canonical head axes (forward, left, up) in CMHeadphoneMotionManager coordinates.
_AIRPODS_TO_HEAD_FRAME = np.array(
    [
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
)


class AirPodsProvider(BaseProvider):
    """Receives head-pose quaternions from the native Swift CoreMotion helper via UDP."""

    def __init__(
        self,
        udp_host: str = "127.0.0.1",
        udp_port: int = 5005,
        queue_maxsize: int = 256,
    ):
        super().__init__(queue_maxsize=queue_maxsize)
        self.udp_host = udp_host
        self.udp_port = udp_port
        self._sock = None

    def _run(self) -> None:
        """Listen on UDP socket for JSON packets from the Swift helper until stop is requested."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.udp_host, self.udp_port))
            self._sock.settimeout(0.5)  # 500ms timeout so we can check stop_event regularly
            print(f"✓ Listening for AirPods on {self.udp_host}:{self.udp_port}")
            self._state = ProviderState.RUNNING

            packet_count = 0
            while not self._stop_event.is_set():
                try:
                    data, addr = self._sock.recvfrom(4096)
                    self._process_packet(data)
                    packet_count += 1
                    if packet_count % 30 == 0:
                        print(f"✓ AirPods: received {packet_count} packets from {addr}")
                except socket.timeout:
                    continue
                except Exception as e:
                    self._state = ProviderState.ERROR
                    print(f"✗ AirPods receiver error: {e}")
                    raise
        finally:
            if self._sock is not None:
                self._sock.close()
                print(f"AirPods listener closed (received {packet_count} total packets)")

    def _process_packet(self, data: bytes) -> None:
        """Parse a JSON UDP packet and emit a ProviderSample in the canonical head frame."""
        try:
            payload = json.loads(data.decode("utf-8"))
            timestamp = float(payload["timestamp"])
            q_dict = payload["quaternion"]
            q = [q_dict["w"], q_dict["x"], q_dict["y"], q_dict["z"]]
            source = payload.get("source", "airpods")

            q_transformed = qm.change_basis(q, _AIRPODS_TO_HEAD_FRAME)

            # Debug: print raw and transformed quaternions every 30 packets
            if not hasattr(self, '_packet_debug_count'):
                self._packet_debug_count = 0
            self._packet_debug_count += 1

            if self._packet_debug_count % 30 == 0:
                yaw, pitch, roll = np.degrees(qm.to_euler_zyx(q_transformed))
                print(f"[AirPods] q_raw=[{q[0]:.3f}, {q[1]:.3f}, {q[2]:.3f}, {q[3]:.3f}] → q_xfm=[{q_transformed[0]:.3f}, {q_transformed[1]:.3f}, {q_transformed[2]:.3f}, {q_transformed[3]:.3f}] → yaw={yaw:+7.1f}° pitch={pitch:+7.1f}° roll={roll:+7.1f}°")

            self._emit(
                ProviderSample(
                    timestamp=timestamp,
                    quaternion=q_transformed,
                    raw_metadata={"source": source},
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            raise ValueError(f"Failed to parse AirPods UDP packet: {e}")
