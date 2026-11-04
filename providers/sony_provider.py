"""Sony Head Tracker v2 JSON adapter, separate from the native Bluetooth bridge.

Run the upstream bridge with ``--axis-map XYZ --invert none``. Its unremapped
Android headphone axes are X right, Y forward, Z up; the conversion here is
the proper rotation to OpenHeadMouse's canonical FLU frame.

Upstream's JSON has no capture timestamp. This provider explicitly uses the
host monotonic clock immediately after recvfrom, not a fabricated device clock.
See README.md for setup and the resulting velocity-timing limitation.
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import time

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

_LOGGER = logging.getLogger(__name__)
_SONY_TO_HEAD_FRAME = np.array([
    [0.0, 1.0, 0.0],
    [-1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
])


class SonyProvider(BaseProvider):
    """Receive scalar-first quaternions on the bridge's JSON port (default 4243).

    The bridge is installed and run separately; stopping this provider only
    closes its receiver. No Sony executable, driver or Bluetooth library is
    downloaded, imported or launched.
    """

    def __init__(
        self,
        udp_host: str = "127.0.0.1",
        udp_port: int = 4243,
        tracking_lost_hold_ms: float = 500.0,
        queue_maxsize: int = 256,
    ):
        if not isinstance(udp_host, str) or not udp_host:
            raise ValueError("udp_host must not be empty")
        if type(udp_port) is not int or not 0 <= udp_port <= 65535:
            raise ValueError("udp_port must be an integer between 0 and 65535")
        if (
            not isinstance(tracking_lost_hold_ms, (int, float))
            or not np.isfinite(tracking_lost_hold_ms)
            or tracking_lost_hold_ms <= 0
        ):
            raise ValueError("tracking_lost_hold_ms must be finite and positive")
        if type(queue_maxsize) is not int or queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be a positive integer")
        super().__init__(queue_maxsize=queue_maxsize)
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.tracking_lost_hold_ms = tracking_lost_hold_ms
        self._sock: socket.socket | None = None
        self._last_timestamp: float | None = None
        self._error_message = ""

    @property
    def status_text(self) -> str:
        if self.state == ProviderState.ERROR:
            return f"error: {self._error_message or 'see log'}"
        if self.state == ProviderState.STOPPED:
            return "stopped"
        if self.state == ProviderState.RUNNING:
            return "tracking (receive timestamps)"
        return (
            f"waiting for Sony Head Tracker JSON on {self.udp_host}:{self.udp_port}; "
            "use XYZ axes, no inversions"
        )

    def _update_tracking_state(self) -> None:
        if (
            self._last_timestamp is None
            or (time.monotonic() - self._last_timestamp) * 1000.0
            >= self.tracking_lost_hold_ms
        ):
            self._state = ProviderState.TRACKING_LOST

    def _process_packet(self, data: bytes, received_at: float | None = None) -> None:
        """Validate a v2 JSON datagram and emit one canonical ProviderSample."""
        timestamp = time.monotonic() if received_at is None else received_at
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid Sony JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Sony JSON must be an object")
        if type(payload.get("version")) is not int or payload["version"] != 2:
            raise ValueError("unsupported Sony JSON version (expected 2)")
        raw_q = payload.get("quaternion")
        if (
            not isinstance(raw_q, list)
            or len(raw_q) != 4
            or any(type(value) not in (int, float) for value in raw_q)
        ):
            raise ValueError("Sony quaternion must contain four numbers [w, x, y, z]")
        try:
            q = np.asarray(raw_q, dtype=np.float64)
        except (OverflowError, ValueError) as exc:
            raise ValueError("Sony quaternion is outside the numeric range") from exc
        if not np.all(np.isfinite(q)) or np.max(np.abs(q)) == 0:
            raise ValueError("Sony quaternion must be finite and nonzero")
        # Scale first so large, finite input cannot overflow during normalization.
        q = qm.change_basis(q / np.max(np.abs(q)), _SONY_TO_HEAD_FRAME)
        if not np.isfinite(timestamp):
            raise ValueError("Sony receive timestamp must be finite")
        if self._last_timestamp is not None and timestamp <= self._last_timestamp:
            _LOGGER.warning("Discarding non-increasing Sony receive timestamp %s", timestamp)
            return
        device = payload.get("device")
        if device is not None and not isinstance(device, str):
            raise ValueError("Sony device must be a string or null")
        self._last_timestamp = timestamp
        self._state = ProviderState.RUNNING
        self._emit(ProviderSample(
            timestamp=timestamp,
            quaternion=q,
            raw_metadata={
                "source": "sony",
                "device": device,
                "bridge_version": payload["version"],
                "timestamp_source": "host_monotonic_receive",
            },
        ))

    def _run(self) -> None:
        self._last_timestamp = None
        self._error_message = ""
        self._state = ProviderState.TRACKING_LOST
        # Do not let a restarted receiver replay samples from its previous run.
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                try:
                    sock.bind((self.udp_host, self.udp_port))
                except OSError as exc:
                    raise OSError(
                        f"cannot listen for Sony JSON on {self.udp_host}:{self.udp_port} "
                        f"(is another client using it?): {exc}"
                    ) from exc
                sock.settimeout(min(0.05, self.tracking_lost_hold_ms / 1000.0))
                self._sock = sock
                _LOGGER.info("Listening for Sony Head Tracker JSON on %s:%s", *sock.getsockname())
                _LOGGER.warning(
                    "Sony JSON has no native timestamps; using monotonic receive time. "
                    "Configure the bridge with --axis-map XYZ --invert none."
                )
                while not self._stop_event.is_set():
                    try:
                        data, _ = sock.recvfrom(65535)
                        received_at = time.monotonic()
                    except socket.timeout:
                        self._update_tracking_state()
                        continue
                    try:
                        self._process_packet(data, received_at)
                    except ValueError as exc:
                        _LOGGER.warning("Discarding malformed Sony packet: %s", exc)
                        self._update_tracking_state()
        except OSError as exc:
            self._error_message = str(exc)
            _LOGGER.exception("Sony provider failed")
            raise
        finally:
            self._sock = None
