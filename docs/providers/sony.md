# Sony provider

The [Sony adapter](../../providers/sony_provider.py) implements `BaseProvider` and
emits the same canonical `ProviderSample` quaternion schema as other inputs.
It receives **v2 JSON telemetry**, not OpenTrack's binary packets, from
[Sony Head Tracker](https://github.com/NicholasSlattery/sony-head-tracker).
Sony Head Tracker **v2.2.0** is a pinned native dependency, not a pip package.
Source setup (`python tools\prepare_release.py --assets`, from the repository root) downloads and verifies
the platform's release archive, executable and licence checksums. First Sony use
also installs it if missing (internet required). Windows and macOS full and
HeadMouse-only executables bundle the bridge and its MIT licence; packaged apps
never download it at runtime. The verified archive is bundled and extracted on
use, preventing PyInstaller from rewriting signed/checksummed native bytes.
Linux has no native Sony acquisition backend.

## Requirements

Upstream reports support for WH/WF-1000XM5, WH/WF-1000XM6, ULT WEAR and LinkBuds S;
compatibility depends on the headset exposing Android Head Tracker HID, firmware
and OS Bluetooth support. XM4/XM3 are not supported. The bridge supports Windows
11 and macOS 14+; Linux hardware acquisition is not provided. These are upstream
compatibility reports, not hardware validation of this adapter.

## Setup

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

## Configuration

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

## Validation

Run `python test_orientation.py --provider sony --duration 20` from the repository root.
Turn left, look up, and tilt the left ear down: yaw, pitch, and roll should each
increase. See [coordinate frames](../coordinate-frames.md).

## Troubleshooting and limitations

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

