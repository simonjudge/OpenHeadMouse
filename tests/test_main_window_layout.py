"""Settings remain reachable when all subsystems are selected."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint

from gui.headmouse_gui import HeadMouseWindow


@pytest.mark.parametrize("size", [(1000, 700), (800, 560)])
@pytest.mark.parametrize("mode", ["absolute", "velocity"])
def test_all_options_fit_or_scroll_without_growing_window(qt_app, size, mode):
    window = HeadMouseWindow(
        initial_kx=8, initial_ky=-8, initial_provider="ximu3",
        initial_dsp_config={"mode": mode},
        initial_ui_config={
            "recording_enabled": True,
            "mouse_control_enabled": True,
            "calibration_enabled": True,
            "dwell_click": {
                "enabled": True,
                "zoom_enabled": True,
                "context_menu_enabled": True,
            },
        },
    )
    try:
        window.resize(*size)
        window.show()
        qt_app.processEvents()
        assert window.width() <= size[0]
        assert window.height() <= size[1]
        assert window.controls.x() + window.controls.width() <= window._dsp_box.x()
        scroll = window._options_scroll
        # Every option can be brought into view even on a short display.
        for widget in (
            window.controls._zoom_options,
            window.controls._context_options,
            window._dsp_box,
            window.telemetry._roll_label,
        ):
            scroll.ensureWidgetVisible(widget)
            qt_app.processEvents()
            centre = widget.mapTo(scroll.viewport(), widget.rect().center())
            assert scroll.viewport().rect().contains(centre)
        for widget in (window.setup_panel._stop_button, window._recenter_button):
            position = widget.mapTo(window.centralWidget(), QPoint(0, 0))
            assert position.y() >= 0
            assert position.y() + widget.height() <= window.centralWidget().height()
        before = window.size()
        window.controls._dwell_check.setChecked(False)
        window.controls._mouse_check.setChecked(False)
        qt_app.processEvents()
        window.controls._dwell_check.setChecked(True)
        window.controls._mouse_check.setChecked(True)
        qt_app.processEvents()
        assert window.size() == before
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
