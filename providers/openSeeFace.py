"""OpenSeeFace UDP head-pose provider, without OpenSeeFace Python dependencies.

OpenSeeFace's facetracker (not this module) opens the webcam and sends its
standard binary packets to 127.0.0.1:11573 (the upstream default). Either run
it yourself, or set ``tracker_command`` (e.g. ``["C:/OpenSeeFace/Binary/
facetracker.exe"]``) so this provider launches it with ``-c <camera> -i <host>
-p <port>`` plus ``tracker_args`` and stops it again. All receiver/adapter
logic lives here; only the project's shared provider interface and quaternion
helpers are used. For a console-only pose check, run
``python -m providers.openSeeFace``.

Protocol reference: emilianavt/OpenSeeFace's facetracker.py and Unity/OpenSee.cs.
Each little-endian face record is 1785 bytes; multiple records may share a
datagram. The wire quaternion is XYZW and represents the INVERSE of the PnP
model-to-camera rotation (upstream tracker.py's matrix_to_quaternion).

PnP uses image (row, column), not (column, row). At a frontal neutral pose,
its rotation is a half-turn about (1, -1, 0). Remove that fixed alignment
after inverting the wire quaternion. The model's anatomical axes are
X right, Y up, Z forward, a reflected frame. For quaternion vector parts
the axial-vector mapping is det(M)*M, a proper rotation, giving
head_x = -model_z, head_y = model_x, head_z = -model_y.

Packet timestamps are the sender's time.time() taken after capture and before
inference, NOT receiver arrival times. They are retained unchanged. Duplicate
or backwards timestamps are discarded; restart this provider after a sender
clock reset. Missing/invalid tracking never generates synthetic pose samples.
"""

from __future__ import annotations

import argparse
import collections
import logging
import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

_LOGGER = logging.getLogger(__name__)
_FACE_RECORD_SIZE = 1785
_POSE_HEADER = struct.Struct("<di4fBf4f")
_CONFIDENCE_OFFSET = 73
_FRONTAL_ALIGNMENT = np.array([0.0, np.sqrt(0.5), -np.sqrt(0.5), 0.0])
_MODEL_AXIAL_TO_HEAD = np.array(
    [
        [0.0, 0.0, -1.0],
        [1.0, 0.0, 0.0],
        [0.0, -1.0, 0.0],
    ]
)


def _wire_quaternion_to_head(quaternion_xyzw: np.ndarray) -> np.ndarray:
    """Convert the upstream inverse-PnP quaternion to canonical FLU WXYZ."""
    raw = np.asarray(quaternion_xyzw, dtype=np.float64)
    if raw.shape != (4,) or not np.all(np.isfinite(raw)):
        raise ValueError("OpenSeeFace quaternion must contain four finite components")
    norm = float(np.linalg.norm(raw))
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError("OpenSeeFace quaternion must have nonzero finite length")
    raw = raw / norm
    model_to_camera = qm.conjugate(raw[[3, 0, 1, 2]])
    model_rotation = qm.multiply(qm.conjugate(_FRONTAL_ALIGNMENT), model_to_camera)
    return qm.change_basis(model_rotation, _MODEL_AXIAL_TO_HEAD)


