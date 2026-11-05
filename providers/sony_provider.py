"""Sony Head Tracker v2 JSON adapter with a managed native Bluetooth bridge.

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
import subprocess
import sys
import tempfile
import time
import zipfile

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState
from providers.sony_bridge import ensure_bridge

_LOGGER = logging.getLogger(__name__)
_SONY_TO_HEAD_FRAME = np.array([
    [0.0, 1.0, 0.0],
    [-1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
])


class SonyProvider(BaseProvider):
    """Receive scalar-first quaternions on the bridge's JSON port (default 4243).

    By default the pinned bridge is launched and stopped with the receiver.
    Set auto_start_bridge=False to use an independently managed bridge.
    """

    def __init__(
        self,
        udp_host: str = "127.0.0.1",
        udp_port: int = 4243,
        tracking_lost_hold_ms: float = 500.0,
        queue_maxsize: int = 256,
        auto_start_bridge: bool = True,
        startup_timeout_s: float = 15.0,
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
        if type(auto_start_bridge) is not bool:
            raise ValueError("auto_start_bridge must be a boolean")
        if (
            not isinstance(startup_timeout_s, (int, float))
            or isinstance(startup_timeout_s, bool)
            or not np.isfinite(startup_timeout_s)
            or startup_timeout_s <= 0
        ):
            raise ValueError("startup_timeout_s must be finite and positive")
        if auto_start_bridge and (udp_host != "127.0.0.1" or udp_port < 2):
            raise ValueError("The managed Sony bridge requires udp_host 127.0.0.1 and udp_port 2..65535")
        super().__init__(queue_maxsize=queue_maxsize)
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.tracking_lost_hold_ms = tracking_lost_hold_ms
        self.auto_start_bridge = auto_start_bridge
        self.startup_timeout_s = startup_timeout_s
        self._sock: socket.socket | None = None
        self._bridge_process: subprocess.Popen | None = None
        self._last_timestamp: float | None = None
        self._error_message = ""
        self._startup_status = ""

    @property
    def has_received_sample(self) -> bool:
        return self._last_timestamp is not None

    @property
    def status_text(self) -> str:
        if self.state == ProviderState.ERROR:
            return f"error: {self._error_message or 'see log'}"
        if self.state == ProviderState.STOPPED:
            return "stopped"
        if self.state == ProviderState.RUNNING:
            if self.has_received_sample:
                return "tracking (receive timestamps)"
        if self._startup_status and not self.has_received_sample:
            return self._startup_status
        return (
            f"waiting for Sony Head Tracker JSON on {self.udp_host}:{self.udp_port}; "
            "use XYZ axes, no inversions"
        )

    @property
    def connection_error(self) -> str:
        return self._error_message

    def _connection_help(self) -> str:
        return (
            "Pair and connect the headphones, and close other Sony Head Tracker instances. "
            "On Windows 11, if no sensor is found, open Sony Head Tracker and use "
            "'Repair Tracker' (administrator approval required), then close it and retry. "
            "On macOS, allow Input Monitoring for the bridge. "
            f"Check that v2 JSON reaches {self.udp_host}:{self.udp_port}."
        )

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise RuntimeError(
                    "Sony dependency setup is still finishing. Wait for it to finish, then press Stop again."
                )
        self._state = ProviderState.STOPPED

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
        self._startup_status = ""
        self._state = ProviderState.TRACKING_LOST
        # Do not let a restarted receiver replay samples from its previous run.
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, tempfile.TemporaryFile() as bridge_log:
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
                _LOGGER.info(
                    "Sony JSON has no native timestamps; using monotonic receive time."
                )
                if self.auto_start_bridge:
                    self._startup_status = "preparing Sony Head Tracker dependency..."
                    executable = ensure_bridge()
                    if self._stop_event.is_set():
                        return
                    self._startup_status = "connecting headphones through Sony Head Tracker..."
                    self._bridge_process = subprocess.Popen(
                        [str(executable), "bridge", "--port", str(self.udp_port - 1),
                         "--axis-map", "XYZ", "--invert", "none", "--smoothing", "1"],
                        stdout=bridge_log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                deadline = time.monotonic() + self.startup_timeout_s
                while not self._stop_event.is_set():
                    if self._bridge_process is not None and self._bridge_process.poll() is not None:
                        bridge_log.seek(0, 2)
                        bridge_log.seek(max(0, bridge_log.tell() - 4096))
                        detail = bridge_log.read().decode("utf-8", errors="replace").strip()
                        raise RuntimeError(
                            f"Sony Head Tracker exited (code {self._bridge_process.returncode}). "
                            f"{detail}\n\n{self._connection_help()}"
                        )
                    if not self.has_received_sample and time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"No valid Sony tracking data received within {self.startup_timeout_s:g} seconds. "
                            + self._connection_help()
                        )
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
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
            self._error_message = str(exc)
            self._state = ProviderState.ERROR
            _LOGGER.exception("Sony provider failed")
        finally:
            if self._bridge_process is not None:
                process = self._bridge_process
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)
                self._bridge_process = None
            self._sock = None
