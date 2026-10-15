"""Tests for gui/control_panel.py."""

import pytest


def test_control_panel_builds(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    assert panel.recording_enabled is True
    assert panel.mouse_control_enabled is True


def test_control_panel_accepts_initial_toggle_values(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel(recording_enabled=False, mouse_control_enabled=False)
    assert panel.recording_enabled is False
    assert panel.mouse_control_enabled is False


def test_control_panel_toggles_recording(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    seen = []
    panel.recording_toggled.connect(seen.append)

    panel._record_check.setChecked(False)
    assert seen == [False]
    assert panel.recording_enabled is False

    panel._record_check.setChecked(True)
    assert seen == [False, True]
    assert panel.recording_enabled is True


def test_control_panel_toggles_mouse_control(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    seen = []
    panel.mouse_control_toggled.connect(seen.append)

    panel._mouse_check.setChecked(False)
    assert panel.mouse_control_enabled is False

    panel._mouse_check.setChecked(True)
    assert panel.mouse_control_enabled is True


def test_control_panel_calibration_toggle(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    assert panel.calibration_enabled is False
    seen = []
    panel.calibration_toggled.connect(seen.append)
    panel._calibration_check.setChecked(True)
    assert panel.calibration_enabled is True
    assert seen == [True]
    assert ControlPanel(calibration_enabled=True).calibration_enabled is True


def test_control_panel_dwell_click_settings(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel(
        dwell_config={
            "enabled": True,
            "dwell_time_s": 1.5,
            "movement_tolerance_px": 12,
            "resume_time_s": 0.75,
        }
    )
    assert panel.dwell_click_enabled is True
    assert panel.dwell_settings == {
        "dwell_time_s": 1.5,
        "movement_tolerance_px": 12.0,
        "resume_time_s": 0.75,
        "ring_diameter_px": 40,
        "ring_thickness_px": 4,
        "ring_color": "#ff0000",
    }

    toggles = []
    settings = []
    panel.dwell_click_toggled.connect(toggles.append)
    panel.dwell_settings_changed.connect(settings.append)
    panel._dwell_check.setChecked(False)
    panel._dwell_time_spin.setValue(2.0)
    assert toggles == [False]
    assert settings[-1]["dwell_time_s"] == 2.0


def test_control_panel_dwell_appearance_settings(qt_app, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    from gui.control_panel import ControlPanel

    panel = ControlPanel(
        dwell_config={"ring_diameter_px": 60, "ring_thickness_px": 8, "ring_color": "#00ff00"}
    )
    settings = []
    panel.dwell_settings_changed.connect(settings.append)
    assert panel.dwell_settings["ring_diameter_px"] == 60
    assert panel.dwell_settings["ring_thickness_px"] == 8
    assert panel.dwell_settings["ring_color"] == "#00ff00"

    panel._ring_diameter_spin.setValue(80)
    assert settings[-1]["ring_diameter_px"] == 80
    panel._ring_thickness_spin.setValue(12)
    assert settings[-1]["ring_thickness_px"] == 12

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("#123456")))
    panel._ring_color_button.click()
    assert settings[-1]["ring_color"] == "#123456"

    count = len(settings)
    panel.set_dwell_settings({"ring_diameter_px": 30, "ring_thickness_px": 2, "ring_color": "#abcdef"})
    assert len(settings) == count
    assert panel.dwell_settings["ring_diameter_px"] == 30
    assert panel.dwell_settings["ring_color"] == "#abcdef"


def test_control_panel_has_no_recordings_folder_and_every_option_has_help(qt_app):
    from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QGroupBox, QWidget
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    titles = [box.title() for box in panel.findChildren(QGroupBox)]
    assert "Recordings Folder" not in titles
    options = (QAbstractButton, QAbstractSpinBox, QComboBox)
    for widget in [w for w in panel.findChildren(QWidget) if isinstance(w, options)]:
        assert widget.toolTip(), widget


def test_dwell_fields_are_paired_side_by_side(qt_app):
    from gui.control_panel import ControlPanel

    panel = ControlPanel()
    grid = panel._movement_tolerance_spin.parentWidget().layout()

    def position(widget):
        row, column, _, _ = grid.getItemPosition(grid.indexOf(widget))
        return row, column

    tol_row, tol_col = position(panel._movement_tolerance_spin)
    assert position(panel._resume_time_spin) == (tol_row, tol_col + 2)
    dia_row, dia_col = position(panel._ring_diameter_spin)
    assert position(panel._ring_thickness_spin) == (dia_row, dia_col + 2)
