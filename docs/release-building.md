# Building Windows executables and installers locally

Install [Inno Setup 6](https://jrsoftware.org/isdl.php) and install the Python dependencies
and PyInstaller into your build environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-py312.lock
.\build-release.ps1 -Version 0.2.0-rc.1
```

Run these commands in PowerShell from the project root. If script execution is blocked,
use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1 -Version 0.2.0-rc.1`.
The script builds all four editions without console windows, then compiles their installers.
Executables and `*-setup.exe` installers are written to `dist`. Generated specs and Inno Setup
definitions go under `build/local-release`, leaving existing root-level specs untouched.
The script downloads missing pinned model assets, verifies their SHA-256 hashes,
exports dependency versions and discovered licence files, and smoke-tests each EXE
without starting tracking or clicking. It also writes checksums and a notices ZIP.

By default, the script uses `.venv\Scripts\python.exe` if present, otherwise `python` on PATH.
Use `-Python "C:\path\to\python.exe"` or `-Iscc "C:\path\to\ISCC.exe"` to override the tools.
It does not install Python dependencies. Installers are unsigned. Single-file binary
publication is deferred pending the [LGPL/model review](../THIRD_PARTY_NOTICES.md).

