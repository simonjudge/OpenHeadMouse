"""Fitts' Law test settings persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def _settings_path() -> Path:
    """Keep source-run settings beside the package, but persist frozen builds per user."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).parent / "fitts_settings.json"

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser()
    return base / "OpenHeadMouse" / "fitts_settings.json"


_SETTINGS_PATH = _settings_path()


DEFAULT_SETTINGS = {
    "mode": "ISO Standard",
    "num_targets": "13",
    "amplitudes": "300,600",
    "widths": "30,60",
    "repetitions": 2,
    "randomize_targets": False,
    "stimulus": "Circle (ISO standard)",
    "icon": "🎯",
    "typing_key_size": "Consistent",
    "typing_word_count": 8,
    "typing_reassign_widths": False,
    "record_head_movement": False,
    "task_selection": "Mouse Task only",
    "start_mode": "Start test",
    "pretest_move_duration_s": 5.0,
    "pretest_hold_duration_s": 5.0,
    # Mirrors config.json's recorder.base_dir so a standalone run (no
    # OpenHeadMouse window) still only asks for the folder once.
    "recordings_base_dir": None,
}


class FittsSettings:
    """Load/save Fitts test settings to persistent JSON file."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if _SETTINGS_PATH.exists():
            try:
                with open(_SETTINGS_PATH) as f:
                    loaded = json.load(f)
                if loaded.get("start_mode") == "Start with HeadMouse":
                    loaded["start_mode"] = "Start with OpenHeadMouse"
                if "task_selection" not in loaded:
                    loaded["task_selection"] = (
                        "HeadMovement and Mouse Task"
                        if loaded.get("initial_rest_enabled")
                        or loaded.get("range_of_movement_enabled")
                        else "Mouse Task only"
                    )
                # Merge with defaults to handle new settings added later
                return {**DEFAULT_SETTINGS, **loaded}
            except (json.JSONDecodeError, IOError):
                return DEFAULT_SETTINGS.copy()
        return DEFAULT_SETTINGS.copy()

    def save(self) -> None:
        """Write current settings to disk."""
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(self.data, f, indent=2)

    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        """Set a setting value."""
        self.data[key] = value
