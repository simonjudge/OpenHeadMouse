# AirPods provider

The [AirPods adapter](../../providers/airpods_provider.py) receives head-pose
JSON from the macOS Swift CoreMotion helper.

## Requirements

- macOS 14 or later
- Xcode command-line tools
- AirPods Pro (2nd generation) or AirPods Max

## Setup

Install Xcode command-line tools if needed with `xcode-select --install`, then
connect and wear compatible AirPods. Use the application source setup or a build
containing the Swift helper.

### Source of truth

[`providers/airpods/swift_src/`](../../providers/airpods/swift_src/) contains
the only Swift helper implementation:

- `main.swift` — menu-bar helper and CoreMotion-to-UDP bridge
- `Package.swift` — SwiftPM build definition
- `Info.plist` — privacy descriptions for signed/app-bundle builds

OpenHeadMouse runs `main.swift` in a minimized Terminal window. Terminal must
have Motion permission because an unsigned or ad-hoc-signed standalone
helper is rejected by macOS TCC without an Apple Development signature.

### Build

From the repository root:

```bash
cd providers/airpods/swift_src
swift build
```

This verifies that the helper compiles and creates:

```text
.build/debug/OpenHeadMouseAirPodsHelper
```

Do not run that raw executable for normal use. macOS may terminate an
ad-hoc-signed executable when it requests Motion access.

OpenHeadMouse executes the source directly, so rebuilding is not required after
editing `main.swift`: pressing Stop and Start loads the latest source.

### Run manually

From `providers/airpods/swift_src/`:

```bash
xcrun swift main.swift
```

The helper defaults to `127.0.0.1:5005`. To match a custom AirPods provider
listener, pass `--host <IPv4-address> --port <1-65535>`, for example:

```bash
xcrun swift main.swift --host 127.0.0.1 --port 5010
```

Keep Terminal open. Stop with `Control-C` or choose Quit from the headphones
menu-bar item.

### Run through OpenHeadMouse

1. Select **AirPods (CoreMotion)**.
2. Press **Start**.
3. OpenHeadMouse opens the helper in a minimized Terminal window and starts the
   Python UDP receiver.
4. Press **Stop** to stop both processes.

## Configuration

Settings are under `providers.airpods` in `config.json`: `udp_host` defaults to
`127.0.0.1`, and `udp_port` defaults to `5005`. The managed helper receives the
same host/port. When manually running it, pass matching `--host` and `--port`
arguments. Mouse gains and filtering live in `mouse_profiles.airpods`.

## Validation

With the helper running in Terminal and the GUI receiver stopped, run from the
repository root:

```bash
python test_orientation.py --provider airpods --duration 20
```

Turn left, look up, and tilt the left ear down: yaw, pitch, and roll should
each increase. See [coordinate frames](../coordinate-frames.md).

## Troubleshooting and limitations

- **"CoreMotion not available":** Only AirPods Pro 2 or AirPods Max support CoreMotion. Earlier AirPods models won't work.
- **No packets received:** Check that the helper is running, Terminal has Motion permission, and the host/port match.
- **Motion permission denied:** System Settings → Privacy & Security → Motion & Fitness → allow Terminal.

## How it Works

1. The Swift helper uses `CMHeadphoneMotionManager` to receive quaternion orientation updates from the AirPods' built-in IMU
2. Each update is serialized as JSON and sent as a UDP packet to `127.0.0.1:5005`
3. The Python `AirPodsProvider` listens on that socket and feeds samples into the normal OpenHeadMouse pipeline
4. Once received, AirPods data is treated identically to UniFace and xIMU3 — no special handling needed

The UDP JSON schema is:
```json
{
    "timestamp": 1234567890.123,
    "quaternion": {"w": 0.707, "x": 0.0, "y": 0.707, "z": 0.0},
    "source": "airpods"
}
```

The current helper writes timestamps in Unix epoch seconds; quaternions are
normalized unit quaternions in `[w, x, y, z]` order. The Python adapter changes
the headphone axes into the canonical head frame. Heading has an arbitrary
reference; this is not an absolute compass.
