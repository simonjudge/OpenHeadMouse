"""Webcam head-pose provider using OpenCV capture + MediaPipe FaceLandmarker.

Pipeline: OpenCV grabs BGR frames -> MediaPipe Tasks `FaceLandmarker` (VIDEO
running mode, ``output_facial_transformation_matrixes=True``) returns a 4x4
facial transformation matrix per detected face -> the rotation part is
re-expressed in this project's [+X forward, +Y left, +Z up] head frame (see
core/quaternion_math.py) -> converted to a [w, x, y, z] quaternion.

MediaPipe's facial transformation matrix is expressed in its camera-space
coordinate system: +X right, +Y up, +Z toward the viewer/camera (an
OpenGL-style right-handed frame; when a face looks straight at the camera,
the rotation part of the matrix is close to identity). Matching this
project's convention up when facing the camera dead-on:
  - head "forward" (nose direction, +X_head) <-> camera +Z (out of the face,
    toward the camera)
  - head "left" (+Y_head, ear-to-ear) <-> camera +X (a person's own left ear
    appears on the *viewer's* right, i.e. +X in camera space)
  - head "up" (+Z_head) <-> camera +Y

That mapping is a fixed axis permutation P (a rotation matrix, not a
reflection: det(P) = +1, so handedness is preserved):
    P = [[0, 0, 1],
         [1, 0, 0],
         [0, 1, 0]]
    (i.e. head_x = cam_z, head_y = cam_x, head_z = cam_y)
Re-expressing the MediaPipe rotation matrix R_mp in the head-labeled frame is
the similarity transform R_head = P @ R_mp @ P.T (P is orthogonal, P^-1 = P.T).

CAVEAT: this axis mapping is derived from MediaPipe's documented camera-space
convention, not empirically verified against a live camera in this sandboxed
build environment. Run this module standalone (``python -m
providers.uniface_provider``) in front of a real webcam and confirm the
printed yaw/pitch/roll signs match real head motion (turn left -> yaw
positive, look up -> pitch positive, tilt left ear down -> roll positive)
before relying on it for cursor control. See tests/test_quaternion_math.py
for the project's sign-convention definitions.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from core import quaternion_math as qm
from providers.base_provider import BaseProvider, ProviderSample, ProviderState

# Permutation matrix mapping MediaPipe camera-space axes (X right, Y up, Z
# toward viewer) to this project's head frame (X forward, Y left, Z up).
_CAMERA_TO_HEAD_FRAME = np.array(
    [
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
)


def _rotation_matrix_to_quaternion(r: np.ndarray) -> np.ndarray:
    """Convert a 3x3 rotation matrix to a [w, x, y, z] unit quaternion.

    Standard Shepperd's method (numerically stable branch selection on the
    matrix trace / largest diagonal element), avoiding the direct sqrt(1+trace)
    formula's failure near trace == -1.
    """
    m = np.asarray(r, dtype=np.float64)
    trace = m[0, 0] + m[1, 1] + m[2, 2]

    if trace > 0:
        s = 0.5 / np.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (m[2, 1] - m[1, 2]) * s
        y = (m[0, 2] - m[2, 0]) * s
        z = (m[1, 0] - m[0, 1]) * s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = 2.0 * np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s

    return qm.normalize(np.array([w, x, y, z]))


def _transformation_matrix_to_head_quaternion(matrix4x4: np.ndarray) -> np.ndarray:
    r_mp = np.asarray(matrix4x4, dtype=np.float64)[:3, :3]
    r_head = _CAMERA_TO_HEAD_FRAME @ r_mp @ _CAMERA_TO_HEAD_FRAME.T
    return _rotation_matrix_to_quaternion(r_head)


class UniFaceProvider(BaseProvider):
    """Head-pose provider driven by a webcam via OpenCV + MediaPipe FaceLandmarker."""

    def __init__(
        self,
        camera_index: int = 0,
        target_fps: int = 30,
        model_path: str = "models/face_landmarker.task",
        tracking_lost_hold_ms: float = 500.0,
        queue_maxsize: int = 256,
    ):
        super().__init__(queue_maxsize=queue_maxsize)
        self.camera_index = camera_index
        self.target_fps = target_fps
        self.model_path = model_path
        self.tracking_lost_hold_ms = tracking_lost_hold_ms

    def _run(self) -> None:
        # Imported lazily: importing mediapipe/cv2 at module load time would make
        # every consumer of providers/ (including mock-only tests) require a
        # working camera/MediaPipe install.
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        model_path = Path(self.model_path)
        if not model_path.is_file():
            raise FileNotFoundError(
                f"FaceLandmarker model not found at '{model_path}'. Download it from "
                "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                "face_landmarker/float16/latest/face_landmarker.task"
            )

        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,
        )

        capture = cv2.VideoCapture(self.camera_index)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera index {self.camera_index}")

        last_good_quaternion = qm.IDENTITY_QUATERNION.copy()
        last_seen_monotonic = time.monotonic()
        period = 1.0 / self.target_fps if self.target_fps > 0 else 0.0

        try:
            capture.set(cv2.CAP_PROP_FPS, self.target_fps)
            with vision.FaceLandmarker.create_from_options(options) as landmarker:
                while not self._stop_event.is_set():
                    frame_start = time.monotonic()
                    ok, frame = capture.read()
                    capture_timestamp = time.monotonic()
                    if not ok:
                        # Transient camera read failure; treat like a lost frame
                        # rather than a fatal error (loose cable, USB hub hiccup).
                        continue

                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    timestamp_ms = int(capture_timestamp * 1000)
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)

                    face_detected = bool(result.facial_transformation_matrixes)
                    if face_detected:
                        matrix = np.array(result.facial_transformation_matrixes[0])
                        last_good_quaternion = _transformation_matrix_to_head_quaternion(matrix)
                        last_seen_monotonic = capture_timestamp
                        self._state = ProviderState.RUNNING
                    else:
                        lost_ms = (capture_timestamp - last_seen_monotonic) * 1000.0
                        self._state = (
                            ProviderState.TRACKING_LOST
                            if lost_ms > self.tracking_lost_hold_ms
                            else ProviderState.RUNNING
                        )

                    # Always emit at the capture cadence (holding the last known
                    # good pose while lost) so downstream consumers relying on a
                    # steady sample rate don't stall; consumers that care about
                    # staleness should check provider.state, not just quaternion
                    # motion.
                    self._emit(
                        ProviderSample(
                            timestamp=capture_timestamp,
                            quaternion=last_good_quaternion,
                            raw_metadata={"face_detected": face_detected},
                        )
                    )

                    if period > 0:
                        elapsed = time.monotonic() - frame_start
                        sleep_for = period - elapsed
                        if sleep_for > 0:
                            time.sleep(sleep_for)
        finally:
            capture.release()


def _run_standalone_console_test() -> None:
    """Manual verification tool: prints live yaw/pitch/roll for sign checking.

    Run with a real webcam: ``python -m providers.uniface_provider``. Turn
    your head left -> yaw should increase; look up -> pitch should increase;
    tilt your left ear toward your left shoulder -> roll should increase.
    """
    provider = UniFaceProvider()
    provider.start()
    print("Streaming UniFace pose (Ctrl+C to stop)...")
    try:
        while True:
            sample = provider.output_queue.get()
            yaw, pitch, roll = np.degrees(qm.to_euler_zyx(sample.quaternion))
            detected = sample.raw_metadata.get("face_detected")
            print(
                f"\rstate={provider.state.value:14s} face_detected={str(detected):5s} "
                f"yaw={yaw:+7.2f} pitch={pitch:+7.2f} roll={roll:+7.2f}",
                end="",
                flush=True,
            )
    except KeyboardInterrupt:
        pass
    finally:
        provider.stop()
        print()


if __name__ == "__main__":
    _run_standalone_console_test()
