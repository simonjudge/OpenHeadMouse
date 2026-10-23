"""OpenSeeFace head-pose provider, without OpenSeeFace Python dependencies.

Two sources are supported:

``source="camera"`` (default) runs OpenSeeFace's head-pose pipeline in-process:
OpenCV captures the webcam, OpenSeeFace's MobileNetV3 face detector and 66-point
landmark model run through ONNX Runtime, and its 3D face model is fitted with
solvePnP. Nothing has to be installed or started separately. The pinned ONNX
models are downloaded on first use into ``model_dir``, and their SHA-256 hashes
are verified on every start. Only head pose is computed. Gaze, blink and
expression features are not.

``source="udp"`` instead receives the standard binary packets from an
independently run OpenSeeFace facetracker (default 127.0.0.1:11573).

For a console-only pose check, run ``python -m providers.openSeeFace``.

Ported from emilianavt/OpenSeeFace (BSD 2-Clause, Copyright (c) 2019,
Emiliana). See THIRD_PARTY_NOTICES.md. Protocol and pipeline reference:
facetracker.py, tracker.py and Unity/OpenSee.cs.

Each little-endian UDP face record is 1785 bytes, and multiple records may share
a datagram. The wire quaternion is XYZW and represents the INVERSE of the PnP
model-to-camera rotation (upstream tracker.py's matrix_to_quaternion). The
camera source produces the same quaternion and uses the same conversion.

PnP uses image (row, column), not (column, row). At a frontal neutral pose,
its rotation is a half-turn about (1, -1, 0). Remove that fixed alignment
after inverting the wire quaternion. The model's anatomical axes are
X right, Y up, Z forward, a reflected frame. For quaternion vector parts
the axial-vector mapping is det(M)*M, a proper rotation, giving
head_x = -model_z, head_y = model_x, head_z = -model_y.

UDP packet timestamps are the sender's time.time() taken after capture and
before inference, NOT receiver arrival times, and they are retained unchanged.
Camera samples use time.monotonic() taken immediately after each frame is read.
Duplicate or backwards timestamps are discarded. Restart this provider after a
sender clock reset. Missing or invalid tracking never generates synthetic pose
samples.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import socket
import struct
import time
from pathlib import Path

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

SOURCES = ("camera", "udp")
_MODEL_COMMIT = "50c09741897010faad721a218b2bbe14ecad6251"
_MODEL_URL = "https://raw.githubusercontent.com/emilianavt/OpenSeeFace/{commit}/models/{name}"
_DETECTION_MODEL = "mnv3_detection_opt.onnx"
BUNDLED_MODEL = 3
MODEL_SHA256 = {
    _DETECTION_MODEL: "0e8e4806766d85ab067a52c7af0dcb59eb7f9dfe580b44f20a8e6ab712d89809",
    "lm_model0_opt.onnx": "43c9c58f03701cc15b8423c49b78af348a6397334d348887e0fda59013275c29",
    "lm_model1_opt.onnx": "5bec42b298a24142cdb249a7256d65bc3fc0fbc673fa1752a64f4d7164719c9f",
    "lm_model2_opt.onnx": "16b33ba7d854a0643875ab3da3a620b4b650f2a7f032dd6f634f881cac108304",
    "lm_model3_opt.onnx": "5aa0ab2594acaf2e1d3916a8de592cffeb1f1a7a7f778cad2f37948bff6549d1",
    "lm_model4_opt.onnx": "2aae9c99a700f756e71781f114ebeac87cdf635f9275a71df80456388c29ceef",
}
# Upstream's 3D face model rows for the landmarks it fits with PnP
# (jaw corners, chin, nose bridge and nostrils). The nose tip (30) is the origin.
_CONTOUR_LANDMARKS = [0, 1, 8, 15, 16, 27, 28, 29, 30, 31, 32, 33, 34, 35]
_CONTOUR_MODEL = np.array([
    [0.4551769692672, 0.300895790030204, -0.764429433974752],
    [0.448998827123556, 0.166995837790733, -0.765143004071253],
    [0.0, -0.621079019321682, -0.287294770748887],
    [-0.448998827123556, 0.166995837790733, -0.765143004071253],
    [-0.4551769692672, 0.300895790030204, -0.764429433974752],
    [0.0, 0.293332603215811, -0.137582088779393],
    [0.0, 0.194828701837823, -0.069158109325951],
    [0.0, 0.103844017393155, -0.009151819844964],
    [0.0, 0.0, 0.0],
    [0.080626352317973, -0.041276068128093, -0.134161035564826],
    [0.046439347377934, -0.057675223874769, -0.102990627164664],
    [0.0, -0.068753126205604, -0.090545348482397],
    [-0.046439347377934, -0.057675223874769, -0.102990627164664],
    [-0.080626352317973, -0.041276068128093, -0.134161035564826],
], dtype=np.float64)
_JAW_WIDTH = 2 * 0.4551769692672
_NOSE_TIP = 30
_LANDMARK_RES = 224
_HEATMAP_RES = 28


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


def _matrix_to_wire_quaternion(m: np.ndarray) -> np.ndarray:
    """Upstream tracker.py matrix_to_quaternion: XYZW of the inverse rotation."""
    if m[2, 2] < 0:
        if m[0, 0] > m[1, 1]:
            t = 1 + m[0, 0] - m[1, 1] - m[2, 2]
            q = [t, m[0, 1] + m[1, 0], m[2, 0] + m[0, 2], m[1, 2] - m[2, 1]]
        else:
            t = 1 - m[0, 0] + m[1, 1] - m[2, 2]
            q = [m[0, 1] + m[1, 0], t, m[1, 2] + m[2, 1], m[2, 0] - m[0, 2]]
    elif m[0, 0] < -m[1, 1]:
        t = 1 - m[0, 0] - m[1, 1] + m[2, 2]
        q = [m[2, 0] + m[0, 2], m[1, 2] + m[2, 1], t, m[0, 1] - m[1, 0]]
    else:
        t = 1 + m[0, 0] + m[1, 1] + m[2, 2]
        q = [m[1, 2] - m[2, 1], m[2, 0] - m[0, 2], m[0, 1] - m[1, 0], t]
    return np.array(q, dtype=np.float64) * 0.5 / np.sqrt(t)


def _resolve_model_dir(model_dir: str | os.PathLike) -> Path:
    path = Path(model_dir)
    return path if path.is_absolute() else Path(__file__).resolve().parent.parent / path


def ensure_models(model_dir: str | os.PathLike, model: int) -> tuple[Path, Path]:
    """Return verified (detection, landmark) model paths, downloading if missing."""
    from core.model_assets import download_verified

    directory = _resolve_model_dir(model_dir)
    paths = []
    for name in (_DETECTION_MODEL, f"lm_model{model}_opt.onnx"):
        path = directory / name
        if not path.is_file():
            _LOGGER.info("Downloading OpenSeeFace model %s to %s", name, directory)
        url = _MODEL_URL.format(commit=_MODEL_COMMIT, name=name)
        paths.append(download_verified(url, path, MODEL_SHA256[name]))
    return paths[0], paths[1]


class _CameraPoseEstimator:
    """Single-face port of OpenSeeFace's detection, landmark and PnP stages."""

    def __init__(
        self,
        detection_model: Path,
        landmark_model: Path,
        detection_threshold: float = 0.6,
        landmark_threshold: float = 0.6,
        threads: int = 2,
    ):
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = threads
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self._detector = onnxruntime.InferenceSession(
            str(detection_model), sess_options=options, providers=providers
        )
        self._landmarker = onnxruntime.InferenceSession(
            str(landmark_model), sess_options=options, providers=providers
        )
        self._detector_input = self._detector.get_inputs()[0].name
        self._landmarker_input = self._landmarker.get_inputs()[0].name
        self.detection_threshold = detection_threshold
        self.landmark_threshold = landmark_threshold
        mean = np.array([0.485, 0.456, 0.406], np.float32)
        std = np.array([0.229, 0.224, 0.225], np.float32)
        self._offset = -mean / std
        self._scale = 1.0 / (std * 255.0)
        self.reset()

    def reset(self) -> None:
        self._box: tuple[float, float, float, float] | None = None
        self._rvec: np.ndarray | None = None
        self._tvec: np.ndarray | None = None

    def _tensor(self, rgb: np.ndarray, size: int) -> np.ndarray:
        import cv2

        image = cv2.resize(rgb.astype(np.float32), (size, size), interpolation=cv2.INTER_LINEAR)
        image = image * self._scale + self._offset
        return np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32)

    def detect(self, frame: np.ndarray) -> tuple[float, float, float, float] | None:
        """Most confident face box (x, y, w, h) in pixels, or None."""
        heatmap, maxpool = self._detector.run(
            None, {self._detector_input: self._tensor(frame[:, :, ::-1], 224)}
        )
        peaks = np.where(heatmap[0, 0] == maxpool[0, 0], heatmap[0, 0], 0.0)
        y, x = np.unravel_index(int(np.argmax(peaks)), peaks.shape)
        if peaks[y, x] < self.detection_threshold:
            return None
        radius = float(heatmap[0, 1, y, x]) * 112.0
        sx, sy = frame.shape[1] / 224.0, frame.shape[0] / 224.0
        return ((4 * x - radius) * sx, (4 * y - radius) * sy, 2 * radius * sx, 2 * radius * sy)

    def landmarks(self, frame: np.ndarray, box) -> tuple[float, np.ndarray] | None:
        """Average confidence and 66 (row, column, confidence) landmarks, or None."""
        height, width = frame.shape[:2]
        x, y, w, h = box
        x1 = int(np.clip(x - int(w * 0.1), 0, width - 1))
        y1 = int(np.clip(y - int(h * 0.125), 0, height - 1)) + 1
        x2 = int(np.clip(x + w + int(w * 0.1), 0, width - 1))
        y2 = int(np.clip(y + h + int(h * 0.125), 0, height - 1)) + 1
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        crop = self._tensor(frame[y1:y2, x1:x2, ::-1], _LANDMARK_RES)
        output = self._landmarker.run(None, {self._landmarker_input: crop})[0][0]
        cells = _HEATMAP_RES * _HEATMAP_RES
        heat = output[0:66].reshape(66, cells)
        best = heat.argmax(1)
        rows = np.arange(66)
        confidence = heat[rows, best]
        offsets = np.clip(output[66:198].reshape(2, 66, cells)[:, rows, best], 1e-7, 1 - 1e-7)
        res = _LANDMARK_RES - 1
        offsets = res * np.log(offsets / (1 - offsets)) / 16.0
        grid = res / (_HEATMAP_RES - 1)
        lms = np.stack([
            y1 + (y2 - y1) / _LANDMARK_RES * (grid * (best // _HEATMAP_RES) + offsets[0]),
            x1 + (x2 - x1) / _LANDMARK_RES * (grid * (best % _HEATMAP_RES) + offsets[1]),
            confidence,
        ], 1)
        lms[~np.isfinite(lms).all(axis=1)] = 0.0
        return float(np.mean(confidence)), lms

    def solve_pose(self, lms: np.ndarray, frame_shape) -> tuple[np.ndarray, float] | None:
        """Fit the 3D model to (row, column) landmarks; return wire XYZW and RMS error."""
        import cv2

        height, width = frame_shape[:2]
        camera = np.array([[width, 0, height / 2], [0, width, width / 2], [0, 0, 1]], np.float64)
        image_points = np.ascontiguousarray(lms[_CONTOUR_LANDMARKS, 0:2], dtype=np.float64)
        if self._rvec is None:
            jaw = float(np.linalg.norm(lms[16, 0:2] - lms[0, 0:2]))
            depth = width * _JAW_WIDTH / max(jaw, 1.0)
            nose = lms[_NOSE_TIP, 0:2]
            self._rvec = np.pi * np.array([[np.sqrt(0.5)], [-np.sqrt(0.5)], [0.0]])
            self._tvec = np.array([
                [(nose[0] - camera[0, 2]) * depth / width],
                [(nose[1] - camera[1, 2]) * depth / width],
                [depth],
            ])
        ok, rvec, tvec = cv2.solvePnP(
            _CONTOUR_MODEL, image_points, camera, None,
            rvec=self._rvec.copy(), tvec=self._tvec.copy(),
            useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok or not (np.all(np.isfinite(rvec)) and np.all(np.isfinite(tvec))) or tvec[2, 0] <= 0:
            self._rvec = self._tvec = None
            return None
        self._rvec, self._tvec = rvec, tvec
        projected = cv2.projectPoints(_CONTOUR_MODEL, rvec, tvec, camera, None)[0].reshape(-1, 2)
        error = float(np.sqrt(np.mean(np.sum((projected - image_points) ** 2, axis=1))))
        return _matrix_to_wire_quaternion(cv2.Rodrigues(rvec)[0]), error

    def process(self, frame: np.ndarray) -> dict | None:
        """Track one face in a BGR frame; return pose data or None when not found."""
        box = self._box or self.detect(frame)
        result = self.landmarks(frame, box) if box is not None else None
        if result is None or result[0] < self.landmark_threshold:
            self.reset()
            return None
        confidence, lms = result
        rows, cols = lms[:, 0], lms[:, 1]
        self._box = (cols.min(), rows.min(), cols.max() - cols.min(), rows.max() - rows.min())
        pose = self.solve_pose(lms, frame.shape)
        if pose is None:
            self.reset()
            return None
        wire, error = pose
        return {"wire_quaternion": wire, "confidence": confidence, "fit_error": error}


class OpenSeeFaceProvider(BaseProvider):
    """OpenSeeFace head pose from the webcam (in-process) or a UDP tracker."""

    def __init__(
        self,
        source: str = "camera",
        udp_host: str = "127.0.0.1",
        udp_port: int = 11573,
        face_id: int = 0,
        tracking_lost_hold_ms: float = 500.0,
        queue_maxsize: int = 256,
        camera_index: int = 0,
        target_fps: float = 30.0,
        frame_width: int = 640,
        frame_height: int = 480,
        model: int = BUNDLED_MODEL,
        model_dir: str = "models/openseeface",
        detection_threshold: float = 0.6,
        landmark_threshold: float = 0.6,
        threads: int = 2,
    ):
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
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
        if not isinstance(camera_index, int) or camera_index < 0:
            raise ValueError("camera_index must be a nonnegative integer")
        if not np.isfinite(target_fps) or target_fps < 0:
            raise ValueError("target_fps must be finite and nonnegative")
        for name, value in (("frame_width", frame_width), ("frame_height", frame_height),
                            ("threads", threads)):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if model not in range(5):
            raise ValueError("model must be an integer from 0 (fastest) to 4")
        for name, value in (("detection_threshold", detection_threshold),
                            ("landmark_threshold", landmark_threshold)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        super().__init__(queue_maxsize=queue_maxsize)
        self.source = source
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.face_id = face_id
        self.tracking_lost_hold_ms = tracking_lost_hold_ms
        self.camera_index = camera_index
        self.target_fps = target_fps
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.model = model
        self.model_dir = model_dir
        self.detection_threshold = detection_threshold
        self.landmark_threshold = landmark_threshold
        self.threads = threads
        self._sock: socket.socket | None = None
        self._last_timestamp: float | None = None
        self._last_valid_monotonic: float | None = None
        self._packets_received = 0
        self._error_message = ""
        self._phase = ""

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._phase = "starting"
        super().start()

    @property
    def status_text(self) -> str:
        """Human-readable status for the GUI, explaining why no pose is available."""
        state = self.state
        if state == ProviderState.ERROR:
            return f"error: {self._error_message or 'see log'}"
        if state == ProviderState.STOPPED:
            return "stopped"
        if self._phase:
            return self._phase
        if state == ProviderState.RUNNING:
            return "tracking"
        if self.source == "camera":
            return f"no face detected (camera {self.camera_index})"
        if self._packets_received == 0:
            return (
                f"waiting for OpenSeeFace facetracker on {self.udp_host}:{self.udp_port}"
            )
        return f"tracking lost (no valid pose for face {self.face_id})"

    def _update_tracking_state(self) -> None:
        if (
            self._last_valid_monotonic is None
            or (time.monotonic() - self._last_valid_monotonic) * 1000.0
            >= self.tracking_lost_hold_ms
        ):
            self._state = ProviderState.TRACKING_LOST

    def _emit_valid(self, sample: ProviderSample) -> bool:
        if self._last_timestamp is not None and sample.timestamp <= self._last_timestamp:
            if sample.timestamp < self._last_timestamp:
                _LOGGER.warning(
                    "Discarding backwards OpenSeeFace timestamp %s (last %s); "
                    "restart the provider if the sender clock was reset",
                    sample.timestamp, self._last_timestamp,
                )
            return False
        self._last_timestamp = sample.timestamp
        self._last_valid_monotonic = time.monotonic()
        self._state = ProviderState.RUNNING
        self._emit(sample)
        return True

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
            emitted = self._emit_valid(sample) or emitted
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
            if self.source == "camera":
                self._run_camera()
            else:
                self._run_udp()
        except Exception as exc:
            self._error_message = str(exc)
            _LOGGER.exception("OpenSeeFace provider failed")
            raise
        finally:
            self._sock = None
            self._phase = ""

    def _run_udp(self) -> None:
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
            self._phase = ""
            _LOGGER.info("Listening for OpenSeeFace on %s:%s", *sock.getsockname())
            while not self._stop_event.is_set():
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

    def _run_camera(self) -> None:
        import cv2

        self._phase = "checking/downloading OpenSeeFace models"
        detection_model, landmark_model = ensure_models(self.model_dir, self.model)
        self._phase = "loading OpenSeeFace models"
        estimator = _CameraPoseEstimator(
            detection_model, landmark_model,
            detection_threshold=self.detection_threshold,
            landmark_threshold=self.landmark_threshold,
            threads=self.threads,
        )
        self._phase = f"opening camera {self.camera_index}"
        capture = None
        if os.name == "nt":
            # DirectShow opens in well under a second; MSMF can take many seconds.
            capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not capture.isOpened():
                capture.release()
                capture = None
        if capture is None:
            capture = cv2.VideoCapture(self.camera_index)
        try:
            if not capture.isOpened():
                raise RuntimeError(f"could not open camera {self.camera_index}")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            if self.target_fps > 0:
                capture.set(cv2.CAP_PROP_FPS, self.target_fps)
            period = 1.0 / self.target_fps if self.target_fps > 0 else 0.0
            failed_reads = 0
            while not self._stop_event.is_set():
                frame_start = time.monotonic()
                ok, frame = capture.read()
                timestamp = time.monotonic()
                if not ok or frame is None:
                    failed_reads += 1
                    if failed_reads >= 100:
                        raise RuntimeError(f"camera {self.camera_index} stopped delivering frames")
                    self._update_tracking_state()
                    time.sleep(0.01)
                    continue
                failed_reads = 0
                self._phase = ""
                pose = estimator.process(frame)
                if pose is None:
                    self._update_tracking_state()
                else:
                    self._emit_valid(ProviderSample(
                        timestamp=timestamp,
                        quaternion=_wire_quaternion_to_head(pose["wire_quaternion"]),
                        raw_metadata={
                            "source": "openseeface",
                            "face_id": 0,
                            "camera_resolution": [frame.shape[1], frame.shape[0]],
                            "fit_3d_error": pose["fit_error"],
                            "confidence": pose["confidence"],
                        },
                    ))
                sleep_for = period - (time.monotonic() - frame_start)
                if sleep_for > 0:
                    self._stop_event.wait(sleep_for)
        finally:
            capture.release()


def main(argv: list[str] | None = None) -> int:
    """Print live angles without starting the GUI or controlling the mouse."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, default="camera")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=int, default=3)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11573)
    parser.add_argument("--face-id", type=int, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        provider = OpenSeeFaceProvider(
            source=args.source, camera_index=args.camera, model=args.model,
            udp_host=args.host, udp_port=args.port, face_id=args.face_id,
        )
    except ValueError as exc:
        parser.error(str(exc))
    provider.start()
    last_status = ""
    try:
        while provider.state != ProviderState.ERROR:
            if provider.status_text != last_status:
                last_status = provider.status_text
                print(f"[{last_status}]", flush=True)
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
        print(f"[{provider.status_text}]", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        provider.stop()


if __name__ == "__main__":
    raise SystemExit(main())
