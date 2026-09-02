"""Central application launcher for the HeadMouse system.

Wires: [provider thread] -> dsp_engine.py (Mode A) -> headmouse.py -> OS
cursor, polled by a QTimer at a fixed rate (default 90Hz, within the spec's
60-120Hz consumer poll-rate guidance), plus the minimal GUI (gui/setup_gui.py
+ gui/headmouse_gui.py + gui/vis_gui.py) for provider start/stop, gain
tuning, recenter, and live telemetry.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
from pathlib import Path

import numpy as np

__version__ = "0.2.0"

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def make_provider(name: str, config: dict):
    """Construct a provider by name from config.json's providers section."""
    provider_config = config.get("providers", {}).get(name, {})

    if name == "uniface":
        from providers.uniface_provider import UniFaceProvider

        return UniFaceProvider(
            camera_index=provider_config.get("camera_index", 0),
            target_fps=provider_config.get("target_fps", 30),
            model_path=provider_config.get("landmarker_model_path", "models/face_landmarker.task"),
            tracking_lost_hold_ms=provider_config.get("tracking_lost_hold_ms", 500.0),
        )
    if name == "mock":
        from providers.mock_provider import MockProvider

        return MockProvider()

    raise ValueError(f"Provider '{name}' is not implemented yet")


class App:
    """Owns the provider -> dsp_engine -> headmouse consumer loop and GUI wiring."""

    def __init__(
        self,
        config: dict,
        poll_hz: float = 90.0,
        auto_start_listeners: bool = True,
        mouse_controller=None,
    ):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        from core import quaternion_math as qm
        from core.dsp_engine import DSPEngine
        from core.headmouse import HeadMouse, ScreenBounds
        from gui.headmouse_gui import HeadMouseWindow

        self._config = config
        self._qm = qm
        self._provider = None
        self._last_target = None

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else None
        width = geometry.width() if geometry else 1920
        height = geometry.height() if geometry else 1080

        self.dsp = DSPEngine.from_config(config["dsp"], screen_width=width, screen_height=height)
        self.headmouse = HeadMouse(
            screen_bounds=ScreenBounds(width=width, height=height),
            corner_region_px=config["screen"]["corner_region_px"],
            corner_failsafe_ms=config["screen"]["corner_failsafe_ms"],
            hotkey=config["hotkeys"]["toggle_tracking"],
            mouse_controller=mouse_controller,
        )

        self.window = HeadMouseWindow(initial_kx=self.dsp.kx, initial_ky=self.dsp.ky)
        self.headmouse.add_state_callback(lambda state: self.window.set_status(state.value))
        self.window.set_status(self.headmouse.state.value)

        self.window.setup_panel.start_requested.connect(self._on_start_requested)
        self.window.setup_panel.stop_requested.connect(self._on_stop_requested)
        self.window.recenter_requested.connect(self._on_recenter)
        self.window.kx_changed.connect(self._on_kx_changed)
        self.window.ky_changed.connect(self._on_ky_changed)

        if auto_start_listeners:
            self.headmouse.start()

        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(int(1000 / poll_hz))

    def show(self) -> None:
        self.window.show()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._provider is not None:
            self._provider.stop()
        self.headmouse.stop()

    def _on_start_requested(self, provider_name: str) -> None:
        self._provider = make_provider(provider_name, self._config)
        self._last_target = None
        self._provider.start()
        self.headmouse.set_tracking_state(True)

    def _on_stop_requested(self) -> None:
        self.headmouse.set_tracking_state(False)
        if self._provider is not None:
            self._provider.stop()
            self._provider = None

    def _on_recenter(self) -> None:
        latest = self._drain_latest()
        if latest is not None:
            self.dsp.recenter(latest.quaternion)
            self._last_target = None

    def _on_kx_changed(self, value: float) -> None:
        self.dsp.kx = value

    def _on_ky_changed(self, value: float) -> None:
        self.dsp.ky = value

    def _drain_latest(self):
        """Pop every currently-queued sample and return only the newest one."""
        if self._provider is None:
            return None
        latest = None
        while True:
            try:
                latest = self._provider.output_queue.get_nowait()
            except queue.Empty:
                break
        return latest

    def _poll(self) -> None:
        sample = self._drain_latest()
        if sample is None:
            return

        yaw_deg, pitch_deg, roll_deg = self.dsp.relative_euler_deg(sample.quaternion)
        self.window.update_telemetry(yaw_deg, pitch_deg, roll_deg)

        target_x, target_y = self.dsp.process_absolute(sample.quaternion)
        if self._last_target is not None:
            delta_x = target_x - self._last_target[0]
            delta_y = target_y - self._last_target[1]
            self.headmouse.process_frame(delta_x, delta_y, sample.timestamp)
        self._last_target = (target_x, target_y)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HeadMouse: head-movement recording and cursor control")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))

    from PySide6.QtWidgets import QApplication

    qt_app = QApplication(sys.argv[:1])
    app = App(config)
    app.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
