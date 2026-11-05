# Release notes

## 0.2.0-rc.1 (in preparation)

First public-release candidate for OpenHeadMouse (formerly HeadMouse).

### Included capabilities

- Webcam/MediaPipe and xIMU3 head tracking; experimental macOS AirPods support.
- Absolute/velocity cursor control, calibration, recording and optional dwell click.
- Fitts-style pointing tasks and synchronised head/cursor session playback.
- Desktop and Colab analysis/viewing tools.
- Full, HeadMouse-only, Dwell Click and Session Viewer editions.
- Optional Snap to context-menu toggle for nearby accessible controls on Windows, macOS and Linux/X11.

### Release preparation

- MIT project licence and explicit third-party redistribution review.
- Safer publication defaults, cleaned notebook outputs and local privacy checks.
- Test-gated draft release builds, verified model assets, checksums and build inventories.
- Windowed executable diagnostics and packaged startup smoke tests.
- Non-owning playback-window links and corrected shared-clock ownership, with
  repeated native destruction regression coverage.
- Portability fixes for scaled X11 cursor bounds, macOS capture permissions and writable
  AirPods helper state, plus standalone controls on desktops without a system tray.
- Persistent packaged Fitts settings, native keyboard shortcuts, Linux runtime dependencies,
  Windows-exclusive AirPods UDP binding and shared OpenGL contexts for repeated viewer use.

### Known limitations

- Windows is the primary release target; clean-machine/hardware sign-off is pending.
- macOS 14+ arm64 and Linux x64 builds are experimental. No universal/Intel macOS
  builds or Wayland global-input support are claimed.
- Downloads are unsigned; macOS bundles are not notarized.
- Single-file binary publication is deferred until the LGPL/model review is complete.
- Python 3.12 / Qt 6.11.x is the release baseline; newer versions need separate validation.
- Not a certified medical device or a validated clinical assessment.
