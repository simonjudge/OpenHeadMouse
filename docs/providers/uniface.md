# UniFace provider

The [UniFace adapter](../../providers/uniface_provider.py) uses OpenCV capture
and MediaPipe FaceLandmarker to estimate head orientation from a webcam.

## Requirements

- A webcam and camera access permission.
- The application's [source dependencies](../../README.md#setup-running-from-source),
  or an OpenHeadMouse build containing the webcam assets.
- The MediaPipe `models/face_landmarker.task` asset.

## Setup

1. For source use, run `python tools\prepare_release.py --assets` from the
   repository root to install and verify the model assets.
2. Close other applications using the camera.
3. Select **UniFace (webcam)** and press **Start** with mouse control and dwell
   clicking off. Keep your face visible and well lit.
4. Confirm the telemetry follows your head, then enable initial calibration
   and restart facing forward before enabling mouse control.

## Configuration

Settings are under `providers.uniface` in `config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `camera_index` | `0` | OpenCV camera index |
| `target_fps` | `30` | Requested capture rate |
| `landmarker_model_path` | `models/face_landmarker.task` | FaceLandmarker asset |
| `tracking_lost_hold_ms` | `500` | Loss-of-tracking interval |

Mouse gains and filtering are stored separately in `mouse_profiles.uniface`.

## Validation

From the repository root:

```powershell
python test_orientation.py --provider uniface --duration 20
```

Hold a neutral pose for the first sample, then turn left, look up, and tilt the
left ear down. Yaw, pitch, and roll should respectively increase. Do not run
this diagnostic while the GUI is using the same camera. See
[coordinate frames](../coordinate-frames.md).

## Troubleshooting and limitations

- If the camera does not open, check OS camera permissions, close competing
  applications, and try another `camera_index`.
- If the model is missing, prepare the assets and check the configured path.
- If pose tracking is unstable, improve lighting and keep the face in view.
- Orientation is relative to the camera, not a compass or gravity reference.
