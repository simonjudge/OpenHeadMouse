import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QSystemTrayIcon


@pytest.mark.parametrize("platform,expected", [
    ("linux", (1600, 1200, -1600, 200)),
    ("darwin", (800, 600, -800, 100)),
    ("win32", (800, 600, -800, 100)),
])
def test_tracking_bounds_use_native_x11_pixels_but_macos_points(monkeypatch, platform, expected):
    import main

    monkeypatch.setattr(main.sys, "platform", platform)
    monkeypatch.setattr(main, "_windows_monitor_geometry", lambda window_id=None: None)
    screen = SimpleNamespace(geometry=lambda: QRect(-800, 100, 800, 600),
                             devicePixelRatio=lambda: 2)
    assert main._screen_geometry(screen) == expected


@pytest.mark.parametrize("platform,allowed", [
    ("linux", False), ("win32", False), ("darwin", True), ("darwin", False),
])
def test_screen_capture_permission_is_macos_only(monkeypatch, platform, allowed):
    from core import platform_access

    calls = []

    class Preflight:
        def __call__(self):
            calls.append(True)
            return allowed

    monkeypatch.setattr(platform_access.sys, "platform", platform)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: SimpleNamespace(
        CGPreflightScreenCaptureAccess=Preflight(),
    ))
    if platform == "darwin" and not allowed:
        with pytest.raises(RuntimeError, match="Screen Recording"):
            platform_access.require_screen_capture()
    else:
        platform_access.require_screen_capture()
    assert calls == ([True] if platform == "darwin" else [])


def test_zoom_cancels_before_capture_when_permission_denied(qt_app, monkeypatch):
    from dwell_click import zoom
    from dwell_click.engine import DwellSettings

    def denied():
        raise RuntimeError("Screen Recording permission required")

    monkeypatch.setattr(zoom, "require_screen_capture", denied)
    monkeypatch.setattr(zoom.QGuiApplication, "screenAt",
                        lambda point: pytest.fail("Must check permission before capture"))
    widget = zoom.DwellZoom()
    try:
        with pytest.raises(RuntimeError, match="Screen Recording"):
            widget.open_at((100, 100), DwellSettings())
        assert not widget.isVisible()
    finally:
        widget.close()


def test_context_menu_still_works_without_screen_capture(qt_app, monkeypatch, caplog):
    from dwell_click import context_menu

    def denied():
        raise RuntimeError("Screen Recording permission required")

    monkeypatch.setattr(context_menu, "require_screen_capture", denied)
    screen = SimpleNamespace(grabWindow=lambda *args: pytest.fail("Permission denied"))
    result = context_menu.DwellContextMenu._is_dark_background(screen, QRect(0, 0, 54, 54))
    assert isinstance(result, bool)
    assert "Screen Recording" in caplog.text


@pytest.mark.parametrize("tray_available,messages_supported", [(False, False), (True, False)])
def test_dwell_error_is_visible_without_tray_notifications(
    qt_app, monkeypatch, tray_available, messages_supported,
):
    from dwell_click import tray
    from dwell_click.engine import DwellSettings

    messages = []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: tray_available)
    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", lambda: messages_supported)
    monkeypatch.setattr(tray.QMessageBox, "warning", lambda *args: messages.append(args[2]))
    widget = tray.DwellTray(SimpleNamespace(is_running=False, stop=lambda: None), DwellSettings())
    try:
        widget._show_error("Accessibility unavailable")
        assert messages == ["Accessibility unavailable"]
    finally:
        widget.close()


def test_airpods_helper_does_not_write_to_bundle(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(main.sys, "platform", "darwin")
    source = tmp_path / "Read Only.app" / "swift_src"
    state = tmp_path / "User Cache"
    monkeypatch.setattr(main, "AIRPODS_HELPER_DIR", source)
    monkeypatch.setattr(main, "AIRPODS_HELPER_PID_PATH", state / "airpods-helper.pid")
    monkeypatch.setattr(main, "AIRPODS_HELPER_LOG_PATH", state / "airpods-helper.log")
    app = main.App.__new__(main.App)
    app._config = {"providers": {"airpods": {"udp_host": "127.0.0.2", "udp_port": 5997}}}
    script = app._airpods_terminal_script()
    assert "--host 127.0.0.2 --port 5997" in script
    assert str(source / "main.swift") in script
    assert str(state / "airpods-helper.pid") in script
    assert str(state / "airpods-helper.log") in script
    assert ".build" not in script
    assert f"cd '{source}'" not in script


@pytest.mark.parametrize("available", [True, False])
def test_standalone_dwell_exposes_controls_without_system_tray(monkeypatch, available):
    from dwell_click import __main__ as launcher, service
    from PySide6 import QtWidgets

    calls = []

    class Application:
        def __init__(self, argv):
            self.aboutToQuit = SimpleNamespace(connect=lambda callback: None)

        def setQuitOnLastWindowClosed(self, enabled):
            calls.append(("quit_on_close", enabled))

        def exec(self):
            return 0

    class Service:
        def __init__(self, settings):
            self.settings = settings

        def start(self):
            calls.append(("start", True))

        def stop(self):
            pass

    class Tray:
        def __init__(self, service, settings, **kwargs):
            pass

        def show_settings(self):
            calls.append(("settings", True))

        def close(self):
            pass

    monkeypatch.setattr(QtWidgets, "QApplication", Application)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: available)
    monkeypatch.setattr(service, "DwellClickService", Service)
    monkeypatch.setattr(launcher, "DwellTray", Tray)
    assert launcher.main([]) == 0
    assert (("settings", True) in calls) is not available
    assert (("quit_on_close", True) in calls) is not available


def test_standalone_settings_remain_accessible_after_applying_without_tray(qt_app, monkeypatch):
    from dwell_click.engine import DwellSettings
    from dwell_click.tray import DwellTray

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    changed = []
    tray = DwellTray(SimpleNamespace(is_running=False, stop=lambda: None),
                     DwellSettings(), on_settings_changed=changed.append)
    try:
        tray.show_settings()
        dialog = tray._settings_dialog
        dialog._dwell_time.setValue(2)
        dialog._apply()
        assert changed[0]["dwell_time_s"] == 2
        assert dialog.isVisible()
        assert tray._settings_dialog is dialog
    finally:
        tray.close()
