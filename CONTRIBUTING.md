# Contributing and reporting problems

Thanks for testing OpenHeadMouse. Start with a release candidate and a physical
mouse or another reliable way to control the computer.

## Bug reports

Use [GitHub issues](https://github.com/simonjudge/OpenHeadMouse/issues).
Include:

- OpenHeadMouse version and edition; installer, portable EXE or source checkout.
- OS version, architecture, provider/device and connection type.
- Steps to reproduce, expected result and actual result.
- Display count, scaling/DPI, permissions and whether the app was elevated.
- A redacted error message/log and a minimal synthetic recording if needed.

Do not attach participant recordings, unredacted study notes, credentials or a
notebook with saved outputs. Diagnostics may include local paths and error details.
For sensitive vulnerabilities, use the [private reporting process](SECURITY.md).

## Development checks

Use Python 3.12 and install `requirements-dev.txt` in a dedicated virtual environment.
Prepare assets with `python tools/prepare_release.py --assets`, then run:

```powershell
python -m pytest -q
node --test .\colab_session_viewer\viewer.test.cjs
python tools\check_publication.py
```

Linux tests need an X display for some input-library imports:
`xvfb-run -a python -m pytest -q`.
Device tests use mocks; passing them does not validate real camera/IMU hardware,
hotkey permissions, OpenGL rendering or packaged installation.

Keep contributions focused, include regression tests and update related guidance.
New code contributed to this project is expected to be available under its MIT
licence; do not copy third-party code/assets without preserving their terms.
