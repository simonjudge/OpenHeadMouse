# HeadMouse

Modular, cross-platform software for recording, visualizing, analyzing, and translating 3D head
movements into computer cursor control. Normalizes multiple input providers (AirPods CoreMotion,
xIMU3 IMU, MediaPipe/UniFace webcam pose) into a shared quaternion reference frame, drives a
configurable DSP engine (absolute or velocity-based cursor mapping), and includes an ISO 9241-411
Fitts' Law evaluation suite.

## Setup

Requires Python 3.12 (mediapipe does not yet publish wheels for 3.13/3.14).

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Layout

- `core/` — quaternion math, DSP engine, recorder, OS cursor control
- `providers/` — input drivers (UniFace/MediaPipe, xIMU3, AirPods)
- `gui/` — PySide6 setup, DSP tuning, and Fitts' Law test screens
- `session_viewer/` — standalone pyqtgraph session viewer (mesh + line-graph playback), opened from the main window's File menu
- `tests/` — unit tests
