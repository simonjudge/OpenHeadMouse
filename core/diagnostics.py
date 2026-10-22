"""Keep diagnostics from windowed frozen applications available to users."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import traceback


def log_directory() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "OpenHeadMouse" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "OpenHeadMouse"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "OpenHeadMouse" / "logs"


def configure_frozen_diagnostics(edition: str) -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{edition}.log"
    previous = path.with_suffix(".previous.log")
    if path.is_file():
        path.replace(previous)
    stream = path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    print(f"{edition} started at {datetime.now(timezone.utc).isoformat()}")

    def report_exception(kind, value, tb):
        traceback.print_exception(kind, value, tb, file=stream)
        stream.flush()
        if "--smoke-test" in sys.argv:
            return
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, f"{edition} error",
                f"An unexpected error occurred:\n\n{value}\n\nDiagnostics: {path}",
            )
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, f"Startup failed: {value}\n\nDiagnostics: {path}", edition, 0x10
            )

    sys.excepthook = report_exception
    return path
