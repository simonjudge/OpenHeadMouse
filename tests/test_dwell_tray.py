from dwell_click.engine import DwellSettings


def test_tray_controls_service_and_settings(qt_app):
    from dwell_click.tray import DwellTray

    class Service:
        def __init__(self):
            self.is_running = False
            self.starts = 0
            self.stops = 0

        def start(self):
            self.is_running = True
            self.starts += 1

        def stop(self):
            self.is_running = False
            self.stops += 1

    service = Service()
    changed = []
    toggled = []
    tray = DwellTray(
        service,
        DwellSettings(),
        on_settings_changed=changed.append,
        on_toggled=toggled.append,
    )
    try:
        assert tray.toggle_action.text() == "Start Dwell Clicking"
        tray.toggle_action.trigger()
        assert service.starts == 1
        assert toggled == [True]
        assert tray.toggle_action.text() == "Stop Dwell Clicking"

        tray.show_settings()
        assert tray._settings_dialog is not None
        tray._settings_dialog._dwell_time.setValue(2.0)
        tray._settings_dialog._apply()
        assert changed == [
            {
                "dwell_time_s": 2.0,
                "movement_tolerance_px": 10.0,
                "resume_time_s": 0.5,
                "ring_diameter_px": 40,
                "ring_thickness_px": 4,
                "ring_color": "#ff0000",
                "zoom_enabled": False,
                "zoom_width_px": 300,
                "zoom_height_px": 200,
                "zoom_scale": 4.0,
                "context_menu_enabled": False,
                "context_menu_distance_px": 60,
                "context_menu_size_px": 54,
                "context_menu_lock_time_s": 3.0,
            }
        ]
    finally:
        tray.close()


def test_settings_start_stop_button_controls_service_without_closing(qt_app):
    from types import SimpleNamespace
    from dwell_click.service import DwellClickService
    from dwell_click.tray import DwellTray

    service = DwellClickService(mouse_controller=SimpleNamespace(click=lambda button: None))
    seen = []
    tray = DwellTray(service, DwellSettings(), on_toggled=seen.append)
    try:
        tray.show_settings()
        dialog = tray._settings_dialog
        assert dialog._toggle_button.text() == "Start Dwell Clicking"
        dialog._toggle_button.click()
        assert service.is_running
        assert dialog.isVisible()
        assert dialog._toggle_button.text() == "Stop Dwell Clicking"
        dialog._toggle_button.click()
        assert not service.is_running
        assert seen == [True, False]
        service.start()
        assert dialog._toggle_button.text() == "Stop Dwell Clicking"
        service.stop()
        assert dialog._toggle_button.text() == "Start Dwell Clicking"
    finally:
        tray.close()


def test_left_activation_opens_settings_but_context_activation_does_not(qt_app):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QSystemTrayIcon
    from dwell_click.tray import DwellTray

    tray = DwellTray(SimpleNamespace(is_running=False, stop=lambda: None), DwellSettings())
    try:
        tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        assert tray._settings_dialog is None
        tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        dialog = tray._settings_dialog
        assert dialog is not None and dialog.isVisible()
        tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        assert tray._settings_dialog is dialog
    finally:
        tray.close()
