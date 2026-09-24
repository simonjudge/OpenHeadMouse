"""Central application launcher for the HeadMouse system.

Wires: [provider thread] -> dsp_engine.py -> headmouse.py -> OS
cursor, polled by a QTimer at a fixed rate (default 90Hz, within the spec's
60-120Hz consumer poll-rate guidance), plus the minimal GUI (gui/setup_gui.py
+ gui/headmouse_gui.py + gui/vis_gui.py) for provider start/stop, gain
tuning, recenter, and live telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

__version__ = "0.2.0"

CONFIG_PATH = Path(__file__).parent / "config.json"
AIRPODS_HELPER_DIR = Path(__file__).parent / "providers" / "airpods" / "swift_src"
AIRPODS_HELPER_PID_PATH = AIRPODS_HELPER_DIR / ".build" / "airpods-helper.pid"
AIRPODS_HELPER_LOG_PATH = AIRPODS_HELPER_DIR / ".build" / "airpods-helper.log"


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
    if name == "ximu3":
        from providers.ximu3_provider import Ximu3Provider

        return Ximu3Provider(
            connection_type=provider_config.get("connection_type", "usb"),
            port_name=provider_config.get("port_name", ""),
            wifi_ip_address=provider_config.get("wifi_ip_address", "192.168.1.1"),
            wifi_port_send=provider_config.get("wifi_port_send", 9000),
            wifi_port_receive=provider_config.get("wifi_port_receive", 8001),
            axes_alignment=provider_config.get("axes_alignment", "+X-Z+Y"),
            ahrs_axes_convention=provider_config.get("ahrs_axes_convention", 2),
            ahrs_ignore_magnetometer=provider_config.get("ahrs_ignore_magnetometer", False),
            target_rate_hz=provider_config.get("target_rate_hz", 200),
        )
    if name == "airpods":
        from providers.airpods.airpods_provider import AirPodsProvider

        return AirPodsProvider(
            udp_host=provider_config.get("udp_host", "127.0.0.1"),
            udp_port=provider_config.get("udp_port", 5005),
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
        config_path: Path | None = None,
    ):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        from core import quaternion_math as qm
        from core.dsp_engine import DSPEngine
        from core.headmouse import HeadMouse, ScreenBounds
        from gui.headmouse_gui import HeadMouseWindow

        self._config = config
        self._config_path = Path(config_path) if config_path is not None else None
        self._qm = qm
        self._provider = None
        self._airpods_helper_pid: int | None = None
        self._playback_session = None  # for loading/replaying recorded sessions
        self._last_target = None
        self._last_sample = None
        self._mouse_was_active = False
        self._session_viewer_window = None  # keep a reference so Qt/PySide doesn't GC it
        self._fitts_test_window = None  # keep a reference so Qt/PySide doesn't GC it

        from core.recorder import Recorder

        self._recorder = Recorder(
            output_dir=config["recorder"]["output_dir"],
            format=config["recorder"]["format"],
        )

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else None
        width = geometry.width() if geometry else 1920
        height = geometry.height() if geometry else 1080
        origin_x = geometry.left() if geometry else 0
        origin_y = geometry.top() if geometry else 0
        print(f"[Screen] Initial display: {width}x{height} at ({origin_x}, {origin_y})")

        self.dsp = DSPEngine.from_config(
            config["dsp"], screen_width=width, screen_height=height
        )
        self.dsp.set_screen_geometry(width, height, origin_x, origin_y)
        self.headmouse = HeadMouse(
            screen_bounds=ScreenBounds(width=width, height=height, origin_x=origin_x, origin_y=origin_y),
            corner_region_px=config["screen"]["corner_region_px"],
            corner_failsafe_ms=config["screen"]["corner_failsafe_ms"],
            hotkey=config["hotkeys"]["toggle_tracking"],
            mouse_controller=mouse_controller,
        )

        self.window = HeadMouseWindow(
            initial_kx=self.dsp.kx,
            initial_ky=self.dsp.ky,
            hotkey=config["hotkeys"]["toggle_tracking"],
            initial_dsp_config=config["dsp"],
            initial_provider=config.get("active_provider", "uniface"),
            initial_ui_config=config.get("ui", {}),
        )
        self.headmouse.add_state_callback(lambda state: self.window.set_status(state.value))
        self.window.set_status(self.headmouse.state.value)

        self.window.setup_panel.start_requested.connect(self._on_start_requested)
        self.window.setup_panel.stop_requested.connect(self._on_stop_requested)
        self.window.setup_panel.provider_changed.connect(self._on_provider_changed)
        self.window.setup_panel.ximu3_ignore_magnetometer_toggled.connect(
            self._on_ximu3_ignore_magnetometer_toggled
        )
        self.window.setup_panel.set_ximu3_ignore_magnetometer(
            config.get("providers", {}).get("ximu3", {}).get("ahrs_ignore_magnetometer", False)
        )
        self.window.recenter_requested.connect(self._on_recenter)
        self.window.kx_changed.connect(self._on_kx_changed)
        self.window.ky_changed.connect(self._on_ky_changed)
        self.window.dsp_config_changed.connect(self._on_dsp_config_changed)
        self.window.load_session_requested.connect(self._on_load_session_requested)
        self.window.fitts_test_requested.connect(self._on_fitts_test_requested)
        self.window.controls.recording_toggled.connect(self._on_recording_toggled)
        self.window.controls.mouse_control_toggled.connect(self._on_mouse_control_toggled)

        if auto_start_listeners:
            self.headmouse.start()
            if self.headmouse.hotkey_error:
                self.window.set_hotkey_status(
                    f"Hotkey unavailable: {self.headmouse.hotkey_error}"
                )

        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(int(1000 / poll_hz))

    def show(self) -> None:
        self.window.show()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._provider is not None:
            self._provider.stop()
            self._provider = None
        self._stop_airpods_helper()
        self.headmouse.stop()

    def _on_start_requested(self, provider_name: str) -> None:
        try:
            # Special handling for xIMU3: show device discovery dialog
            if provider_name == "ximu3":
                from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QMessageBox
                from providers.ximu3_provider import discover_ximu3_devices

                # Discover available devices
                devices = discover_ximu3_devices()

                if not devices:
                    QMessageBox.warning(self.window, "No Devices Found", "No xIMU3 devices were found.\n\nMake sure:\n- USB device is connected, or\n- WiFi AP mode is enabled and in range")
                    self.window.setup_panel.reset_to_stopped()
                    return

                # Show device selection dialog
                dialog = QDialog(self.window)
                dialog.setWindowTitle("Select xIMU3 Device")
                layout = QVBoxLayout(dialog)
                layout.addWidget(QLabel("Select xIMU3 device:"))

                combo = QComboBox()
                for i, device in enumerate(devices):
                    combo.addItem(device.name, userData=device)
                layout.addWidget(combo)

                ok_button = QPushButton("Connect")
                cancel_button = QPushButton("Cancel")
                layout.addWidget(ok_button)
                layout.addWidget(cancel_button)

                selected_device = None

                def on_ok():
                    nonlocal selected_device
                    selected_device = combo.currentData()
                    dialog.accept()

                ok_button.clicked.connect(on_ok)
                cancel_button.clicked.connect(dialog.reject)

                if dialog.exec() != QDialog.Accepted or selected_device is None:
                    self.window.setup_panel.reset_to_stopped()
                    return

                # Create provider with selected device
                from providers.ximu3_provider import Ximu3Provider
                self._provider = Ximu3Provider(
                    connection_type=selected_device.connection_type,
                    port_name=selected_device.port_name,
                    wifi_ip_address=selected_device.ip_address,
                    wifi_port_send=9000,  # Standard xIMU3 WiFi send port
                    wifi_port_receive=8001,  # Standard xIMU3 WiFi receive port
                    axes_alignment=self._config.get("providers", {}).get("ximu3", {}).get("axes_alignment", "+X-Z+Y"),
                    ahrs_axes_convention=self._config.get("providers", {}).get("ximu3", {}).get("ahrs_axes_convention", 2),
                    ahrs_ignore_magnetometer=self.window.setup_panel.ximu3_ignore_magnetometer_enabled,
                    target_rate_hz=self._config.get("providers", {}).get("ximu3", {}).get("target_rate_hz", 200),
                )
            else:
                self._provider = make_provider(provider_name, self._config)
                if provider_name == "airpods":
                    self._start_airpods_helper()

            self._use_window_screen()
            self.dsp.reset(position=self.headmouse.cursor_position)
            self._last_sample = None
            self._mouse_was_active = False
            self._last_target = None
            self._provider.start()
            self.headmouse.set_tracking_state(True)

        except Exception as e:
            # Reset UI state and show error
            self._stop_airpods_helper()
            self._provider = None
            self.window.setup_panel.reset_to_stopped()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.window,
                "Connection Failed",
                f"Failed to connect to provider:\n\n{str(e)}"
            )

    def _use_window_screen(self) -> None:
        """Use and center on the display that currently contains the app window."""
        from PySide6.QtGui import QGuiApplication
        from core.headmouse import ScreenBounds

        window_handle = self.window.windowHandle()
        screen = window_handle.screen() if window_handle is not None else None
        if screen is None:
            screen = QGuiApplication.screenAt(self.window.frameGeometry().center())
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("Could not determine the display containing the HeadMouse window")

        geometry = screen.geometry()
        width, height = geometry.width(), geometry.height()
        origin_x, origin_y = geometry.left(), geometry.top()
        print(f"[Screen] Tracking display: {width}x{height} at ({origin_x}, {origin_y})")

        self.dsp.set_screen_geometry(width, height, origin_x, origin_y)
        self.headmouse.set_screen_bounds(
            ScreenBounds(
                width=width,
                height=height,
                origin_x=origin_x,
                origin_y=origin_y,
            )
        )
        self.headmouse.center_cursor()

    def _on_stop_requested(self) -> None:
        self.headmouse.set_tracking_state(False)
        if self._provider is not None:
            self._provider.stop()
            self._provider = None
        self._stop_airpods_helper()
        # Save the recording if anything was recorded
        if self._recorder.has_samples:
            filepath = self._recorder.save()
            print(f"Session saved to {filepath}")

    def _airpods_terminal_script(self) -> str:
        if sys.platform != "darwin":
            raise RuntimeError("AirPods CoreMotion is only available on macOS")

        shell_command = (
            f"cd {shlex.quote(str(AIRPODS_HELPER_DIR))}; "
            "mkdir -p .build; "
            "/usr/bin/xcrun swift main.swift >> .build/airpods-helper.log 2>&1 & "
            "helper_pid=$!; "
            "echo $helper_pid > .build/airpods-helper.pid; "
            "wait $helper_pid; "
            "exit"
        )
        escaped_command = shell_command.replace("\\", "\\\\").replace('"', '\\"')
        return (
            'tell application "Terminal"\n'
            f'    do script "{escaped_command}"\n'
            "    delay 1\n"
            "    try\n"
            "        set miniaturized of front window to true\n"
            "    end try\n"
            "end tell"
        )

    def _start_airpods_helper(self) -> None:
        if self._airpods_helper_pid is not None:
            try:
                os.kill(self._airpods_helper_pid, 0)
                return
            except ProcessLookupError:
                self._airpods_helper_pid = None

        AIRPODS_HELPER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)
        AIRPODS_HELPER_LOG_PATH.write_text("")
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", self._airpods_terminal_script()],
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError(
                "Could not launch the AirPods helper in Terminal. Install the Xcode "
                "command-line tools and allow Terminal to access Motion data."
            ) from e

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not AIRPODS_HELPER_PID_PATH.exists():
            time.sleep(0.05)
        if not AIRPODS_HELPER_PID_PATH.exists():
            raise RuntimeError("AirPods helper did not report its process ID")

        try:
            helper_pid = int(AIRPODS_HELPER_PID_PATH.read_text().strip())
            os.kill(helper_pid, 0)
        except (ValueError, ProcessLookupError) as e:
            log = AIRPODS_HELPER_LOG_PATH.read_text().strip()
            raise RuntimeError(f"AirPods helper exited during startup.\n\n{log}") from e
        self._airpods_helper_pid = helper_pid

    def _stop_airpods_helper(self) -> None:
        helper_pid = self._airpods_helper_pid
        self._airpods_helper_pid = None
        if helper_pid is None:
            return

        try:
            os.kill(helper_pid, signal.SIGTERM)
        except ProcessLookupError:
            AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)
            return

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(helper_pid, 0)
            except ProcessLookupError:
                AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)
                return
            time.sleep(0.05)

        try:
            os.kill(helper_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)

    def _on_ximu3_ignore_magnetometer_toggled(self, enabled: bool) -> None:
        from providers.ximu3_provider import Ximu3Provider

        self._config.setdefault("providers", {}).setdefault("ximu3", {})[
            "ahrs_ignore_magnetometer"
        ] = enabled
        self._persist_config()
        if not isinstance(self._provider, Ximu3Provider):
            return
        try:
            self._provider.set_ahrs_ignore_magnetometer(enabled)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.window,
                "AHRS Setting Failed",
                f"Failed to set AHRS ignore magnetometer:\n\n{str(e)}",
            )

    def _on_recenter(self) -> None:
        latest = self._drain_latest() or self._last_sample
        if latest is not None:
            self.dsp.recenter(latest.quaternion)
            self._last_target = None
            if self.window.controls.mouse_control_enabled:
                self.headmouse.process_target(
                    self.dsp.origin_x + self.dsp.screen_width / 2.0,
                    self.dsp.origin_y + self.dsp.screen_height / 2.0,
                    latest.timestamp,
                )
            # Log the recenter event with the quaternion used as reference
            self._recorder.record_event(
                latest.timestamp,
                "recenter",
                {"quaternion": [float(latest.quaternion[i]) for i in range(4)]}
            )
            print(f"✓ Recentered at {latest.timestamp:.3f}s")

    def _on_kx_changed(self, value: float) -> None:
        self.dsp.kx = value
        self._config.setdefault("dsp", {}).setdefault("absolute", {})["kx"] = value
        self._persist_config()

    def _on_ky_changed(self, value: float) -> None:
        self.dsp.ky = value
        self._config.setdefault("dsp", {}).setdefault("absolute", {})["ky"] = value
        self._persist_config()

    def _on_dsp_config_changed(self, values: dict) -> None:
        from dataclasses import replace
        self.dsp.mode = values["mode"]
        self.dsp.velocity_kx = values["velocity_kx"]
        self.dsp.velocity_ky = values["velocity_ky"]
        self.dsp.gamma = values["gamma"]
        self.dsp.deadband_deg_s = values["deadband_deg_s"]
        one_euro = replace(
            self.dsp.one_euro_filter,
            enabled=values["one_euro_enabled"],
            min_cutoff=values["one_euro_min_cutoff"],
            beta=values["one_euro_beta"],
            d_cutoff=values["one_euro_d_cutoff"],
        )
        auto_zero = replace(
            self.dsp.auto_zero,
            enabled=values["auto_zero_enabled"],
        )
        drift = replace(
            self.dsp.yaw_drift_correction,
            enabled=values["yaw_drift_enabled"],
        )
        self.dsp.set_filter_configs(one_euro, auto_zero, drift)
        self.dsp.reset(position=self.headmouse.cursor_position)
        self._last_target = None
        dsp_config = self._config.setdefault("dsp", {})
        dsp_config["mode"] = values["mode"]
        dsp_config.setdefault("velocity", {}).update(
            {
                "kx": values["velocity_kx"],
                "ky": values["velocity_ky"],
                "gamma": values["gamma"],
                "deadband_deg_s": values["deadband_deg_s"],
            }
        )
        dsp_config.setdefault("one_euro_filter", {}).update(
            {
                "enabled": values["one_euro_enabled"],
                "min_cutoff": values["one_euro_min_cutoff"],
                "beta": values["one_euro_beta"],
                "d_cutoff": values["one_euro_d_cutoff"],
            }
        )
        dsp_config.setdefault("auto_zero", {})["enabled"] = values["auto_zero_enabled"]
        dsp_config.setdefault("yaw_drift_correction", {})["enabled"] = values[
            "yaw_drift_enabled"
        ]
        self._persist_config()

    def _on_provider_changed(self, provider_name: str) -> None:
        self._config["active_provider"] = provider_name
        self._persist_config()

    def _on_recording_toggled(self, enabled: bool) -> None:
        self._config.setdefault("ui", {})["recording_enabled"] = enabled
        self._persist_config()

    def _on_mouse_control_toggled(self, enabled: bool) -> None:
        self._config.setdefault("ui", {})["mouse_control_enabled"] = enabled
        self._persist_config()

    def _persist_config(self) -> None:
        if self._config_path is None:
            return

        temporary_path = self._config_path.with_name(f".{self._config_path.name}.tmp")
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temporary_path, "w") as config_file:
                json.dump(self._config, config_file, indent=2)
                config_file.write("\n")
            temporary_path.replace(self._config_path)
        except (OSError, TypeError) as e:
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to save settings to {self._config_path}: {e}") from e

    def _on_load_session_requested(self) -> None:
        """Open the standalone session visualizer (it has its own File > Open Session)."""
        from session_viewer.session_viewer import visualize_session_native

        # If a viewer window is already open, just bring it to the front instead
        # of leaking another one.
        if self._session_viewer_window is not None:
            try:
                self._session_viewer_window.show()
                self._session_viewer_window.raise_()
                self._session_viewer_window.activateWindow()
                return
            except RuntimeError:
                # The underlying Qt widget was already destroyed - fall through
                # and create a fresh one.
                self._session_viewer_window = None

        # Keep a reference on self - without it, PySide has no C++-side owner
        # for the window and garbage-collects it as soon as this method
        # returns, so nothing ever appears on screen.
        self._session_viewer_window = visualize_session_native()

    def _on_fitts_test_requested(self) -> None:
        """Open the standalone Fitts' Law test suite."""
        from fitts_law.fitts_test import launch_fitts_test

        if self._fitts_test_window is not None:
            try:
                self._fitts_test_window.show()
                self._fitts_test_window.raise_()
                self._fitts_test_window.activateWindow()
                return
            except RuntimeError:
                self._fitts_test_window = None

        self._fitts_test_window = launch_fitts_test()

    def _drain_latest(self):
        """Pop every currently-queued sample and return only the newest one from the live provider."""
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
        from core.headmouse import TrackingState

        mouse_active = (
            self.window.controls.mouse_control_enabled
            and self.headmouse.state == TrackingState.ACTIVE
        )
        if mouse_active != self._mouse_was_active:
            self.dsp.reset(position=self.headmouse.cursor_position)
            self._mouse_was_active = mouse_active
        sample = self._drain_latest()
        if sample is None:
            return
        self._last_sample = sample

        yaw_deg, pitch_deg, roll_deg = self.dsp.relative_euler_deg(sample.quaternion)
        self.window.update_telemetry(yaw_deg, pitch_deg, roll_deg)

        # Record if enabled
        if self.window.controls.recording_enabled:
            self._recorder.record_sample(
                sample.timestamp,
                sample.quaternion,
                raw_metadata=sample.raw_metadata,
            )

        # Update cursor position if mouse control is enabled
        if mouse_active:
            target_x, target_y = self.dsp.process(sample.quaternion, timestamp=sample.timestamp)

            # # Debug: show yaw and target position periodically
            # if not hasattr(self, '_poll_count'):
            #     self._poll_count = 0
            # self._poll_count += 1
            # if self._poll_count % 10 == 0:
            #     print(f"[Poll] yaw={yaw_deg:+7.1f}° target=({target_x:7.1f}, {target_y:7.1f}) screen=({self.dsp.screen_width}, {self.dsp.screen_height})")

            self.headmouse.process_target(target_x, target_y, sample.timestamp)
            self._last_target = (target_x, target_y)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HeadMouse: head-movement recording and cursor control")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))

    from PySide6.QtWidgets import QApplication

    qt_app = QApplication(sys.argv[:1])
    app = App(config, config_path=Path(args.config))
    app.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
