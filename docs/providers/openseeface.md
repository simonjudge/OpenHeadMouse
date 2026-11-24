# OpenSeeFace provider

The [OpenSeeFace adapter](../../providers/openseeface_provider.py) supports
in-process webcam tracking (default) and external OpenSeeFace UDP tracking.

## Requirements

- A webcam and camera permission for `source="camera"`.
- OpenCV and ONNX Runtime from the application's dependencies.
- Verified OpenSeeFace detector and landmark ONNX models. Source runs download
  missing models on first use; release asset preparation installs them too.
- For `source="udp"` only, an independently installed
  [OpenSeeFace tracker](https://github.com/emilianavt/OpenSeeFace).

## Setup

### Webcam (recommended)

1. Use the [application source setup](../../README.md#setup-running-from-source)
   or a build with the model assets.
2. Leave `providers.openseeface.source` set to `"camera"`.
3. Select **OpenSeeFace (webcam)** and press **Start** with mouse control and
   dwell clicking off. No separate facetracker process is required.
4. Keep your face visible, verify the telemetry, then calibrate facing forward.

### External UDP tracker

1. Set `providers.openseeface.source` to `"udp"` in `config.json`.
2. Install OpenSeeFace separately. On Windows its release archive contains
   `Binary\facetracker.exe` and models. Start it, for example:

   ```powershell
   .\facetracker.exe -c 0 --faces 1 -i 127.0.0.1 -p 11573
   ```

3. Select **OpenSeeFace (webcam)** in OpenHeadMouse and press **Start**. The
   configured source, not the GUI label, determines camera versus UDP operation.
4. Stop the external tracker separately when finished. This adapter does not
   launch it; `tracker_command`, `tracker_args`, and CLI `--tracker` are not
   supported by the current implementation.

If using upstream `facetracker.py` instead of the executable, use its own
Python environment. Do not replace this project's dependencies with upstream's
older pinned dependencies. Do not run UniFace and OpenSeeFace on one camera.

## Configuration

Settings are under `providers.openseeface` in `config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `source` | `"camera"` | `"camera"` or `"udp"` |
| `camera_index`, `target_fps` | `0`, `30` | Capture device/rate |
| `frame_width`, `frame_height` | `640`, `480` | Requested capture size |
| `model`, `model_dir` | `3`, `models/openseeface` | Landmark model and verified assets |
| `detection_threshold`, `landmark_threshold` | `0.6`, `0.6` | Detection confidence thresholds |
| `threads` | `2` | ONNX inference threads |
| `udp_host`, `udp_port` | `127.0.0.1`, `11573` | External tracker destination/listener |
| `face_id` | `0` | Selected UDP face |
| `tracking_lost_hold_ms` | `500` | Loss-of-valid-pose interval |

Mouse gains and filtering are stored separately in `mouse_profiles.openseeface`.

## Validation

From the repository root, without controlling the mouse:

```powershell
python -m providers.openseeface_provider --source camera --camera 0
```

For an external tracker:

```powershell
python -m providers.openseeface_provider --source udp --port 11573 --face-id 0
```

Stop with Ctrl+C. For a neutral-relative direction test, use
`python test_orientation.py --provider openseeface --duration 20` with the source
set in `config.json`. Turn left, look up, and tilt left-ear-down: yaw, pitch, and
roll should increase. See [coordinate frames](../coordinate-frames.md).

## Troubleshooting and limitations

- Camera failures: check permissions, camera index, and competing applications.
- Model failures: check the configured directory, network access on first use,
  and checksum/download errors.
- UDP waiting: confirm the sender destination/port and selected face ID.
- UDP is unauthenticated; keep it on loopback, not untrusted networks.
- Only successful 3D fits produce samples; lost tracking never emits held poses.
  Malformed datagrams are logged and discarded, and socket errors are reported.
- Camera timestamps are monotonic post-capture time; UDP timestamps are sender
  post-capture/pre-inference time. Duplicates/backwards timestamps are discarded;
  restart the provider after a sender clock reset.
- The camera implementation estimates head pose only, not gaze, blink, or
  expression. Hardware validation is still required.
