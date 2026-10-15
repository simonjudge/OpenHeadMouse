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
            }
        ]
    finally:
        tray.close()
