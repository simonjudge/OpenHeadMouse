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

- `core/` — quaternion math, absolute/velocity DSP engine and filters, recorder, OS cursor control
- `providers/` — input drivers (UniFace/MediaPipe, xIMU3, AirPods)
- `gui/` — PySide6 setup and DSP tuning UI
- `session_viewer/` — standalone pyqtgraph session viewer (mesh + line-graph playback), opened from the main window's File menu
- `fitts_law/` — standalone ISO 9241-411 Fitts' Law test suite (circular + edge-target tasks, pluggable stimuli, Random Path and Typing Test modes), opened from the main window's File menu
- `tests/` — unit tests

The DSP configuration supports absolute mapping and velocity mapping. The optional One Euro
filter applies adaptive smoothing in either mode. Velocity mode also supports deadband/gamma
shaping, stationary auto-zero bias, and optional yaw-drift correction. The application uses Qt screen geometry so cursor
clamping follows the display that contains the HeadMouse window, including displays
with negative coordinate origins. Starting tracking centers the cursor on that display.

Main-window settings are saved to `config.json` as they change and restored at the next
launch. This includes the selected provider, recording and mouse-control toggles, xIMU3
magnetometer mode, mapping mode and gains, velocity parameters, and filter options. When
the application is launched with `--config`, settings are saved back to that selected file.

### Absolute-mode fine pointing

Enable **One Euro filter** under **Adaptive smoothing** to reduce cursor jitter when
selecting small targets, then choose a filtering level:

- **Low filtering** — most responsive; use when the input is already stable.
- **Medium filtering** — balances stability and responsiveness and is the recommended default.
- **High filtering** — steadier fine pointing, with more noticeable movement lag.

For tuning, run the same small-target Fitts condition with smoothing off and on. Compare
error rate and throughput, starting with Medium and moving to High only if needed. The
checkbox is optional—when disabled, absolute mapping uses the unfiltered yaw and pitch values.

Saved filter values that do not match a preset appear as **Custom (from config)**.
They remain active until you explicitly select a preset.

Absolute mode applies the calculated screen position directly, including after recentering
and returning from a display edge. Velocity-mode settings changes preserve the current
cursor position; pausing or disabling mouse control prevents background movement from
accumulating into a jump on resume. Positive velocity vertical gain moves upward for
positive (upward) pitch.

In Velocity mode, **Auto-zero bias** estimates angular-rate bias after sustained low-speed
motion; **Yaw drift correction** estimates the remaining yaw-rate bias more slowly using
its configured time constant. Both subtract rate estimates rather than accumulating pose
offsets. With both disabled, only the configured deadband suppresses small movements.
These estimates cannot distinguish sensor drift from deliberately very slow movement:
disable them for unrestricted fine motion. They do not recenter Absolute mode.

## Validation

Run the headless test suite from `headmouse_project/`:

```
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Camera, xIMU3, AirPods, global hotkey, and native Swift helper behavior require the
corresponding hardware and macOS permissions; the protocol and provider unit tests run
without those devices.

Press **Esc** to pause or resume cursor tracking.

On macOS, the global toggle uses `Cmd+Option+M` (`<cmd>+<alt>+m`). Grant Accessibility
and Input Monitoring permission to the executable that actually runs the app (usually
the project virtual-environment Python, not just VS Code), then restart HeadMouse.
If registration is denied, the main window displays the reason instead of silently
claiming the hotkey is active.

TODO:
FITTS - Record rest position
RECORDER - ?? What to record RE trajectory. 

 * recentering tool for absolute mode
 * snap to object
 * ?? zooming dwell clicker ??
 *  FUSION MODE - camera+IMU
   * Use for recentering? Work out centre from camera, but only re-centre/adjust when at edges (hard to reach)????
 * calibration - establish ROM - work out the gains...

 CHECK
  * timestamps in mouse logging line up with the timestamps of mouse data
  
