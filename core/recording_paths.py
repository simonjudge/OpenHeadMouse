"""The user-chosen base folder that every recording is written under.

Head-movement sessions and Fitts' Law results are kept in their own
subfolders of a single base folder, so one study's data stays together and
can be copied or backed up in one piece:

    <base folder>/sessions/        head-movement session files
    <base folder>/fitts_sessions/  Fitts' Law result files

The choice is stored in config.json under ``recorder.base_dir`` so the
OpenHeadMouse window and the Fitts test (usually launched from it) agree on it.
Whoever asks for the folder first saves it there, so the question is only
asked once per machine even when the Fitts test is run on its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HEAD_SESSIONS_SUBDIR = "sessions"
FITTS_SESSIONS_SUBDIR = "fitts_sessions"


def default_config_path() -> Path:
    # Imported lazily: core/ must not depend on main at module-import time.
    from main import CONFIG_PATH

    return CONFIG_PATH


def head_sessions_dir(base_dir: str | Path) -> Path:
    return Path(base_dir) / HEAD_SESSIONS_SUBDIR


def fitts_sessions_dir(base_dir: str | Path) -> Path:
    return Path(base_dir) / FITTS_SESSIONS_SUBDIR


def base_dir_from_config(config: dict) -> Path | None:
    """The configured base folder, or None if the user hasn't chosen one."""
    raw = (config.get("recorder") or {}).get("base_dir")
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw)).expanduser()


def _read_config(path: Path) -> dict:
    try:
        with open(path) as config_file:
            config = json.load(config_file)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def load_base_dir(config_path: str | Path | None = None) -> Path | None:
    """Read the base folder straight from config.json.

    Used by the Fitts test when it runs standalone, with no OpenHeadMouse window
    to ask. A missing or unreadable config simply means "not chosen yet".
    """
    path = Path(config_path) if config_path is not None else default_config_path()
    return base_dir_from_config(_read_config(path))


def save_base_dir(base_dir: str | Path, config_path: str | Path | None = None) -> bool:
    """Store the chosen base folder in config.json; True when it was written.

    Lets a standalone Fitts test remember the folder in the same place the
    OpenHeadMouse window reads it from, instead of asking again on every run.
    Other settings in the file are preserved.
    """
    path = Path(config_path) if config_path is not None else default_config_path()
    config = _read_config(path)
    recorder = config.get("recorder")
    if not isinstance(recorder, dict):
        recorder = {}
        config["recorder"] = recorder
    recorder["base_dir"] = str(base_dir)

    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temporary_path, "w") as config_file:
            json.dump(config, config_file, indent=2)
            config_file.write("\n")
        temporary_path.replace(path)
    except (OSError, TypeError):
        temporary_path.unlink(missing_ok=True)
        return False
    return True


def prompt_for_base_dir(parent=None, reason: str = "", start_dir=None) -> Path | None:
    """Ask for the base folder; returns None if the user cancelled."""
    from PySide6.QtWidgets import QFileDialog

    title = "Choose a folder for OpenHeadMouse recordings"
    if reason:
        title = f"{title} ({reason})"
    start = Path(start_dir) if start_dir is not None else Path.home()
    while not start.is_dir() and start.parent != start:
        start = start.parent
    chosen = QFileDialog.getExistingDirectory(parent, title, str(start))
    return Path(chosen) if chosen else None


def prepare_base_dir(base_dir: str | Path) -> str | None:
    """Create the base folder and its subfolders and check they are writable.

    Returns None when recordings can be saved there, otherwise the reason
    they can't, so nothing is recorded into a folder that would lose it.
    """
    try:
        for folder in (head_sessions_dir(base_dir), fitts_sessions_dir(base_dir)):
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".headmouse_write_test_{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
    except OSError as exc:
        return str(exc) or exc.__class__.__name__
    return None


def warn_unusable_base_dir(parent, base_dir, problem: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(
        parent,
        "Recordings folder can't be used",
        f"Recordings can't be saved in:\n{base_dir}\n\n{problem}\n\nPlease choose another folder.",
    )


def ensure_usable_base_dir(current, parent=None, reason: str = "") -> Path | None:
    """A base folder that exists and is writable, asking for one if needed.

    `current` is used when it can be prepared; otherwise (unset, missing
    drive, no permission) the user is asked until a usable folder is chosen.
    Returns None only when the user cancels.
    """
    if current is not None:
        problem = prepare_base_dir(current)
        if problem is None:
            return Path(current)
        warn_unusable_base_dir(parent, current, problem)
    while True:
        chosen = prompt_for_base_dir(parent, reason)
        if chosen is None:
            return None
        problem = prepare_base_dir(chosen)
        if problem is None:
            return chosen
        warn_unusable_base_dir(parent, chosen, problem)
