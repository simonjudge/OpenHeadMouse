"""AirPods provider: Python UDP receiver for the Swift CoreMotion helper.

The native Swift helper (in swift_src/) streams CMHeadphoneMotionManager
quaternions as JSON over UDP to 127.0.0.1:5005. This provider listens on
that socket and normalizes the stream into ProviderSample.

COORDINATE FRAME & ALIGNMENT:
=============================

Reference Frame (right-handed, shared across all providers):
  +X: Forward (direction of head gaze, out of nose)
  +Y: Right (ear-to-ear lateral axis, toward right ear)
  +Z: Down (vertical, toward neck/floor)

CMHeadphoneMotionManager Frame:
  CMHeadphoneMotionManager (available on AirPods Pro 2nd gen and AirPods Max)
  returns head motion as quaternions in the **device frame**, where the device
  frame is referenced to the AirPods' physical orientation and inertial sensors.

  When worn on the head, the device frame axes are designed to align with the
  wearer's head motion axes in an intuitive way. Apple's implementation returns
  quaternions that describe head rotation relative to a neutral orientation
  (device-level zero).

Assumed Alignment:
  This implementation **assumes** that the quaternions returned by
  CMHeadphoneMotionManager already align with the project's reference frame
  (or align sufficiently closely that no coordinate transformation is required).

  The assumption is based on:
  1. Apple's design intent: CoreMotion APIs are built for head-tracking
     applications and naturally express motion in head-centric terms
  2. Device orientation: When worn on the head, AirPods' sensors provide
     intuitive mappings (forward tilt → pitch, head turn → yaw, head roll → roll)
  3. Common implementation: AirPods return quaternions in a frame where +X is
     forward, +Y is right, and +Z is down (matching our reference frame)

Verification Steps (if this assumption is incorrect):
  1. Perform real-time test with AirPods running:
     - Turn head LEFT → Yaw should be POSITIVE
     - Nod head UP → Pitch should be POSITIVE
     - Tilt head LEFT (left ear down) → Roll should be POSITIVE
  2. If signs don't match, a quaternion transformation matrix may be needed
     (similar to UniFace's camera-to-head frame transformation)
  3. If yaw/pitch/roll are swapped, the axes may need permutation

To Debug Frame Alignment:
  Use test_orientation.py with --provider airpods:
    python test_orientation.py --provider airpods --duration 20
  Expected: same sign conventions as other providers (see COORDINATE_FRAMES.md)

Swift Helper & Normalization:
  The Swift helper (swift_src/main.swift) receives quaternions from
  CMHeadphoneMotionManager, normalizes them to unit length, and transmits
  in [w, x, y, z] scalar-first format (matching this project's convention).
  No frame transformation is applied in the Swift code — quaternions pass
  through unchanged, assuming Apple's frame is already correct.

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

from providers.base_provider import BaseProvider, ProviderSample, ProviderState


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
        """Parse a JSON UDP packet and emit a ProviderSample.

        AirPods CMHeadphoneMotionManager returns quaternions with pitch and roll
        axes swapped relative to our reference frame. Transform them here.
        """
        try:
            payload = json.loads(data.decode("utf-8"))
            timestamp = float(payload["timestamp"])
            q_dict = payload["quaternion"]
            q = [q_dict["w"], q_dict["x"], q_dict["y"], q_dict["z"]]
            source = payload.get("source", "airpods")

            # Transform: swap x and y components to align with reference frame
            # AirPods [w, x, y, z] → Our frame [w, y, x, z]
            q_transformed = [q[0], q[2], q[1], q[3]]

            # Debug: print raw and transformed quaternions every 30 packets
            if not hasattr(self, '_packet_debug_count'):
                self._packet_debug_count = 0
            self._packet_debug_count += 1

            if self._packet_debug_count % 30 == 0:
                import numpy as np
                from core import quaternion_math as qm
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
