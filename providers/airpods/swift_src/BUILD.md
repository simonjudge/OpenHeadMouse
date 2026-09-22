# Building the AirPods CoreMotion Helper

## Requirements
- macOS 12 or later
- Xcode 14+
- AirPods Pro (2nd generation) or AirPods Max

## Quick Start

1. **Open Xcode and create a new Cocoa App project:**
   - File → New → Project
   - Choose "App" under macOS
   - Product Name: `HeadMouseAirPods`
   - Uncheck "Include SwiftUI"

2. **Replace the generated code:**
   - Delete the default `AppDelegate.swift`
   - Copy `main.swift` from this directory into the Xcode project

3. **Configure Info.plist:**
   - Open Info.plist in Xcode
   - Add these keys:
     ```
     NSMicrophoneUsageDescription: "Required for AirPods Pro motion tracking"
     NSBonjourServices: (empty array, or leave out)
     ```

4. **Build and run:**
   - Cmd+B to build
   - Cmd+R to run
   - You may be prompted for Microphone permission; grant it
   - When running, the app will stream to `127.0.0.1:5005`

5. **In the Python app:**
   - Select "AirPods (coming soon)" in the provider dropdown
   - Hit Start
   - The Python receiver will connect and display real-time telemetry

## Troubleshooting

- **"CoreMotion not available":** Only AirPods Pro 2 or AirPods Max support CoreMotion. Earlier AirPods models won't work.
- **No packets received:** Check that the Swift app is running and Microphone permission is granted.
- **Microphone permission denied:** System Preferences → Security & Privacy → Microphone → Allow HeadMouseAirPods.

## How it Works

1. The Swift helper uses `CMHeadphoneMotionManager` to receive quaternion orientation updates from the AirPods' built-in IMU
2. Each update is serialized as JSON and sent as a UDP packet to `127.0.0.1:5005`
3. The Python `AirPodsProvider` listens on that socket and feeds samples into the normal HeadMouse pipeline
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
