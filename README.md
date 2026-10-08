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
- `session_viewer/` — standalone pyqtgraph session viewer (mesh + line-graph playback), opened from the main window's File menu; also replays Fitts sessions, optionally synchronised with a head recording (see below)
- `fitts_law/` — standalone ISO 9241-411 Fitts' Law test suite (circular + edge-target tasks, pluggable stimuli, Random Path and Typing Test modes), opened from the main window's File menu
- `dwell_click/` — standalone dwell-click engine, translucent progress overlay, and command-line launcher
- `tests/` — unit tests

### Dwell click

Tick **Dwell Click** in the Subsystems panel to click automatically when the pointer remains
inside the configured movement tolerance (default 10 pixels) for the dwell time (default 1
second). A translucent red ring around the cursor fills clockwise as the dwell completes. After
a click, the pointer must leave the activation area before another click can occur.

Ticking the box only arms the feature. Dwell clicking starts when mouse control becomes active
and stops whenever mouse control is switched off, paused by the toggle hotkey or corner
failsafe, or suspended during initial calibration.

If the pointer leaves an activation area and returns within the configurable resume time
(default 0.5 seconds), progress continues from where it paused. Dwell time, movement tolerance,
and resume time can be changed in the main window and are persisted in `config.json`.

The module can also run independently of HeadMouse:

```
python -m dwell_click
python -m dwell_click --dwell-time 1.5 --movement-tolerance 12 --resume-time 0.75
```

Both standalone mode and HeadMouse provide a cross-platform system-tray icon. Its menu starts
or stops dwell clicking, opens the dwell-click settings dialog, and exits standalone mode.

### Coordinate frame and gain signs

All providers emit quaternions in one canonical head frame (+X forward, +Y left, +Z up), so
for every provider turning left gives **positive yaw**, looking up gives **positive pitch** and
tilting the left ear down gives **positive roll**. Cursor mapping uses the same signs in
absolute and velocity mode: **positive gains make the cursor follow the head** (turn left →
cursor left, look up → cursor up). A negative gain inverts that axis. If a provider needs
negative gains to feel natural, its frame conversion is wrong. See
[COORDINATE_FRAMES.md](COORDINATE_FRAMES.md) for the per-provider conversions and a checklist
for adding a new provider.

The DSP configuration supports absolute mapping and velocity mapping. The optional One Euro
filter applies adaptive smoothing in either mode. Velocity mode also supports deadband/gamma
shaping, stationary auto-zero bias, and optional yaw-drift correction. Cursor clamping
follows the display that contains the HeadMouse window, including displays with negative
coordinate origins. On Windows, native pixel bounds are used so display scaling does not
shrink the usable cursor area. The bounds are refreshed and the cursor is centered on that
display whenever tracking starts.

### Session files

Each Start → Stop run of a provider is saved as its own session file when **Recording** is on.
The recorder is cleared after every save. Earlier versions kept every sample since launch, so
each file also held data from previous runs, sometimes from different providers with different
clocks. The session viewer handles those older files by switching to `host_time` when the
provider timestamps aren't one consistent clock. For old files without `host_time`, the plots
can still look squashed.

### Angular speed and acceleration

Head speed is the geodesic angle between consecutive orientation quaternions divided by the
elapsed time, rather than the length of the yaw/pitch/roll difference vector. The geodesic
angle is the true rotation magnitude, so it doesn't inflate near gimbal lock and doesn't jump
at the ±180° yaw wrap.

Samples are delivered in bursts, so a few consecutive pairs arrive microseconds apart while
carrying a normal amount of rotation. Dividing by those tiny intervals produced peaks of
hundreds or thousands of deg/s that were artefacts of delivery timing, not head movement.
Intervals shorter than half the session's median interval are therefore skipped instead of
being differentiated. `SessionStats.sampling_stats()` reports the effective sample rate and how
many intervals were skipped, and the viewer shows both in the Session Data panel. The viewer's
Vel/Accel graphs use the same computation as the statistics panel, so the two always agree.

