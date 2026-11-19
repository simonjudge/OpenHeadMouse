# OpenHeadMouse

Repository: [simonjudge/OpenHeadMouse](https://github.com/simonjudge/OpenHeadMouse).

The project was previously named HeadMouse. Existing recordings and settings remain
compatible; the saved Fitts start mode is migrated to **Start with OpenHeadMouse**.

OpenHeadMouse translates head movements into cursor control and provides tools for
recording, replaying and analysing pointing performance. Use a webcam, xIMU3,
supported AirPods (macOS only), or compatible Sony headphones through a managed
Sony Head Tracker bridge (Windows 11/macOS 14+), with optional dwell clicking.

**First release in preparation: `0.2.0-rc.1`.** This is experimental assistive and
research software, not a certified medical device or a validated clinical assessment.
Keep another way to control the computer available while testing. Stop if movement
causes discomfort.

- [Downloads and release notes](https://github.com/simonjudge/OpenHeadMouse/releases):
  choose **Full** unless you only need a standalone tool. A draft is not a public download.
- [Windows quick start](#windows-quick-start) | [Source setup](#setup-running-from-source)
- [Release checklist](RELEASE_CHECKLIST.md) | [Report a problem](CONTRIBUTING.md)
- [MIT licence](LICENSE) | [Third-party distribution review](THIRD_PARTY_NOTICES.md)

![OpenHeadMouse setup window, with mouse control and recording disabled](docs/images/main-window.png)

The screenshot shows the application alone with no live tracker or participant data.

### Platform support

| Platform | First-release status | Notes |
| --- | --- | --- |
| Windows x64 | Primary release target; clean-machine/hardware sign-off still required | Test Windows 10/11, permissions, DPI and multiple monitors |
| macOS 14+, Apple Silicon | Experimental | arm64 builds, not universal/Intel; camera, Accessibility and Input Monitoring permissions; zoom also needs Screen Recording; AirPods also require Xcode command-line tools and Terminal Motion permission |
| Linux x64 | Experimental | Built on Ubuntu 24.04; X11 desktop expected; Wayland global hotkeys/input injection are not supported |

Only claim a tested OS/provider combination after recording its result in the
[release checklist](RELEASE_CHECKLIST.md). Successful packaging is not hardware validation.

Core tracking, calibration, recording, dwell clicking, Fitts tasks and session playback
are intended for all three platforms, subject to the permissions and runtime dependencies
above. This does not imply feature parity with unsupported environments: AirPods CoreMotion
is macOS-only; global pointer control and hotkeys require X11 on Linux; the 3D viewer needs
a working desktop OpenGL context; Snap to can only detect controls exposed by accessibility
APIs. Windows elevated/secure desktops and taskbar previews have additional restrictions
described in the [dwell-click guide](docs/dwell-click.md). Native hardware and packaged-app
checks remain necessary on each OS.

## Windows quick start

1. When an approved release is available, download the **Full Windows x64 installer**
   and its `SHA256SUMS-Windows.txt` from the same release. Python is not needed.
2. Compare the download with its listed checksum:
   `Get-FileHash .\OpenHeadMouse-windows-amd64-setup.exe -Algorithm SHA256`.
   A checksum detects changed bytes; it does not replace publisher authentication.
3. Install and launch OpenHeadMouse. Current installers are **unsigned**. Windows may
   show an unknown-publisher/SmartScreen warning; do not disable protection globally.
   If you cannot verify or trust a download, do not run it.
4. Select your input provider. For a webcam, allow camera access and start tracking
   with **Mouse Control** and **Dwell Click** off. Confirm the telemetry follows your head.
5. Enable **Initial Calibration**, sit comfortably facing forward, then enable mouse
   control when ready. Start with gentle movements; tune gains to your needs.
6. **Esc pauses/resumes tracking.** Holding the cursor in a screen corner for the
   configured failsafe time pauses it too. Keep a physical mouse or helper available;
   verify both pause routes before enabling dwell clicking.
7. To record, enable **Recording** and choose a folder when prompted. Data is saved
   beneath its `sessions` and `fitts_sessions` folders. Reopen it from the File menu.

The portable EXE can be run without installing, but still uses per-user settings.
Do not expect portable settings alongside the EXE.

See [coordinate frames](docs/coordinate-frames.md) for mounting and movement conventions.

## Documentation

- [Recording formats](docs/recording-formats.md) — head and Fitts file fields, clocks and the ZIP assignment manifest.
- [Dwell click](docs/dwell-click.md) — settings, standalone operation, context menu, snap, zoom and platform limits.
- [Colab analysis](docs/colab-analysis.md) — calculations, dwell estimates, exclusions, aggregation and result interpretation.
- [Release building](docs/release-building.md) — Windows build prerequisites, commands, outputs and redistribution checks.
- [Coordinate frames](docs/coordinate-frames.md) — head axes, angle signs, provider conversions and hardware checks.

## Layout

- `core/` — quaternion math, absolute/velocity DSP engine and filters, recorder, OS cursor control
- `providers/` — input drivers (UniFace/MediaPipe, OpenSeeFace, xIMU3, AirPods, Sony)
- `gui/` — PySide6 setup and DSP tuning UI
- `session_viewer/` — standalone pyqtgraph session viewer (mesh + line-graph playback), opened from the main window's File menu; also replays Fitts sessions, optionally synchronised with a head recording (see below)
- `colab_session_viewer/` — [Colab notebook viewer](colab_session_viewer/README.md) with one head/cursor playback panel, multiple participants/sessions, pooled summaries and per-participant first-target exclusions
- `fitts_law/` — standalone ISO 9241-411 Fitts' Law test suite (circular + edge-target tasks, pluggable stimuli, Random Path and Typing Test modes), opened from the main window's File menu
- `dwell_click/` — standalone dwell-click engine, translucent progress overlay, and command-line launcher
- `tests/` — unit tests

### OpenSeeFace webcam provider

The self-contained adapter in [providers/openSeeFace.py](providers/openSeeFace.py)
receives pose data from an
[OpenSeeFace tracker](https://github.com/emilianavt/OpenSeeFace). The tracker,
not OpenHeadMouse, opens the webcam: until it sends packets the status reads
"waiting for ... facetracker". The adapter does not import, install or download
OpenSeeFace, and adds no Python dependencies to this project; it can optionally
launch a tracker you have installed.

1. Obtain OpenSeeFace from its upstream project (on Windows, the release zip
   contains `Binary\facetracker.exe` and its models). Either:
   - let OpenHeadMouse launch it by setting `tracker_command` in
     `config.json`, e.g. `"tracker_command": ["C:/OpenSeeFace/Binary/facetracker.exe"]`
     (or `["C:/path/to/python.exe", "C:/OpenSeeFace/facetracker.py"]`). It is
     started with `-c <camera> -i <udp_host> -p <udp_port>` plus `tracker_args`
     (default `--faces 1`), its output goes to the log, and it is stopped with
     the provider. If it exits, the status shows its exit code and last output; or
   - run it yourself: `.\facetracker.exe -c 0 --faces 1 -i 127.0.0.1 -p 11573`.

   Do not replace this project's dependencies with OpenSeeFace's older pinned
   Python dependencies; run `facetracker.py` in its own environment.
2. In OpenHeadMouse, select **OpenSeeFace (UDP webcam)** and click **Start**.
   Keep mouse control off while checking movement directions, then calibrate
   facing forward before enabling it. Do not run UniFace on the same camera.
3. For a console-only check from this repository's root, run
   `python -m providers.openSeeFace --port 11573 --face-id 0`
   (add `--tracker C:/OpenSeeFace/Binary/facetracker.exe` to launch it too).
   Turn left, look up and tilt the left ear down: yaw, pitch and roll respectively
   should increase. Physical webcam validation is still required.

The `providers.openseeface` settings in `config.json` accept `udp_host`, `udp_port`,
`face_id` (default `0`), `tracking_lost_hold_ms` (default `500`), `tracker_command`
(default `[]`, i.e. not launched), `camera` (default `0`) and `tracker_args`.
An externally run tracker
must send to that address/port and use the selected face ID. Localhost is the
default; this UDP protocol is unauthenticated, so do not expose it to untrusted
networks. No OpenSeeFace binaries or models are bundled with OpenHeadMouse.

Only successful 3D fits generate samples. On startup or after the configured
interval without a fresh valid pose, the provider reports `TRACKING_LOST`; it
resumes on the next valid sample without inventing capture timestamps or held
pose samples. Source timestamps come from OpenSeeFace's post-capture,
pre-inference wall clock. Duplicates and backwards timestamps are discarded;
restart the provider after resetting the sender clock. A socket/bind failure is
reported as an error, while malformed datagrams are logged and discarded.
Each provider gets an independent mouse profile, initially copied from the active
profile when no saved profile exists.

### Seeed XIAO nRF52840 Sense provider

The XIAO Sense provider uses the onboard LSM6DS3TR-C IMU and communicates with
the board over Bluetooth LE. Upload the Arduino sketch and install the board
libraries using [`providers/xiao_ble/README.md`](./providers/xiao_ble/README.md),
then select **XIAO nRF52840 Sense (BLE)**. The default orientation assumes the
sensor axes already align with the canonical head frame; configure
`providers.xiao_ble.device_to_head` in `config.json` for the board's actual
mounting. This is a six-axis sensor without a magnetometer, so yaw is relative
and drifts over time. Check its signs with
`python test_orientation.py --provider xiao_ble --duration 20` before enabling
mouse control.

### Sony headphone provider

The separate `providers/sony_provider.py` adapter implements `BaseProvider` and
emits the same canonical `ProviderSample` quaternion schema as other inputs.
It receives **v2 JSON telemetry**, not OpenTrack's binary packets, from
[Sony Head Tracker](https://github.com/NicholasSlattery/sony-head-tracker).
Sony Head Tracker **v2.2.0** is a pinned native dependency, not a pip package.
Source setup (`python tools/prepare_release.py --assets`) downloads and verifies
the platform's release archive, executable and licence checksums. First Sony use
also installs it if missing (internet required). Windows and macOS full and
HeadMouse-only executables bundle the bridge and its MIT licence; packaged apps
never download it at runtime. The verified archive is bundled and extracted on
use, preventing PyInstaller from rewriting signed/checksummed native bytes.
Linux has no native Sony acquisition backend.

Upstream reports support for WH/WF-1000XM5, WH/WF-1000XM6, ULT WEAR and LinkBuds S;
compatibility depends on the headset exposing Android Head Tracker HID, firmware
and OS Bluetooth support. XM4/XM3 are not supported. The bridge supports Windows
11 and macOS 14+; Linux hardware acquisition is not provided. These are upstream
compatibility reports, not hardware validation of this adapter.

1. Pair and connect your headphones. Close any independently running Sony Head
   Tracker instance so it does not compete for the sensor. On macOS, allow Input
   Monitoring for the bridge when prompted.
2. Select **Sony headphones (Sony Head Tracker)** and click **Start**, initially
   with mouse control and dwell clicking off. OpenHeadMouse launches the bridge
   with XYZ axes, no inversions and smoothing `1`, then waits for a valid v2 JSON
   sample before activating tracking or starting calibration. Stop terminates
   only the bridge process launched by this run.
3. **On Windows 11, particularly after a fresh boot**, Bluetooth audio pairing
   may not expose the Android Head Tracker sensor. If the managed bridge reports
   that no tracker was found, OpenHeadMouse offers **Repair and retry**. Connect
   and power on the headphones, accept the offer, then approve the Windows
   administrator prompt. The bundled `repair --no-launch` command recreates the
   Bluetooth HID sensor/driver binding without opening the native GUI. It may
   briefly interrupt the headphones and closes other Sony Head Tracker windows.
   OpenHeadMouse itself does not run as administrator; repair is never performed
   without approval. Connection is retried once after a successful repair.
   Cancelling either prompt stops startup; press Start to try again.
   During repair, allow it to finish before stopping: OpenHeadMouse must not kill
   the wrapper and leave its elevated driver-repair process running unattended.
4. Other failures (or no valid data within **15 seconds** after bridge launch/retry)
   show a short connection message with diagnostics under **Show Details**.
   Start becomes available again. Repair is not offered for socket conflicts,
   missing dependencies, a stalled stream, or macOS/external bridges.
5. Check `python test_orientation.py --provider sony --duration 20`:
   left turn, upward nod and left-ear-down tilt must give positive yaw, pitch
   and roll. Then use OpenHeadMouse's calibration/recenter controls.

**Externally managed bridge (advanced):** set `auto_start_bridge` to `false` in
`providers.sony` to retain manual bridge control. Use **XYZ axes with no
inversions**, not the bridge's YXZ/inverted defaults. Run one instance:

```powershell
.\sony-head-tracker.exe bridge --port 4242 --axis-map XYZ --invert none --smoothing 1
```

On macOS:

```bash
sony-head-tracker-macos bridge --port 4242 --axis-map XYZ --invert none --smoothing 1
```

Alternatively configure those axes, inversions and smoothing in the native
bridge GUI. Smoothing `1` avoids double filtering; OpenHeadMouse applies its
own filter. The JSON stream does not advertise axis settings, so the adapter
cannot detect a misconfigured bridge.

`providers.sony` in `config.json` accepts `udp_host` (default `127.0.0.1`),
`udp_port` (default **4243**, the bridge's `--port + 1`),
`tracking_lost_hold_ms` (default `500`), `auto_start_bridge` (default `true`) and
`startup_timeout_s` (default `15`, positive finite seconds). The managed bridge
requires loopback host `127.0.0.1` and JSON port `2..65535`, and automatically uses
base port `udp_port - 1`. For an external bridge, update the receiver's JSON port
if you change its base port. Each provider has an independent mouse profile,
copied from the active profile when none has been saved. Existing user configs
without a `sony` section use the defaults. In manual mode, Stop closes only the
receiver, not the external bridge.

**Timing limitation:** the startup log about monotonic receive time is
informational, not a connection confirmation. JSON v2 contains no capture/device
timestamp. With this explicit exception to the provider timing contract, `SonyProvider` timestamps
each datagram immediately after socket receipt using `time.monotonic()`, and
sets `raw_metadata.timestamp_source` to `host_monotonic_receive`. UDP/OS jitter
therefore affects velocity estimates; these are not hardware-timed samples.
Neither `packetsPerSecond` nor `receiveLatencyMs` is used to invent a clock.
Native timestamps would require an upstream protocol extension.

Malformed/unsupported packets are logged and discarded. Waiting or loss of valid
samples for the configured interval reports `TRACKING_LOST` without emitting
held poses; valid packets resume tracking. Bind/socket errors appear in status
and logs. Keep the stream on loopback: UDP is unauthenticated. Do not recenter or
change axes in the bridge during an OpenHeadMouse run; stop, configure the
bridge, then restart and recalibrate to avoid reference-frame jumps.

## Release editions

Release builds prepare four editions. Tagged builds create a **draft** release;
publishing it requires manual test and redistribution approval.

- **Full** — OpenHeadMouse, the Fitts' Law test suite, and the session viewer.
- **HeadMouse-only** — OpenHeadMouse without the Fitts' Law test suite or session viewer. Their
  File-menu items are omitted; head-movement recording and its folder setting remain available.
- **Dwell Click** — the standalone dwell-click tray application, without the OpenHeadMouse interface.
- **Session Viewer** — standalone head-movement and Fitts' Law playback, without starting
  head tracking or mouse control.

The Dwell Click edition starts its tray application when launched. Its settings and command-line
options are the same as the standalone `python -m dwell_click` mode in the [dwell-click guide](docs/dwell-click.md).
Windows executables for all four editions are built without a console window. The Fitts'
Law suite and session viewer run within the full edition without opening a separate terminal.
This applies to newly built executables; existing downloads must be rebuilt or replaced.

Use the [release build script](docs/release-building.md) for consistent Windows packaging. Root-level
generated spec files are ignored and may contain stale local options; running
`pyinstaller main.py` directly can produce a console application.

### Building Windows executables and installers locally

See the [Windows release-building guide](docs/release-building.md) for dependencies, commands, outputs and redistribution checks.

## Setup (running from source)

Python 3.12 is the release/CI baseline. PySide6 is kept on the validated 6.11.x
series. Native crashes exposed a synchronised-window ownership cycle during
preparation; that lifecycle is now regression-tested. Newer Qt series need their
own validation before changing the Windows release lock.
Use a fresh virtual environment rather than modifying a working installation.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\prepare_release.py --assets
.\.venv\Scripts\python.exe main.py
```

macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/prepare_release.py --assets
python main.py
```

Network access is needed for dependency/model setup, not routine desktop tracking
once the assets are installed. For development, install `requirements-dev.txt`.

### Privacy and diagnostics

Desktop camera processing is local. Recordings contain timestamps, head orientation,
cursor trajectories and any study notes/labels you enter; treat them as potentially
sensitive. Do not commit participant recordings, ZIPs or notebook outputs.
Colab uploads go to Google's runtime: see the [notebook privacy notes](colab_session_viewer/README.md).

Frozen Windows applications write diagnostics to
`%LOCALAPPDATA%\OpenHeadMouse\logs`; macOS uses `~/Library/Logs/OpenHeadMouse`
and Linux uses `$XDG_STATE_HOME/OpenHeadMouse/logs` (or `~/.local/state/...`).
The latest log and one previous run are retained per edition. Logs can include paths
and exception details: inspect/redact them before attaching them to an issue.
Source launches keep their usual terminal output.

## MODULE NOTES:
### Standalone session viewer

From the repository root, with the dependencies installed, run:

```powershell
python -m session_viewer
```

The viewer opens without a recording loaded. Use its File menu to open a head-movement
session (JSON or CSV), a Fitts test, or synchronised head/Fitts playback. It does not start
OpenHeadMouse, connect to a tracker, or control the cursor. The main application's File-menu
viewer continues to work as before.

Opening **Open Synchronised Playback...** automatically tiles the head viewer on the left
and Fitts playback on the right. Both windows have **File > Tile Windows Side by Side** to
restore this layout after moving or maximising them. The command uses the available area
of the screen containing the window where it was selected, moving both windows onto that
screen. It is disabled when the other playback window is closed.

For a development-only standalone viewer executable:

```powershell
python -c "from session_viewer.mesh_visualizer import download_mediapipe_model; download_mediapipe_model()"
python -m PyInstaller --noconfirm SessionViewer.spec
```

The versioned viewer spec is the exception to ignored, locally generated root specs.
For release deliverables, use the Windows build script so notices and smoke tests are included.
On Windows the spec creates `dist\SessionViewer.exe` without a terminal window. The face mesh
is bundled, so no model download is needed at runtime. Tagged releases also package the
viewer as a Windows installer, Linux executable/`.deb`, and macOS executable/`.app`/DMG.

Head meshes start in a front-on view: zero yaw, pitch and roll faces straight
out of the screen. In desktop and Colab playback, **Show relative to reference
pose** is checked by default. Uncheck it to see the **recorded pose**, before
removing the reference pose. This changes only the mesh and its angle labels,
not graphs, statistics, or exports. The reference source is shown above the mesh:
saved recentering takes priority over calibration; without either, initial rest
or up to the first 30 samples within one second are used.
Pitch is red, yaw green and roll blue in all viewer angle plots and meshes.
Pitch and yaw values follow their axis tips. Dragging the playback view changes
only the viewing angle.

### Colab session viewer

The notebook's **Each hold opening (s)** control defaults to **1 second**,
applied independently to initial rest and every left/right/up/down hold.
Initial-position summaries use only the labelled rest hold, never the preceding
move-to-rest phase. Excluded hold samples are omitted from held means/SDs,
offsets, exercise ranges/SDs and recording movement statistics, and shaded grey
on head charts. Move phases, saved calibration and raw playback are unchanged.
The separate Head movement stats table covers all recording phases, apart from
its selected recording-opening, hold-opening and target-interval exclusions.
Its **Skip opening of head recording (s)** control independently defaults to
**1 second**, excluding startup samples from ranges, speed and acceleration
without changing the original reference pose. **Exclusions** is at the top of
the viewer. Both time controls sit together to the right of a single target-count
box that applies to all participants/files, with a label above each input.
Excluded targets' time intervals are also omitted from head statistics.
Embedded initial-pose/session statistics use the same settings as the tables;
excluded chart periods have grey background shading while traces keep their
original colours and all data remain visible.
Below Fitts stats, **Show matched-target comparison** adds an optional second
table. Expand **Fitts calculation review** to choose strict planned-ID matching
(same task type/width, within 0.1 bits) or **Closest target conditions (retain
every test)**. The latter maximises equal counts within shared block/amplitude/
width conditions and selects nearest IDs within balanced test quotas, without
an ID cutoff. Global target exclusions apply first; remaining ID differences
and per-test contributions are reported.
The calculation review also offers optional **MTnodwell** and no-dwell
throughput in the Fitts table, retaining original timing results. Cursor/head
movement, settling estimates and counts have a separate **Dwell behaviour stats**
table (including session-level and matched views), with **-** for inapplicable
non-dwell fields rather than zero. Individual imports and ZIP
review rows accept a selection method and configured dwell duration; manifests
preserve these as `selection_method` and `dwell_time_s` (seconds). Unknown methods
are not guessed. See the [dwell analysis notes](docs/colab-analysis.md#dwell-timing-and-movement-estimates)
for definitions, contributing counts and trajectory/timer limitations.
The existing difficulty column is labelled **Effective ID (IDe)**: measured
click accuracy can make it differ even when planned ID matches. Calculation
notes explain why the legacy pooled formulas are not sequence-level ISO estimates.
To audit formulas, sample selection and aggregation, start with the
[calculation source guide](docs/colab-analysis.md#where-to-inspect-the-calculations).
It links the shared Python calculation functions and browser live-path equivalent;
their docstrings document units, formulas, exclusions and edge cases.
The main Fitts table also shows average planned ID immediately before IDe.
A collapsed **Fitts test conditions by file** table is the first item under
Fitts stats, with one row per participant/Fitts file showing saved width/amplitude
lists, block types and observed target/repetition/trial counts, plus a CSV download.
Expand **Individual sessions** below each main summary table to inspect a separate
session-level table, identified by participant and session. These sections start collapsed.

Open [session_viewer.ipynb](colab_session_viewer/session_viewer.ipynb) in Google Colab
and run its setup and viewer cells. Import individual head/Fitts recordings or
multiple ZIP data sets, with or without a participant/session manifest. ZIPs without
a manifest offer timestamp-based head/Fitts pairing, editable labels and a
downloadable manifest for reuse. The single viewer
follows the import block and switches between participants and sessions. Six
tables below it summarize sessions, head-only initial positions (with participant
wireframes), task-only range of movement, whole-session head movement, Fitts
targets/hits and Fitts performance, with one-decimal display and CSV export. Each participant can
exclude the first N targets **per Fitts file** from the summary without changing
playback or removing whole tests. See the [notebook guide](colab_session_viewer/README.md)
for setup, batch import format and data-privacy notes, and the
[analysis guide](docs/colab-analysis.md) for aggregation definitions.

### Dwell click

Tick **Dwell Click** in the Subsystems panel to click when the pointer remains
within the configured tolerance for the dwell time. It starts only while an input
provider controls the cursor, and stops when mouse control is paused or suspended.
For settings, standalone use, the context menu, snap, zoom and platform limits,
see the [dwell-click guide](docs/dwell-click.md).

## GENERAL NOTES:
### Coordinate frame and gain signs

All providers emit quaternions in one canonical head frame (+X forward, +Y left, +Z up), so
for every provider turning left gives **positive yaw**, looking up gives **positive pitch** and
tilting the left ear down gives **positive roll**. Cursor mapping uses the same signs in
absolute and velocity mode: **positive gains make the cursor follow the head** (turn left →
cursor left, look up → cursor up). A negative gain inverts that axis. If a provider needs
negative gains to feel natural, its frame conversion is wrong. See
[docs/coordinate-frames.md](docs/coordinate-frames.md) for the per-provider conversions and a checklist
for adding a new provider.

Main-window labels are kept short and related options sit side by side; hover over any
option to see a fuller explanation. The main window uses two columns for dwell and DSP
settings, with scrollbars on smaller screens so enabling every option does not push the
bottom controls off-screen. Provider controls and Recenter remain outside the scrolling area.

The DSP configuration supports absolute mapping and velocity mapping. The optional One Euro
filter applies adaptive smoothing in either mode. The main window shows **DSP** settings only
while **Mouse Control** is ticked; hiding the settings preserves their values. Velocity mode
also supports deadband/gamma
shaping, stationary auto-zero bias, and optional yaw-drift correction. Cursor clamping
follows the display that contains the OpenHeadMouse window, including displays with negative
coordinate origins. On Windows, native pixel bounds are used so display scaling does not
shrink the usable cursor area. The bounds are refreshed and the cursor is centered on that
display whenever tracking starts.

On Windows, head-driven movement uses absolute mouse-input events across the virtual
desktop rather than only repositioning the cursor. This allows Windows to restore a pointer
suppressed after touch/pen input, independently of whether **Dwell Click** is enabled.
Blocked input pauses tracking and displays a permissions warning.

The real Windows pointer is separate from the dwell ring, zoom box and click-menu overlays.
Those overlays can be covered by taskbar previews or privileged windows (see [UIAccess](docs/dwell-click.md#uiaccess-windows));
UIAccess is not a general fix for an invisible native pointer. If pointer movement or clicking
is blocked over an application running as administrator, OpenHeadMouse needs matching permissions
or an appropriately signed/installed UIAccess build. Secure-desktop prompts remain out of scope.

### Recordings folder

Everything this app records is written under one base folder, so a study's data stays
together:

```
<recordings folder>/sessions/        head-movement session files
<recordings folder>/fitts_sessions/  Fitts' Law result files
```

Set it from the main OpenHeadMouse window with **File → Set Recordings Folder...**; the folder in
use is shown on the **Saving to:** line just below it in the File menu. The chooser opens at the
current folder, and the new folder applies straight away, including to a session that is
already being recorded. The choice is saved in `config.json` as `recorder.base_dir` and is shared
with the Fitts' Law test window.

Before anything is recorded, the folder and its `sessions/` and `fitts_sessions/` subfolders are
created if missing and checked to be writable. This happens when **Recording** is switched on,
when a provider is started with **Recording** on, and before every Fitts test (its results are
always saved). If no folder has been chosen, or the chosen one can't be used (for example a
removed drive or no write permission), you are asked for another one. Cancelling refuses to
record rather than writing to a default folder: Recording is switched back off, tracking with
Recording on is not started, or the Fitts test is not started, with a message saying why. If a
head session still can't be saved when tracking stops, you are asked for another folder; it
is only discarded if you confirm that.

The Fitts window run standalone (`python fitts_law/fitts_test.py`) does the same checks itself
and saves the chosen folder to `config.json` and its own settings file, so it is only asked
once either way. Source runs use `fitts_law/fitts_settings.json`; packaged editions use
per-user application data so settings survive upgrades and work in read-only installations.

### Session files

See the [recording format reference](docs/recording-formats.md) for head
JSON/CSV and Fitts JSON fields, clocks, and the ZIP assignment manifest.

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
are preserved until a preset is selected. See [Coordinate Frame Conventions](docs/coordinate-frames.md)
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
  **Initial Calibration** and **Recording** settings of the main OpenHeadMouse window. It turns
  **Recording** on so the head session is saved. The head session and the Fitts session can then
  be replayed together with **Open Synchronised Playback…**.
- **Start Test** runs the test with whatever is already driving the OS cursor (head mouse,
  trackpad, eye gaze, switch, etc). The test window itself stays agnostic to the input device.
  With **Record Head Movement** ticked, it also brings the head tracker up but switches
  **Mouse Control** *off*, so head movement is recorded while the access method being measured
  keeps the cursor. This is how head movement is captured during, say, an eye-gaze or switch run.
- **Start with dwell click** adds dwell clicking to the current cursor input for the mouse
  task. When hosted by OpenHeadMouse it reuses the main GUI's dwell timing, tolerance and ring
  settings without changing its enabled checkbox. Standalone runs use the default dwell settings.
  Dwell clicking (including dwell enabled in the main OpenHeadMouse GUI) is suppressed during
  tracker startup and all head exercises; it starts only when the mouse task begins.
  Dwell clicking and OpenHeadMouse stop when the mouse task completes, is cancelled, or the
  window closes. The head recording is saved automatically and retained for playback.
- **Start with OpenHeadMouse** records head movement *and* drives the cursor with
  it. It starts the chosen provider exactly as the setup panel's **Start** button would, ticking
  **Record Head Movement** and switching **Mouse Control** on. If initial calibration is enabled,
  the calibration overlay runs first and the trials only begin once the neutral pose has been
  captured and tracking is live. If the tracker is already running, the test starts straight away.
  It respects the main GUI's dwell-click enabled setting, sets **Access method** to OpenHeadMouse,
  and re-enables head-driven cursor control when the head exercises finish and the mouse task
  begins, including resuming a tracker paused during the exercises. Dwell clicking remains
  suppressed until that transition and then follows the main GUI's enabled setting.
  This start mode is disabled when the Fitts window is run
  standalone (`python fitts_law/fitts_test.py`), where there is no OpenHeadMouse app to drive.

Either way the head recording covers the whole run, so its samples can be labelled by phase (see
below). `metadata.record_head_movement` in the Fitts file records which mode was used.

The **Tasks** dropdown selects **HeadMovement and Mouse Task**, **HeadMovement only**, or
**Mouse Task only**. HeadMovement always includes both tasks below and requests head recording
when hosted by the main app. In standalone runs it records the task timings and cursor
trajectories, but cannot start a head tracker. Head-only runs skip the mouse trials and do not
require valid mouse-task settings.

- **Initial Rest Position (Calibration)** gives the participant the configured move/read time
  to adopt a comfortable neutral pose, then records it for the configured hold time. It uses the
  same neutral-pose calculation as the main OpenHeadMouse calibration. If OpenHeadMouse is running, its
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
saved. When hosted by OpenHeadMouse, reopen the Fitts window from the File menu to inspect the results.
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

- **Open OpenHeadMouse Session…** (`Ctrl+O`) opens a head-movement recording on its own.
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
were not reliably delivered while OpenHeadMouse moved the hidden cursor, so earlier recordings may
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

Install development dependencies and run checks from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --test .\colab_session_viewer\viewer.test.cjs
.\.venv\Scripts\python.exe tools\check_publication.py
```

Tests configure offscreen Qt by default and have a per-test timeout. On Linux, use
`QT_QPA_PLATFORM=xcb xvfb-run -a python -m pytest -q` so OpenGL widgets have a
supported X display/context. GUI launchers enable shared OpenGL contexts before creating
the application, so closing and reopening viewers does not invalidate cached shaders.
CI runs these checks
before packaging. The publication check is pattern-based and not an exhaustive audit.

Camera, xIMU3, AirPods, global hotkey, and native Swift helper behavior require the
corresponding hardware and macOS permissions; the protocol and provider unit tests run
without those devices.

Press **Esc** to pause or resume cursor tracking.

The default global toggle is **Esc** on macOS too; `hotkeys.toggle_tracking` can override it.
Grant Accessibility and Input Monitoring permission to the executable that actually runs the app (usually
the project virtual-environment Python, not just VS Code), then restart OpenHeadMouse.
Zoom additionally needs **Screen Recording** permission; without it, zoom reports an error
instead of allowing a click against an incomplete capture. Menu icons still work, using the
application colour scheme rather than sampling other windows.
If registration is denied, the main window displays the reason instead of silently
claiming the hotkey is active.

## Contributing and support

Use the [bug-report guidance](CONTRIBUTING.md), include your edition, version,
OS and provider, and share only anonymised/reproducible examples.
For potentially sensitive vulnerabilities, follow the [private reporting guidance](SECURITY.md).