class OpenSeeFaceProvider(BaseProvider):
    """Receive one selected face from an independently running OpenSeeFace tracker."""

    def __init__(
        self,
        udp_host: str = "127.0.0.1",
        udp_port: int = 11573,
        face_id: int = 0,
        tracking_lost_hold_ms: float = 500.0,
        queue_maxsize: int = 256,
        tracker_command: list[str] | str | None = None,
        camera: int | str = 0,
        tracker_args: list[str] | None = None,
    ):
        if isinstance(tracker_command, str):
            tracker_command = [tracker_command] if tracker_command else []
        tracker_command = list(tracker_command or [])
        tracker_args = ["--faces", "1"] if tracker_args is None else list(tracker_args)
        if not all(isinstance(part, str) and part for part in tracker_command):
            raise ValueError("tracker_command must be a list of nonempty strings")
        if not all(isinstance(part, str) for part in tracker_args):
            raise ValueError("tracker_args must be a list of strings")
        if not udp_host:
            raise ValueError("udp_host must not be empty")
        if not isinstance(udp_port, int) or not 0 <= udp_port <= 65535:
            raise ValueError("udp_port must be an integer between 0 and 65535")
        if not isinstance(face_id, int) or face_id < 0:
            raise ValueError("face_id must be a nonnegative integer")
        if not np.isfinite(tracking_lost_hold_ms) or tracking_lost_hold_ms < 0:
            raise ValueError("tracking_lost_hold_ms must be finite and nonnegative")
        if not isinstance(queue_maxsize, int) or queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be a positive integer")
        super().__init__(queue_maxsize=queue_maxsize)
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.face_id = face_id
        self.tracking_lost_hold_ms = tracking_lost_hold_ms
        self.tracker_command = tracker_command
        self.camera = camera
        self.tracker_args = tracker_args
        self._sock: socket.socket | None = None
        self._last_timestamp: float | None = None
        self._last_valid_monotonic: float | None = None
        self._packets_received = 0
        self._error_message = ""
        self._tracker: subprocess.Popen | None = None
        self._tracker_output: collections.deque[str] = collections.deque(maxlen=20)

    @property
    def status_text(self) -> str:
        """Human-readable status for the GUI, explaining why no pose is available."""
        state = self.state
        if state == ProviderState.ERROR:
            return f"error: {self._error_message or 'see log'}"
        if state == ProviderState.STOPPED:
            return "stopped"
        if state == ProviderState.RUNNING:
            return "tracking"
        if self._packets_received == 0:
            source = "facetracker" if self.tracker_command else "OpenSeeFace facetracker (start it separately)"
            return f"waiting for {source} on {self.udp_host}:{self.udp_port}"
        return f"tracking lost (no valid pose for face {self.face_id})"

    def _tracker_argv(self) -> list[str]:
        return [
            *self.tracker_command,
            "-c", str(self.camera),
            "-i", self.udp_host,
            "-p", str(self.udp_port),
            *self.tracker_args,
        ]

    def _start_tracker(self) -> None:
        argv = self._tracker_argv()
        _LOGGER.info("Launching OpenSeeFace tracker: %s", subprocess.list2cmdline(argv))
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._tracker_output.clear()
        self._tracker = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            creationflags=creationflags,
        )
        threading.Thread(
            target=self._pump_tracker_output, args=(self._tracker,), daemon=True,
            name="OpenSeeFaceTrackerOutput",
        ).start()

    def _pump_tracker_output(self, process: subprocess.Popen) -> None:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                self._tracker_output.append(line)
                _LOGGER.info("facetracker: %s", line)

    def _check_tracker(self) -> None:
        if self._tracker is None:
            return
        code = self._tracker.poll()
        if code is not None:
            time.sleep(0.2)  # let the output pump collect the final lines
            tail = " | ".join(list(self._tracker_output)[-3:])
            raise RuntimeError(
                f"OpenSeeFace tracker exited with code {code}" + (f": {tail}" if tail else "")
            )

    def _stop_tracker(self) -> None:
        process, self._tracker = self._tracker, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3.0)

    def _update_tracking_state(self) -> None:
        if (
            self._last_valid_monotonic is None
            or (time.monotonic() - self._last_valid_monotonic) * 1000.0
            >= self.tracking_lost_hold_ms
        ):
            self._state = ProviderState.TRACKING_LOST

    def _process_packet(self, data: bytes) -> None:
        """Validate selected face records and emit only valid, source-timed poses."""
        if not data or len(data) % _FACE_RECORD_SIZE:
            raise ValueError(
                f"OpenSeeFace datagram has {len(data)} bytes; expected a nonzero "
                f"multiple of {_FACE_RECORD_SIZE}"
            )
        samples = []
        for offset in range(0, len(data), _FACE_RECORD_SIZE):
            (
                timestamp, face_id, width, height, right_eye, left_eye,
                got_3d, fit_error, x, y, z, w,
            ) = _POSE_HEADER.unpack_from(data, offset)
            if face_id != self.face_id:
                continue
            if not np.all(np.isfinite([timestamp, width, height, right_eye, left_eye, fit_error])):
                raise ValueError("OpenSeeFace pose header contains non-finite values")
            if width <= 0 or height <= 0 or fit_error < 0 or got_3d not in (0, 1):
                raise ValueError("OpenSeeFace pose header contains invalid dimensions, fit error or 3D flag")
            if not got_3d:
                _LOGGER.debug("OpenSeeFace face %s has no valid 3D pose", face_id)
                continue
            quaternion = _wire_quaternion_to_head(np.array([x, y, z, w]))
            confidence = np.frombuffer(
                data, dtype="<f4", count=68, offset=offset + _CONFIDENCE_OFFSET
            )
            if not np.all(np.isfinite(confidence)):
                raise ValueError("OpenSeeFace landmark confidence contains non-finite values")
            samples.append(
                ProviderSample(
                    timestamp=timestamp,
                    quaternion=quaternion,
                    raw_metadata={
                        "source": "openseeface",
                        "face_id": face_id,
                        "camera_resolution": [width, height],
                        "right_eye_open": right_eye,
                        "left_eye_open": left_eye,
                        "fit_3d_error": fit_error,
                        "confidence": float(np.mean(confidence)),
                    },
                )
            )

        emitted = False
        for sample in samples:
            if self._last_timestamp is not None and sample.timestamp <= self._last_timestamp:
                if sample.timestamp < self._last_timestamp:
                    _LOGGER.warning(
                        "Discarding backwards OpenSeeFace timestamp %s (last %s); "
                        "restart the provider if the sender clock was reset",
                        sample.timestamp, self._last_timestamp,
                    )
                continue
            self._last_timestamp = sample.timestamp
            self._last_valid_monotonic = time.monotonic()
            self._state = ProviderState.RUNNING
            self._emit(sample)
            emitted = True
        if not emitted:
            self._update_tracking_state()

    def _run(self) -> None:
        self._last_timestamp = None
        self._last_valid_monotonic = None
        self._packets_received = 0
        self._error_message = ""
        self._state = ProviderState.TRACKING_LOST
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
                        f"cannot listen on UDP {self.udp_host}:{self.udp_port} "
                        f"(is another OpenHeadMouse or tracker client using it?): {exc}"
                    ) from exc
                sock.settimeout(0.05)
                self._sock = sock
                _LOGGER.info("Listening for OpenSeeFace on %s:%s", *sock.getsockname())
                if self.tracker_command:
                    self._start_tracker()
                while not self._stop_event.is_set():
                    self._check_tracker()
                    try:
                        data, _ = sock.recvfrom(65535)
                    except socket.timeout:
                        self._update_tracking_state()
                        continue
                    except ConnectionResetError:
                        # Windows reports ICMP port-unreachable on UDP sockets; ignore.
                        continue
                    self._packets_received += 1
                    try:
                        self._process_packet(data)
                    except ValueError as exc:
                        _LOGGER.warning("Discarding malformed OpenSeeFace packet: %s", exc)
                        self._update_tracking_state()
        except (OSError, RuntimeError) as exc:
            self._error_message = str(exc)
            _LOGGER.exception("OpenSeeFace provider failed")
            raise
        finally:
            self._sock = None
            self._stop_tracker()


def main(argv: list[str] | None = None) -> int:
    """Print live angles without starting the GUI or controlling the mouse."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11573)
    parser.add_argument("--face-id", type=int, default=0)
    parser.add_argument(
        "--tracker", nargs="+", default=None, metavar="ARG",
        help="launch facetracker, e.g. --tracker C:/OpenSeeFace/Binary/facetracker.exe",
    )
    parser.add_argument("--camera", default="0")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        provider = OpenSeeFaceProvider(
            udp_host=args.host, udp_port=args.port, face_id=args.face_id,
            tracker_command=args.tracker, camera=args.camera,
        )
    except ValueError as exc:
        parser.error(str(exc))
    provider.start()
    try:
        while provider.state != ProviderState.ERROR:
            try:
                sample = provider.output_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            yaw, pitch, roll = np.degrees(qm.to_euler_zyx(sample.quaternion))
            print(
                f"t={sample.timestamp:.6f} yaw={yaw:+7.1f} "
                f"pitch={pitch:+7.1f} roll={roll:+7.1f}",
                flush=True,
            )
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        provider.stop()


if __name__ == "__main__":
    raise SystemExit(main())