### Initial calibration

Tick **Initial Calibration** (Subsystems panel) to capture the at-rest head pose each time a
provider is started. A fullscreen target appears on the tracking display asking the user to
hold a neutral head position. After a read period (`calibration.read_duration_s`, default 5 s)
a countdown (`calibration.capture_duration_s`, default 5 s) runs while samples are captured.
The cursor does not move and no movement samples are recorded during this time. The mean
quaternion becomes the neutral reference (the same recentre used by the **Recenter** button),
the cursor is centred and tracking starts. Press Esc (or Stop) to abort; the provider stops.

When **Recording** is on, the capture is saved separately from movement data: JSON sessions
(`format_version` `1.1`) gain a top-level `calibration` object (timestamps, sample count,
neutral quaternion, neutral yaw/pitch/roll, their standard deviations, and the raw
calibration `samples`); CSV sessions gain a `phase` column (`calibration`/`movement`). Files
without calibration keep the previous format. The session viewer shows this under
**Calibration** in the Session Data panel: the mean at-rest Yaw/Pitch/Roll, each ± its
standard deviation during the capture.

Main-window settings are saved to `config.json` as they change and restored at the next
launch. Mapping mode and gains, velocity parameters, adaptive smoothing, auto-zero, and yaw
drift correction are stored separately for each input under `mouse_profiles`; selecting a
provider immediately restores its complete movement profile. Legacy configurations with one
top-level `dsp` section are migrated by copying that profile to every configured provider.
The selected provider, recording, mouse-control and calibration toggles, and xIMU3
magnetometer mode and mounting orientation remain global. When the application is launched
with `--config`, settings are saved back to that selected file.

### xIMU3 mounting orientation

When xIMU3 is selected, the **Mounting** dropdown appears beside the magnetometer
option. Choose **LHS-Horizontal**, **LHS-Vertical**, **RHS-Horizontal**, or
**RHS-Vertical**. Horizontal means hanging from glasses with USB pointing backward;
vertical means mounted on the temple with USB pointing down. Printed arrows face
out of the head in all four cases.

The selection saves `axes_alignment` and `ahrs_axes_convention` and sends both
settings to a connected device (USB or Wi-Fi). During a Wi-Fi outage it is applied
on reconnection. Changing a live mount resets cursor mapping and restarts calibration
if enabled. Existing non-preset settings appear as **Custom (from config)** and
are preserved until a preset is selected. See [Coordinate Frame Conventions](COORDINATE_FRAMES.md)
for the preset alignments.

### xIMU3 Wi-Fi AP connection

Join the device's Wi-Fi AP before starting xIMU3. The device picker scans for USB and Wi-Fi
announcements in the background; **Refresh devices** retries the scan. If announcements are
missed, **Connect directly** uses `providers.ximu3.wifi_ip_address`,
`wifi_port_send`, and `wifi_port_receive` from `config.json` (defaults:
`192.168.1.1`, `9000`, `8001`). Announced devices instead use their advertised
UDP ports. Confirm the computer is actually joined to the device's AP when direct
connection does not produce data.

The status under Input Provider distinguishes connection, configuration, waiting for
samples, streaming, and reconnection. Tracking and initial calibration wait for a
configured connection and a real quaternion. During a Wi-Fi outage cursor updates stop;
the SDK retries until Stop is pressed. On recovery tracking resumes using the previous
neutral pose, without counting the outage as a new recording session. Calibration that
was interrupted before completion restarts after samples resume. Configuration-command
rejections are errors, not successful connections. Both USB and Wi-Fi startup
verify and apply the saved device settings.

The xIMU3 SDK timestamps quaternion messages in microseconds of device time. The provider
converts these to seconds before velocity-mode processing and recording, matching the
other providers' timestamp unit; the measured sample interval, not the configured target
rate, determines angular speed. Existing recordings made before this conversion retain
their original microsecond timestamps and are not migrated.

