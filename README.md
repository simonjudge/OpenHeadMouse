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

Ticking the box only arms the feature. Dwell clicking starts only once the input provider is
actually controlling the cursor (mouse control active and head data moving the pointer), not
merely when **Start** is pressed or tracking is resumed. It stops whenever mouse control is
switched off, paused by the toggle hotkey or corner failsafe, or suspended during initial
calibration.

If the pointer leaves an activation area and returns within the configurable resume time
(default 0.5 seconds), progress continues from where it paused. Dwell time, movement tolerance,
and resume time can be changed in the main window and are persisted in `config.json`.

The timer ring's appearance is also configurable: **Diameter** (size of the circle, default 40
pixels), **Thickness** (width of the timer segment, default 4 pixels), and **Colour** (click
the swatch to open a colour picker). These are saved as `ring_diameter_px`,
`ring_thickness_px`, and `ring_color` under `ui.dwell_click` in `config.json`, and the same
options appear in the tray's dwell-click settings dialog.

The module can also run independently of HeadMouse:

```
python -m dwell_click
python -m dwell_click --dwell-time 1.5 --movement-tolerance 12 --resume-time 0.75
python -m dwell_click --diameter 60 --thickness 8 --color "#00a0ff"
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

Main-window labels are kept short and related options sit side by side; hover over any
option to see a fuller explanation.

The DSP configuration supports absolute mapping and velocity mapping. The optional One Euro
filter applies adaptive smoothing in either mode. Velocity mode also supports deadband/gamma
shaping, stationary auto-zero bias, and optional yaw-drift correction. Cursor clamping
follows the display that contains the HeadMouse window, including displays with negative
coordinate origins. On Windows, native pixel bounds are used so display scaling does not
shrink the usable cursor area. The bounds are refreshed and the cursor is centered on that
display whenever tracking starts.

### Recordings folder

Everything this app records is written under one base folder, so a study's data stays
together:

```
<recordings folder>/sessions/        head-movement session files
<recordings folder>/fitts_sessions/  Fitts' Law result files
```

Set it from the main HeadMouse window with **File → Set Recordings Folder...** (hover over the
menu item to see the current folder). The choice is saved in `config.json` as
`recorder.base_dir` and is shared with the Fitts' Law test window.

If no folder has been chosen, you are asked for one the first time something is actually
recorded: when **Recording** is switched on, when a provider is started with **Recording**
already on, or when a Fitts test is started with **Record head movement** ticked. This also
applies to the Fitts window run standalone (`python fitts_law/fitts_test.py`), which asks for
the folder itself and saves the answer to `config.json` and to its own
`fitts_law/fitts_settings.json`, so it is only asked once either way. Cancelling the prompt
falls back to the `recorder.output_dir` default (`sessions/`), and you'll be asked again next
time.

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

Packaged builds initialize their per-user `config.json` from the bundled defaults on first
launch, then save settings to the user's configuration directory.

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

In Velocity mode, **Auto-zero** (bias) estimates angular-rate bias after sustained low-speed
motion; **Yaw drift** (correction) estimates the remaining yaw-rate bias more slowly using
its configured time constant. Both subtract rate estimates rather than accumulating pose
offsets. With both disabled, only the configured deadband suppresses small movements.
These estimates cannot distinguish sensor drift from deliberately very slow movement:
disable them for unrestricted fine motion. They do not recenter Absolute mode.

### Recording head movement during a Fitts test

The Fitts window's setup page has a **Record Head Movement** option and a **Start Test**
button with a start-mode dropdown to its right:

- **Record Head Movement** records head movement alongside the Fitts data, using the tracker,
  **Initial Calibration** and **Recording** settings of the main HeadMouse window. It turns
  **Recording** on so the head session is saved. The head session and the Fitts session can then
  be replayed together with **Open Synchronised Playback…**.
- **Start Test** runs the test with whatever is already driving the OS cursor (head mouse,
  trackpad, eye gaze, switch, etc). The test window itself stays agnostic to the input device.
  With **Record Head Movement** ticked, it also brings the head tracker up but switches
  **Mouse Control** *off*, so head movement is recorded while the access method being measured
  keeps the cursor. This is how head movement is captured during, say, an eye-gaze or switch run.
- **Start with dwell click** adds dwell clicking to the current cursor input for the mouse
  task. When hosted by HeadMouse it reuses the main GUI's dwell timing, tolerance and ring
  settings without changing its enabled checkbox. Standalone runs use the default dwell settings.
  Dwell clicking (including dwell enabled in the main HeadMouse GUI) is suppressed during
  tracker startup and all head exercises; it starts only when the mouse task begins.
  Dwell clicking and HeadMouse stop when the mouse task completes, is cancelled, or the
  window closes. The head recording is saved automatically and retained for playback.
- **Start with HeadMouse** records head movement *and* drives the cursor with
  it. It starts the chosen provider exactly as the setup panel's **Start** button would, ticking
  **Record Head Movement** and switching **Mouse Control** on. If initial calibration is enabled,
  the calibration overlay runs first and the trials only begin once the neutral pose has been
  captured and tracking is live. If the tracker is already running, the test starts straight away.
  It respects the main GUI's dwell-click enabled setting, sets **Access method** to HeadMouse,
  and is disabled when the Fitts window is run
  standalone (`python fitts_law/fitts_test.py`), where there is no HeadMouse app to drive.

Either way the head recording covers the whole run, so its samples can be labelled by phase (see
below). `metadata.record_head_movement` in the Fitts file records which mode was used.

The **Tasks** dropdown selects **HeadMovement and Mouse Task**, **HeadMovement only**, or
**Mouse Task only**. HeadMovement always includes both tasks below and requests head recording
when hosted by the main app. In standalone runs it records the task timings and cursor
trajectories, but cannot start a head tracker. Head-only runs skip the mouse trials and do not
require valid mouse-task settings.

- **Initial Rest Position (Calibration)** gives the participant the configured move/read time
  to adopt a comfortable neutral pose, then records it for the configured hold time. It uses the
  same neutral-pose calculation as the main HeadMouse calibration. If HeadMouse is running, its
  cursor is paused during the task and the held pose recentres it before the next task.
- **Range Of Movement Task** asks for maximum comfortable movement in this order: left, right,
  up, down. For each direction a red band marks the corresponding screen edge. The participant
  gets the configured move time to reach it and the configured hold time to maintain it.

**Time to move/read instructions** and **Time to hold/record position** are shared by both tasks.
Press Esc to cancel a pre-test sequence and return to setup.

At completion the test window closes before its results are saved. If saving fails, a warning
explains the error and a folder chooser lets you retry (the JSON is saved directly in the selected
folder). Cancelling the chooser reopens the results window with the data still in memory, so
**File > Save Results As…** can be used later. Both mouse trials and head-only task records are
saved. When hosted by HeadMouse, reopen the Fitts window from the File menu to inspect the results.
Use **File > Close** or `Ctrl+W` to stop a run midway. Head tracking
and dwell clicking stop, partial mouse trials/head-task phases are saved, and unfinished phases
are marked as cancelled. The setup window and both playback windows have the same Close controls;
the setup panel and fullscreen calibration overlay also have a Close button.

All provider samples are retained during calibration, range-of-movement and Fitts trials (the
cursor still uses only the newest sample per GUI update). In head-session JSON/CSV each sample's
`metadata.task_phase` identifies its section, for example
`initial_rest.hold`, `range_of_movement.left.move`, or `fitts_test`. Head-session `task_phase`
events mark the same boundaries. The Fitts JSON `metadata` contains:

- `pretest_configuration` — enabled tasks and configured move/hold durations;
- `pretest_phases` — ordered phase labels, task/direction/segment, shared-host-clock start/end
  times, and cursor trajectories captured during each phase;
- `fitts_task` — the `fitts_test` phase label, shared-host-clock boundaries, and completion flag.

These labels allow the neutral-rest, range-of-movement and pointing data to be extracted without
inferring boundaries from timing. The held neutral samples also remain in the head session's
top-level `calibration` section for existing calibration analysis.

### Fitts playback and synchronised playback

The session viewer's File menu has three ways to open a recording:

- **Open HeadMouse Session…** (`Ctrl+O`) opens a head-movement recording on its own.
- **Open Synchronised Playback…** (`Ctrl+Shift+O`) asks for a head-movement session and then a
  Fitts session. The two windows are then driven by one shared playback clock, so Play, the slider
  or the speed control in either window moves both. Playback covers the head-movement exercise
  (initial head position and range of movement) and the mouse task. Head data recorded before
  or after these is not shown, and head statistics cover only that period. Times in both
  windows are measured from the start of the exercise (or of the mouse task when there was no
  exercise).
- **Open Fitts Test Only…** (`Ctrl+F`) opens a Fitts playback window on its own. The left side shows a scaled drawing of the test screen. It shows the current target
  (diameter W), the start point, the dashed task axis, the cursor trail up to the playhead, the
  cursor, and click markers (green for a hit, red for a miss). Earlier trials of the same condition
  are drawn faded. Below the drawing are graphs of cursor speed and distance to target, with a
  playhead and vertical lines at each trial's start (green, dashed) and click (purple, dotted).

All three are also on the viewer's toolbar.

**HeadMovement only** Fitts files also open through these options, even with no mouse trials.
Playback follows the recorded initial-rest/range-of-movement phases and their cursor traces;
pointing measures are marked as not applicable and mouse-task graphs are hidden. For runs with
both tasks, playback first shows the exercise phases and then the mouse trials.

The head viewer always chooses a neutral reference for the face mesh: the recorded recenter/
calibration pose (including a Fitts initial-rest calibration), otherwise phase-labelled
`initial_rest.hold` samples, otherwise the mean of up to the first 30 samples within the first
second of use. The fallback uses the beginning of the original recording, even when playback
is trimmed to a later task. Disabling **Initial Calibration** therefore no longer makes the
viewer average the whole movement exercise to guess a neutral pose. The Calibration panel
identifies an inferred reference when no explicit calibration was recorded.

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
task in words, for example "Range of movement - holding head at furthest LEFT position" or
"Fitts mouse task". During the mouse task it also shows the current trial and its measures, up to
the playhead. The head viewer's **Live Data** shows the same task description, from each head
sample's `task_phase` label.
Hover over any measure name in **Summary**, **Live Data**, or **Initial Pose & Range of Movement**
for its definition, units, and (where relevant) formula or interpretation. This includes Ae,
We, IDe, throughput, TRE, TAC, MDC, ODC, MV, ME and MO. Path-accuracy summaries average the
measure over trials with usable recorded paths; live measures use only the path up to the playhead.

#### Initial pose and range of movement summary

When the run included the pre-test tasks, an **Initial Pose & Range of Movement** box appears below
the summary (it is hidden for sessions without that data). It is built from
`metadata["pretest_phases"]` in the Fitts file, so it is available in **Open Fitts Test Only…** too:

- **Initial head pose** — cursor drift during `initial_rest.hold` (σx, σy and the largest deviation
  from the mean position), the hold duration and the number of cursor points.
- **Range of movement** — the extreme cursor position reached in each `range_of_movement.*.hold`
  phase, plus the overall horizontal and vertical cursor ranges in px.

The cursor position is sampled on every exercise tick (about 20 Hz). Mouse-move events alone
were not reliably delivered while HeadMouse moved the hidden cursor, so earlier recordings may
hold few or no cursor points.

The head session's samples are also passed to the Fitts window. In **Open Synchronised
Playback…** you choose that session. **Open Fitts Test Only…** finds it automatically: first the
file named in the Fitts file's `metadata["head_session_file"]`, which is written when the test
saves its head recording, then the `session_*.json` saved closest in time in the neighbouring
`sessions` folder. A head file is used only if its timestamps overlap the Fitts session. Their `metadata.task_phase` labels add head-pose statistics: mean and standard deviation of
yaw, pitch and roll at rest, the mean angle held in each direction with its offset from the rest
pose, and the resulting yaw (left↔right) and pitch (up↔down) ranges in degrees.

The same pair of windows can be opened straight from the Fitts test. Reopen the test window
after a mouse-task run finishes; its results page has an **Open Synchronised Playback…**
button (also in that window's File menu,
alongside Save/Open Results). It uses the head recording saved at completion (or stops and saves
one if still running), then
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
  
