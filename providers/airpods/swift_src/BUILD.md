# AirPods CoreMotion Helper

## Requirements
- macOS 14 or later
- Xcode command-line tools
- AirPods Pro (2nd generation) or AirPods Max

## Source of truth

This directory contains the only Swift helper implementation:

- `main.swift` — menu-bar helper and CoreMotion-to-UDP bridge
- `Package.swift` — SwiftPM build definition
- `Info.plist` — privacy descriptions for signed/app-bundle builds

OpenHeadMouse runs `main.swift` in a minimized Terminal window. Terminal must
have Motion permission because an unsigned or ad-hoc-signed standalone
helper is rejected by macOS TCC without an Apple Development signature.

## Build

From this directory:

```bash
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

## Run manually

From this directory:

```bash
xcrun swift main.swift
```

Keep Terminal open. Stop with `Control-C` or choose Quit from the headphones
menu-bar item.

## Run through OpenHeadMouse

1. Select **AirPods (CoreMotion)**.
2. Press **Start**.
3. OpenHeadMouse opens the helper in a minimized Terminal window and starts the
   Python UDP receiver.
4. Press **Stop** to stop both processes.

## Troubleshooting

- **"CoreMotion not available":** Only AirPods Pro 2 or AirPods Max support CoreMotion. Earlier AirPods models won't work.
- **No packets received:** Check that the helper is running and Microphone permission is granted.
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

Timestamps are in seconds (Unix epoch); quaternions are normalized unit quaternions in `[w, x, y, z]` order.
