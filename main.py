"""Central application launcher for the HeadMouse system.

Wires: [provider thread] -> dsp_engine.py -> headmouse.py -> OS
cursor, polled by a QTimer at a fixed rate (default 90Hz, within the spec's
60-120Hz consumer poll-rate guidance), plus the minimal GUI (gui/setup_gui.py
+ gui/headmouse_gui.py + gui/vis_gui.py) for provider start/stop, gain
tuning, recenter, and live telemetry.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

__version__ = "0.2.0"

CONFIG_PATH = Path(__file__).parent / "config.json"
AIRPODS_HELPER_DIR = Path(__file__).parent / "providers" / "airpods" / "swift_src"
AIRPODS_HELPER_PID_PATH = AIRPODS_HELPER_DIR / ".build" / "airpods-helper.pid"
AIRPODS_HELPER_LOG_PATH = AIRPODS_HELPER_DIR / ".build" / "airpods-helper.log"


def _windows_monitor_geometry(window_id: int | None = None) -> tuple[int, int, int, int] | None:
    """Return native-pixel monitor bounds on Windows."""
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [
        wintypes.HMONITOR,
        ctypes.POINTER(MonitorInfo),
    ]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE

    previous_dpi_context = user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
    if not previous_dpi_context:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if window_id is None:
            monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
        else:
            monitor = user32.MonitorFromWindow(wintypes.HWND(window_id), 2)
        if not monitor:
            raise ctypes.WinError(ctypes.get_last_error())

        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not user32.SetThreadDpiAwarenessContext(previous_dpi_context):
            raise ctypes.WinError(ctypes.get_last_error())

    bounds = info.rcMonitor
    return (
        bounds.right - bounds.left,
        bounds.bottom - bounds.top,
        bounds.left,
        bounds.top,
    )


def _screen_geometry(screen, window_id: int | None = None) -> tuple[int, int, int, int]:
    """Return geometry in the coordinate system used by OS cursor injection."""
    native_geometry = _windows_monitor_geometry(window_id)
    if native_geometry is not None:
        return native_geometry

    geometry = screen.geometry()
    return geometry.width(), geometry.height(), geometry.left(), geometry.top()


def ensure_mouse_profiles(config: dict) -> dict:
    """Return per-provider DSP profiles, migrating the legacy shared profile."""
    profiles = config.setdefault("mouse_profiles", {})
    legacy_profile = config.pop("dsp", None)
    provider_names = set(config.get("providers", {}))
    provider_names.add(config.get("active_provider", "uniface"))

    if legacy_profile is not None:
        for provider_name in provider_names:
            profiles.setdefault(provider_name, copy.deepcopy(legacy_profile))

    if not profiles:
        raise ValueError("Configuration must define 'mouse_profiles' or legacy 'dsp' settings")

    template = profiles.get(config.get("active_provider", "uniface"))
    if template is None:
        template = next(iter(profiles.values()))
    for provider_name in provider_names:
        profiles.setdefault(provider_name, copy.deepcopy(template))
    return profiles


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
        self._config_migrated = "dsp" in config
        self._mouse_profiles = ensure_mouse_profiles(config)
        self._active_provider_name = config.get("active_provider", "uniface")
        self._qm = qm
        self._provider = None
        self._airpods_helper_pid: int | None = None
        self._airpods_terminal_window_id: int | None = None
        self._playback_session = None  # for loading/replaying recorded sessions
        self._last_target = None
        self._last_sample = None
        self._mouse_was_active = False
        self._session_viewer_window = None  # keep a reference so Qt/PySide doesn't GC it
        self._fitts_test_window = None  # keep a reference so Qt/PySide doesn't GC it
        self._tracking_screen_geometry = None
        self._calibration_overlay = None
        self._calibrating = False
        self._calibration_capturing = False
        self._calibration_buffer: list = []
        self._calibration_warning = None
        self._ximu_waiting_to_start = False
        self._ximu_was_streaming = False
        self._ximu_status = None
        self._ximu_wifi_provider = False

        from core.recorder import Recorder

        self._recorder = Recorder(
            output_dir=config["recorder"]["output_dir"],
            format=config["recorder"]["format"],
        )

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            width, height, origin_x, origin_y = _screen_geometry(screen)
        else:
            width, height, origin_x, origin_y = 1920, 1080, 0, 0
        print(f"[Screen] Initial display: {width}x{height} at ({origin_x}, {origin_y})")

        active_dsp_config = self._mouse_profile(self._active_provider_name)
        self.dsp = DSPEngine.from_config(
            active_dsp_config, screen_width=width, screen_height=height
        )
        self.dsp.set_screen_geometry(width, height, origin_x, origin_y)
        self.headmouse = HeadMouse(
            screen_bounds=ScreenBounds(width=width, height=height, origin_x=origin_x, origin_y=origin_y),
            corner_region_px=config["screen"]["corner_region_px"],
            corner_failsafe_ms=config["screen"]["corner_failsafe_ms"],
            hotkey=config["hotkeys"]["toggle_tracking"],
            mouse_controller=mouse_controller,
        )
        from dwell_click.engine import DwellSettings
        from dwell_click.service import DwellClickService
        from dwell_click.tray import DwellTray

        dwell_config = config.get("ui", {}).get("dwell_click", {})
        self._dwell_armed = bool(dwell_config.get("enabled", False))
        self._mouse_control_enabled = bool(
            config.get("ui", {}).get("mouse_control_enabled", True)
        )
        self._dwell_settings = DwellSettings(
                dwell_time_s=float(dwell_config.get("dwell_time_s", 1.0)),
                movement_tolerance_px=float(
                    dwell_config.get("movement_tolerance_px", 10.0)
                ),
                resume_time_s=float(dwell_config.get("resume_time_s", 0.5)),
                ring_color=dwell_config.get("ring_color", "#ff0000"),
        )
        self._dwell_click = DwellClickService(
            self._dwell_settings,
            mouse_controller=mouse_controller,
        )

        self.window = HeadMouseWindow(
            initial_kx=self.dsp.kx,
            initial_ky=self.dsp.ky,
            hotkey=config["hotkeys"]["toggle_tracking"],
            initial_dsp_config=active_dsp_config,
            initial_provider=config.get("active_provider", "uniface"),
            initial_ui_config=config.get("ui", {}),
        )
        self.headmouse.add_state_callback(
            lambda state: self.window.tracking_state_changed.emit(state.value)
        )
        self.window.set_status(self.headmouse.state.value)
        self.headmouse.add_state_callback(lambda _state: self._sync_dwell_click())

        self.window.setup_panel.start_requested.connect(self._on_start_requested)
        self.window.setup_panel.stop_requested.connect(self._on_stop_requested)
        self.window.setup_panel.provider_changed.connect(self._on_provider_changed)
        self.window.setup_panel.ximu3_ignore_magnetometer_toggled.connect(
            self._on_ximu3_ignore_magnetometer_toggled
        )
        self.window.setup_panel.set_ximu3_ignore_magnetometer(
            config.get("providers", {}).get("ximu3", {}).get("ahrs_ignore_magnetometer", False)
        )
        ximu3_config = config.get("providers", {}).get("ximu3", {})
        self.window.setup_panel.set_ximu3_axes_configuration(
            ximu3_config.get("axes_alignment", "+X-Z+Y"),
            ximu3_config.get("ahrs_axes_convention", 2),
        )
        self.window.setup_panel.ximu3_mounting_orientation_changed.connect(
            self._on_ximu3_mounting_orientation_changed
        )
        self.window.recenter_requested.connect(self._on_recenter)
        self.window.kx_changed.connect(self._on_kx_changed)
        self.window.ky_changed.connect(self._on_ky_changed)
        self.window.dsp_config_changed.connect(self._on_dsp_config_changed)
        self.window.load_session_requested.connect(self._on_load_session_requested)
        self.window.fitts_test_requested.connect(self._on_fitts_test_requested)
        self.window.controls.recording_toggled.connect(self._on_recording_toggled)
        self.window.controls.mouse_control_toggled.connect(self._on_mouse_control_toggled)
        self.window.controls.calibration_toggled.connect(self._on_calibration_toggled)
        self.window.controls.dwell_click_toggled.connect(self._on_dwell_click_toggled)
        self.window.controls.dwell_settings_changed.connect(self._on_dwell_settings_changed)
        self._dwell_tray = DwellTray(
            self._dwell_click,
            self._dwell_settings,
            on_settings_changed=self._on_dwell_settings_changed,
            on_toggled=self._on_dwell_tray_toggled,
            parent=self.window,
        )

        if auto_start_listeners:
            self.headmouse.start()
            if self.headmouse.hotkey_error:
                self.window.set_hotkey_status(
                    f"Hotkey unavailable: {self.headmouse.hotkey_error}"
                )

        if self.window.controls.dwell_click_enabled:
            self._sync_dwell_click()

        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(int(1000 / poll_hz))
        if self._config_migrated:
            self._persist_config()

    def show(self) -> None:
        self.window.show()

    def shutdown(self) -> None:
        self._timer.stop()
        self._dwell_tray.close()
        self._dwell_click.stop()
        self._end_calibration()
        if self._provider is not None:
            self._provider.stop()
            self._provider = None
        self._stop_airpods_helper()
        self.headmouse.stop()

    def _on_start_requested(self, provider_name: str) -> None:
        try:
            # Special handling for xIMU3: show device discovery dialog
            if provider_name == "ximu3":
                from ipaddress import ip_address
                from PySide6.QtCore import QTimer
                from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton
                from providers.ximu3_provider import DiscoveredDevice, discover_ximu3_devices

                dialog = QDialog(self.window)
                dialog.setWindowTitle("Select xIMU3 Device")
                layout = QVBoxLayout(dialog)
                layout.addWidget(QLabel("Select xIMU3 device:"))
                combo = QComboBox()
                
                layout.addWidget(combo)
                scan_status = QLabel("Scanning for devices...")
                layout.addWidget(scan_status)
                refresh_button = QPushButton("Refresh devices")
                layout.addWidget(refresh_button)
                ok_button = QPushButton("Connect")
                ok_button.setEnabled(combo.count() > 0)
                cancel_button = QPushButton("Cancel")
                layout.addWidget(ok_button)
                layout.addWidget(cancel_button)

                scan_results = queue.Queue()
                scanning = False

                def refresh():
                    nonlocal scanning
                    if scanning:
                        return
                    scanning = True
                    refresh_button.setEnabled(False)
                    scan_status.setText("Scanning for devices...")

                    def scan():
                        errors = []
                        devices = discover_ximu3_devices(errors=errors)
                        scan_results.put((devices, errors))

                    threading.Thread(target=scan, name="xIMU3 discovery", daemon=True).start()

                def collect_scan():
                    nonlocal scanning
                    try:
                        devices, errors = scan_results.get_nowait()
                    except queue.Empty:
                        return
                    scanning = False
                    refresh_button.setEnabled(True)
                    selected = combo.currentData()
                    while combo.count() > 0:
                        combo.removeItem(0)
                    for device in devices:
                        combo.addItem(device.name, userData=device)
                    ok_button.setEnabled(combo.count() > 0)
                    if selected is not None:
                        for i in range(combo.count()):
                            if combo.itemData(i) == selected:
                                combo.setCurrentIndex(i)
                                break
                    scan_status.setText("; ".join(errors) or f"{len(devices)} device(s) discovered.")

                timer = QTimer(dialog)
                timer.timeout.connect(collect_scan)
                timer.start(100)
                refresh_button.clicked.connect(refresh)
                refresh()
                selected_device = None

                def on_ok():
                    nonlocal selected_device
                    selected_device = combo.currentData()
                    dialog.accept()

                ok_button.clicked.connect(on_ok)
                cancel_button.clicked.connect(dialog.reject)

                accepted = dialog.exec() == QDialog.Accepted
                timer.stop()
                if not accepted or selected_device is None:
                    self.window.setup_panel.reset_to_stopped()
                    return

                # Create provider with selected device
                from providers.ximu3_provider import Ximu3Provider
                self._provider = Ximu3Provider(
                    connection_type=selected_device.connection_type,
                    port_name=selected_device.port_name,
                    wifi_ip_address=selected_device.ip_address,
                    wifi_port_send=selected_device.port,
                    wifi_port_receive=selected_device.receive_port,
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
            self._ximu_wifi_provider = provider_name == "ximu3" and self._provider.connection_type == "wifi_ap"
            self._ximu_waiting_to_start = self._ximu_wifi_provider
            self._ximu_was_streaming = False
            self._ximu_status = None
            self._provider.start()
            if self._ximu_waiting_to_start:
                self.headmouse.set_tracking_state(False)
                self.window.setup_panel.set_connection_status("Connecting to xIMU3...")
            elif self.window.controls.calibration_enabled:
                self._begin_calibration()
            else:
                self.headmouse.set_tracking_state(True)

        except Exception as e:
            # Reset UI state and show error
            self._end_calibration()
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

        window_id = int(self.window.winId()) if sys.platform == "win32" else None
        width, height, origin_x, origin_y = _screen_geometry(screen, window_id)
        self._tracking_screen_geometry = screen.geometry()
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

    def _on_stop_requested(self):
        self._end_calibration()
        self.headmouse.set_tracking_state(False)
        if self._provider is not None:
            try:
                self._provider.stop()
            except RuntimeError as exc:
                from PySide6.QtWidgets import QMessageBox

                self.window.setup_panel.keep_stop_enabled()
                QMessageBox.critical(self.window, "Disconnection Failed", str(exc))
                return None
            self._provider = None
        self._ximu_waiting_to_start = False
        self._ximu_was_streaming = False
        self._ximu_status = None
        self._ximu_wifi_provider = False
        self.window.setup_panel.set_connection_status("")
        self._stop_airpods_helper()
        # Save the recording if anything was recorded
        filepath = None
        if self._recorder.has_samples or self._recorder.has_calibration:
            filepath = self._recorder.save()
            print(f"Session saved to {filepath}")
        # Each start/stop run is its own session file; without this, every
        # save re-wrote all data since launch (mixing providers' clocks).
        self._recorder.clear()
        return filepath

    def stop_tracking_and_save(self):
        """Stop a running provider and save its recording; returns the file path.

        Used by the Fitts window when it opens synchronised playback: the head
        session has to be stopped and written out before it can be replayed.
        """
        was_running = self._provider is not None
        filepath = self._on_stop_requested()
        if was_running:
            self.window.setup_panel.reset_to_stopped()
        return filepath

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
            f'    set helperTab to do script "{escaped_command}"\n'
            "    set helperTty to tty of helperTab\n"
            "    repeat with w in windows\n"
            "        repeat with t in tabs of w\n"
            "            if tty of t is helperTty then\n"
            "                set helperWindowId to id of w\n"
            "                try\n"
            "                    set miniaturized of w to true\n"
            "                end try\n"
            "                return helperWindowId\n"
            "            end if\n"
            "        end repeat\n"
            "    end repeat\n"
            '    error "AirPods helper Terminal window not found"\n'
            "end tell"
        )

    def _start_airpods_helper(self) -> None:
        if self._airpods_helper_pid is not None:
            try:
                os.kill(self._airpods_helper_pid, 0)
                return
            except ProcessLookupError:
                self._stop_airpods_helper()

        AIRPODS_HELPER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)
        AIRPODS_HELPER_LOG_PATH.write_text("")
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", self._airpods_terminal_script()],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError(
                "Could not launch the AirPods helper in Terminal. Install the Xcode "
                "command-line tools and allow Terminal to access Motion data."
            ) from e

        try:
            window_id = int(result.stdout.strip())
            if window_id <= 0:
                raise ValueError
        except ValueError as e:
            raise RuntimeError("Terminal did not report the AirPods helper window ID") from e
        self._airpods_terminal_window_id = window_id

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
        if helper_pid is not None:
            try:
                os.kill(helper_pid, signal.SIGTERM)
            except ProcessLookupError:
                AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)
            else:
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    try:
                        os.kill(helper_pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    try:
                        os.kill(helper_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                AIRPODS_HELPER_PID_PATH.unlink(missing_ok=True)

        window_id = self._airpods_terminal_window_id
        self._airpods_terminal_window_id = None
        if window_id is None:
            return

        close_window_script = (
            'tell application "Terminal"\n'
            f"    if exists (window id {window_id}) then\n"
            "        repeat 40 times\n"
            f"            if not (busy of selected tab of window id {window_id}) then exit repeat\n"
            "            delay 0.05\n"
            "        end repeat\n"
            f"        close (window id {window_id})\n"
            "    end if\n"
            "end tell"
        )
        subprocess.run(
            ["/usr/bin/osascript", "-e", close_window_script],
            check=False,
        )

    def _on_ximu3_mounting_orientation_changed(self, name: str) -> None:
        from providers.ximu3_provider import Ximu3Provider
        from providers.ximu3_settings import MOUNTING_ORIENTATIONS
        from PySide6.QtWidgets import QMessageBox

        config = self._config.setdefault("providers", {}).setdefault("ximu3", {})
        previous_config = config.copy()
        previous = (
            config.get("axes_alignment", "+X-Z+Y"),
            config.get("ahrs_axes_convention", 2),
        )
        applied = False
        try:
            alignment, convention = MOUNTING_ORIENTATIONS[name]
            if isinstance(self._provider, Ximu3Provider):
                self._provider.set_axes_configuration(alignment, convention)
                applied = True
            config.update(axes_alignment=alignment, ahrs_axes_convention=convention)
            self._persist_config()
        except Exception as exc:
            config.clear()
            config.update(previous_config)
            self.window.setup_panel.set_ximu3_axes_configuration(*previous)
            error = str(exc)
            if applied:
                try:
                    self._provider.set_axes_configuration(*previous)
                except Exception as rollback_error:
                    error += f"\nRestoring previous device settings failed: {rollback_error}"
            QMessageBox.critical(
                self.window, "Mounting Setting Failed",
                f"Failed to set xIMU3 mounting orientation:\n\n{error}",
            )
            return
        if applied:
            self._end_calibration()
            self.dsp.reset(position=self.headmouse.cursor_position)
            self._last_sample = None
            self._last_target = None
            self._mouse_was_active = False
            if self.window.controls.calibration_enabled and not self._ximu_waiting_to_start:
                self._begin_calibration()

    def _on_ximu3_ignore_magnetometer_toggled(self, enabled: bool) -> None:
        from providers.ximu3_provider import Ximu3Provider

        config = self._config.setdefault("providers", {}).setdefault("ximu3", {})
        previous = config.get("ahrs_ignore_magnetometer", False)
        try:
            if isinstance(self._provider, Ximu3Provider):
                self._provider.set_ahrs_ignore_magnetometer(enabled)
            config["ahrs_ignore_magnetometer"] = enabled
            self._persist_config()
        except Exception as e:
            config["ahrs_ignore_magnetometer"] = previous
            self.window.setup_panel.set_ximu3_ignore_magnetometer(previous)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.window,
                "AHRS Setting Failed",
                f"Failed to set AHRS ignore magnetometer:\n\n{str(e)}",
            )

    def _on_recenter(self) -> None:
        if self._calibrating:
            return
        latest = self._drain_latest() or self._last_sample
        if latest is not None:
            self._recenter_to(latest.quaternion, latest.timestamp)

    def _recenter_to(self, quaternion, timestamp: float, source: str | None = None) -> None:
        self.dsp.recenter(quaternion)
        self._last_target = None
        if self.window.controls.mouse_control_enabled:
            self.headmouse.process_target(
                self.dsp.origin_x + self.dsp.screen_width / 2.0,
                self.dsp.origin_y + self.dsp.screen_height / 2.0,
                timestamp,
            )
        # Log the recenter event with the quaternion used as reference
        data = {"quaternion": [float(quaternion[i]) for i in range(4)]}
        if source is not None:
            data["source"] = source
        self._recorder.record_event(timestamp, "recenter", data)
        print(f"✓ Recentered at {timestamp:.3f}s")

    # -- initial calibration ---------------------------------------------------
    def _begin_calibration(self) -> None:
        from gui.calibration_overlay import CalibrationOverlay

        calibration_config = self._config.get("calibration", {})
        self.headmouse.set_tracking_state(False)
        self._calibrating = True
        self._calibration_capturing = False
        self._calibration_buffer = []
        overlay = CalibrationOverlay(
            read_duration_s=calibration_config.get("read_duration_s", 5.0),
            capture_duration_s=calibration_config.get("capture_duration_s", 5.0),
        )
        overlay.capture_started.connect(self._on_calibration_capture_started)
        overlay.finished.connect(self._on_calibration_finished)
        overlay.cancelled.connect(self._on_calibration_cancelled)
        self._calibration_overlay = overlay
        overlay.start(self._tracking_screen_geometry)

    def _end_calibration(self) -> None:
        """Tear down any in-progress calibration without saving it."""
        overlay = self._calibration_overlay
        self._calibration_overlay = None
        self._calibrating = False
        self._calibration_capturing = False
        self._calibration_buffer = []
        if overlay is not None:
            overlay.stop()
            overlay.deleteLater()

    def _on_calibration_capture_started(self) -> None:
        # Discard anything queued during the read period so only the
        # held-still countdown contributes to the neutral pose.
        self._drain_all()
        self._calibration_buffer = []
        self._calibration_capturing = True

    def _on_calibration_finished(self) -> None:
        if not self._calibrating:
            return
        self._collect_calibration_samples()
        buffer = self._calibration_buffer
        capture_duration = self._calibration_overlay.capture_duration_s if self._calibration_overlay else None
        self._end_calibration()

        self.dsp.reset(position=self.headmouse.cursor_position)
        self._mouse_was_active = False
        self.headmouse.set_tracking_state(True)

        if not buffer:
            self._show_calibration_warning(
                "No head-tracking data was received during calibration, so the "
                "neutral pose could not be captured. Tracking has started without recentring."
            )
            return

        neutral = self._qm.mean_quaternion(sample.quaternion for sample in buffer)
        if self.window.controls.recording_enabled:
            self._recorder.set_calibration(
                [(sample.timestamp, sample.quaternion) for sample in buffer],
                neutral,
                duration_s=capture_duration,
            )
        self._last_sample = buffer[-1]
        self._recenter_to(neutral, buffer[-1].timestamp, source="calibration")
        print(f"✓ Calibration captured from {len(buffer)} samples")

    def _on_calibration_cancelled(self) -> None:
        if not self._calibrating:
            return
        print("Calibration cancelled")
        self.window.setup_panel.reset_to_stopped()
        self._on_stop_requested()

    def _show_calibration_warning(self, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        print(f"⚠ {text}")
        box = QMessageBox(QMessageBox.Warning, "Calibration", text, QMessageBox.Ok, self.window)
        box.setModal(False)
        box.show()
        self._calibration_warning = box

    def _collect_calibration_samples(self) -> None:
        samples = self._drain_all()
        if samples:
            self._last_sample = samples[-1]
            yaw_deg, pitch_deg, roll_deg = self.dsp.relative_euler_deg(samples[-1].quaternion)
            self.window.update_telemetry(yaw_deg, pitch_deg, roll_deg)
        if self._calibration_capturing:
            self._calibration_buffer.extend(samples)

    def _on_kx_changed(self, value: float) -> None:
        self.dsp.kx = value
        self._mouse_profile().setdefault("absolute", {})["kx"] = value
        self._persist_config()

    def _on_ky_changed(self, value: float) -> None:
        self.dsp.ky = value
        self._mouse_profile().setdefault("absolute", {})["ky"] = value
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
        dsp_config = self._mouse_profile()
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
        self._active_provider_name = provider_name
        self._config["active_provider"] = provider_name
        self._apply_mouse_profile(provider_name)
        self._persist_config()

    def _mouse_profile(self, provider_name: str | None = None) -> dict:
        provider_name = provider_name or self._active_provider_name
        profile = self._mouse_profiles.get(provider_name)
        if profile is None:
            source = self._mouse_profiles.get(self._active_provider_name)
            if source is None:
                source = next(iter(self._mouse_profiles.values()))
            profile = copy.deepcopy(source)
            self._mouse_profiles[provider_name] = profile
        return profile

    def _apply_mouse_profile(self, provider_name: str) -> None:
        from core.dsp_engine import DSPEngine

        profile = self._mouse_profile(provider_name)
        replacement = DSPEngine.from_config(
            profile,
            screen_width=self.dsp.screen_width,
            screen_height=self.dsp.screen_height,
        )
        replacement.set_screen_geometry(
            self.dsp.screen_width,
            self.dsp.screen_height,
            self.dsp.origin_x,
            self.dsp.origin_y,
        )
        replacement.reset(position=self.headmouse.cursor_position)
        self.dsp = replacement
        self._last_target = None
        self._last_sample = None
        self._mouse_was_active = False
        self.window.set_dsp_config(profile)

    def _on_recording_toggled(self, enabled: bool) -> None:
        self._config.setdefault("ui", {})["recording_enabled"] = enabled
        self._persist_config()

    def _on_mouse_control_toggled(self, enabled: bool) -> None:
        self._config.setdefault("ui", {})["mouse_control_enabled"] = enabled
        self._mouse_control_enabled = enabled
        self._sync_dwell_click()
        self._persist_config()

    def _on_calibration_toggled(self, enabled: bool) -> None:
        self._config.setdefault("ui", {})["calibration_enabled"] = enabled
        self._persist_config()

    def _on_dwell_click_toggled(self, enabled: bool) -> None:
        dwell_config = self._config.setdefault("ui", {}).setdefault("dwell_click", {})
        dwell_config["enabled"] = enabled
        self._dwell_armed = enabled
        self._sync_dwell_click()
        self._persist_config()

    def _on_dwell_tray_toggled(self, enabled: bool) -> None:
        dwell_config = self._config.setdefault("ui", {}).setdefault("dwell_click", {})
        dwell_config["enabled"] = enabled
        self._dwell_armed = enabled
        self.window.controls.set_dwell_click_enabled(enabled)
        self._sync_dwell_click()
        self._persist_config()

    def _sync_dwell_click(self) -> None:
        """Dwell clicking follows mouse control: it runs only while the cursor is driven."""
        from core.headmouse import TrackingState

        should_run = (
            self._dwell_armed
            and self._mouse_control_enabled
            and self.headmouse.state == TrackingState.ACTIVE
        )
        self._dwell_click.request_running(should_run)

    def _on_dwell_settings_changed(self, values: dict) -> None:
        dwell_config = self._config.setdefault("ui", {}).setdefault("dwell_click", {})
        dwell_config.update(values)
        from dwell_click.engine import DwellSettings

        self._dwell_settings = DwellSettings(
            dwell_time_s=values["dwell_time_s"],
            movement_tolerance_px=values["movement_tolerance_px"],
            resume_time_s=values["resume_time_s"],
            ring_color=dwell_config.get("ring_color", "#ff0000"),
        )
        self._dwell_click.update_settings(self._dwell_settings)
        self._dwell_tray.set_settings(self._dwell_settings)
        self.window.controls.set_dwell_settings(values)
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
        self._fitts_test_window.set_head_session_provider(self.stop_tracking_and_save)

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

    def _drain_all(self) -> list:
        """Pop and return every currently-queued sample from the live provider."""
        if self._provider is None:
            return []
        samples = []
        while True:
            try:
                samples.append(self._provider.output_queue.get_nowait())
            except queue.Empty:
                break
        return samples

    def _poll(self) -> None:
        from core.headmouse import TrackingState
        from providers.base_provider import ProviderState

        if self._ximu_wifi_provider and self._provider is not None:
            status = self._provider.link_status
            if self._provider.state == ProviderState.ERROR:
                status = "error"
            detail = self._provider.connection_error
            if (status, detail) != self._ximu_status:
                self._ximu_status = (status, detail)
                self.window.setup_panel.set_connection_status(
                    f"xIMU3: {status}" + (f" ({detail})" if detail else "")
                )
            if status != "streaming":
                if self._ximu_was_streaming:
                    self._ximu_was_streaming = False
                    self._last_sample = None
                    self._mouse_was_active = False
                    if self._calibrating:
                        self._end_calibration()
                        self._ximu_waiting_to_start = True
                return
            if not self._ximu_was_streaming:
                self._ximu_was_streaming = True
                self.dsp.reset(position=self.headmouse.cursor_position)
                if self._ximu_waiting_to_start:
                    self._ximu_waiting_to_start = False
                    if self.window.controls.calibration_enabled:
                        self._begin_calibration()
                    else:
                        self.headmouse.set_tracking_state(True)

        if self._calibrating:
            # The global toggle hotkey (Esc by default) aborts calibration
            # rather than starting tracking early.
            if self.headmouse.state == TrackingState.ACTIVE:
                self._on_calibration_cancelled()
                return
            self._collect_calibration_samples()
            return

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