Requires the `ximu3` Python package 1.9.1 or newer. Settings are sent as JSON commands
(e.g. `{"axesAlignment": 2}`) followed by `{"apply": null}`, as the x-IMU3 protocol requires.

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
accumulating into a jump on resume.

In Velocity mode, **Auto-zero bias** estimates angular-rate bias after sustained low-speed
motion; **Yaw drift correction** estimates the remaining yaw-rate bias more slowly using
its configured time constant. Both subtract rate estimates rather than accumulating pose
offsets. With both disabled, only the configured deadband suppresses small movements.
These estimates cannot distinguish sensor drift from deliberately very slow movement:
disable them for unrestricted fine motion. They do not recenter Absolute mode.

### Fitts playback and synchronised playback

The session viewer's File menu has two Fitts options:

- **Open Fitts Session…** opens a Fitts playback window on its own. The left side shows a scaled
  drawing of the test screen. It shows the current target (diameter W), the start point, the
  dashed task axis, the cursor trail up to the playhead, the cursor, and click markers (green for
  a hit, red for a miss). Earlier trials of the same condition are drawn faded. Below the drawing
  are graphs of cursor speed and distance to target, with a playhead and vertical lines at each
  trial's start (green, dashed) and click (purple, dotted).
- **Open Synchronised Playback…** asks for a head-movement session and then a Fitts session. The
  two windows are then driven by one shared playback clock, so Play, the slider or the speed
  control in either window moves both. Only the Fitts test period is played back. Head data
  recorded before or after the test is not shown, and head statistics cover only that period.
  Times in both windows are measured from the start of the test.

The right side of the Fitts window uses the same layout as the head viewer. The **summary** shows:

- ISO 9241-411 results: error rate, mean MT, Ae, We, IDe and throughput.
- The path-accuracy measures from MacKenzie, Kauppinen & Silfverberg, *Accuracy measures for
  evaluating computer pointing devices* (CHI 2001):
  - target re-entry (TRE), task axis crossing (TAC), movement direction change (MDC) and
    orthogonal direction change (ODC), as mean counts per trial;
  - movement variability (MV), movement error (ME) and movement offset (MO), in px.

  They are computed from each saved trajectory in task-axis coordinates, with the click point
  as the last path point.

The summary can exclude the first N repetitions of each condition. **Live Data** shows the current
trial and its measures, up to the playhead.

The same pair of windows can be opened straight from the Fitts test. When a run finishes, its
results page has an **Open Synchronised Playback…** button (also in that window's File menu,
alongside Save/Open Results). It stops and saves the head recording if one is still running, then
opens the two playback windows side by side, each filling half the screen. If the Fitts test was
run on its own, it asks which head session to use.

Synchronisation relies on a shared host clock (`core/clock.py`, the host's wall-clock time in
Unix-epoch seconds). It doesn't use `time.monotonic()`, because that clock stops while macOS
sleeps. Provider timestamps use each device's own clock, so they can't be compared
between files. Head sessions now save a `host_time` for every sample and event: a
`host_time` field plus a top-level `"clock"` key in JSON, or a `host_time` column in CSV.
Fitts sessions (`format_version` `1.1`) save `start_host_time`/`end_host_time` for every trial,
plus the screen size in `metadata`. Synchronisation is refused, with an explanation, if either
file has no host timestamps (recordings made before this change) or the two recordings don't
overlap in time. You can still play the Fitts session on its own.

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
TEST - add centre point to detect headpose and tremor etc.

IDEAS:
 * recentering tool for absolute mode
 * snap to object
 * ?? zooming dwell clicker ??
 *  FUSION MODE - camera+IMU
   * Use for recentering? Work out centre from camera, but only re-centre/adjust when at edges (hard to reach)????
 * calibration - establish ROM - work out the gains...

 * Head joystick - movement threshold ->  mouse movement.....

 * Generic switch - detect movement over threshold... -> switch signal
  
