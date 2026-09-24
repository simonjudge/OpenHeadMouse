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
