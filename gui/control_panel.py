"""Control panel for toggling subsystems independently."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from dwell_click.engine import DwellSettings
from dwell_click.widgets import DWELL_HELP, ColorButton


class ControlPanel(QWidget):
    """Toggles for recording, mouse control, initial calibration and dwell clicking."""

    recording_toggled = Signal(bool)
    mouse_control_toggled = Signal(bool)
    calibration_toggled = Signal(bool)
    dwell_click_toggled = Signal(bool)
    dwell_settings_changed = Signal(dict)

    def __init__(
        self,
        parent=None,
        recording_enabled: bool = True,
        mouse_control_enabled: bool = True,
        calibration_enabled: bool = False,
        dwell_config: dict | None = None,
    ):
        super().__init__(parent)
        dwell_config = dwell_config or {}
        defaults = DwellSettings()

        self._record_check = QCheckBox("Recording")
        self._record_check.setToolTip(
            "Save the head-movement data to a session file while tracking. "
            "Sessions are saved under File > Set Recordings Folder."
        )
        self._record_check.setChecked(recording_enabled)
        self._record_check.toggled.connect(self.recording_toggled)

        self._mouse_check = QCheckBox("Mouse Control")
        self._mouse_check.setToolTip(
            "Let head movement move the mouse pointer. Untick to track (and record) "
            "head movement without moving the pointer."
        )
        self._mouse_check.setChecked(mouse_control_enabled)
        self._mouse_check.toggled.connect(self.mouse_control_toggled)

        self._calibration_check = QCheckBox("Initial Calibration")
        self._calibration_check.setToolTip(
            "When a provider starts, capture the neutral (at-rest) head pose for 5 seconds "
            "and recentre on it before the cursor moves or movement data is recorded."
        )
        self._calibration_check.setChecked(calibration_enabled)
        self._calibration_check.toggled.connect(self.calibration_toggled)

        self._dwell_check = QCheckBox("Dwell Click")
        self._dwell_check.setChecked(bool(dwell_config.get("enabled", False)))
        self._dwell_check.setToolTip(
            "Click when the pointer remains within the movement tolerance for the dwell time. "
            "Dwell clicking runs only while mouse control is active."
        )
        self._dwell_check.toggled.connect(self.dwell_click_toggled)

        self._dwell_time_spin = QDoubleSpinBox()
        self._dwell_time_spin.setRange(0.1, 60.0)
        self._dwell_time_spin.setDecimals(2)
        self._dwell_time_spin.setSingleStep(0.1)
        self._dwell_time_spin.setSuffix(" s")
        self._dwell_time_spin.setValue(float(dwell_config.get("dwell_time_s", defaults.dwell_time_s)))

        self._movement_tolerance_spin = QSpinBox()
        self._movement_tolerance_spin.setRange(0, 500)
        self._movement_tolerance_spin.setSuffix(" px")
        self._movement_tolerance_spin.setValue(
            round(float(dwell_config.get("movement_tolerance_px", defaults.movement_tolerance_px)))
        )

        self._resume_time_spin = QDoubleSpinBox()
        self._resume_time_spin.setRange(0.0, 60.0)
        self._resume_time_spin.setDecimals(2)
        self._resume_time_spin.setSingleStep(0.1)
        self._resume_time_spin.setSuffix(" s")
        self._resume_time_spin.setValue(float(dwell_config.get("resume_time_s", defaults.resume_time_s)))

        self._ring_diameter_spin = QSpinBox()
        self._ring_diameter_spin.setRange(8, 400)
        self._ring_diameter_spin.setSuffix(" px")
        self._ring_diameter_spin.setValue(
            int(dwell_config.get("ring_diameter_px", defaults.ring_diameter_px))
        )

        self._ring_thickness_spin = QSpinBox()
        self._ring_thickness_spin.setRange(1, 100)
        self._ring_thickness_spin.setSuffix(" px")
        self._ring_thickness_spin.setValue(
            int(dwell_config.get("ring_thickness_px", defaults.ring_thickness_px))
        )

        self._ring_color_button = ColorButton(dwell_config.get("ring_color", defaults.ring_color))
        self._ring_color_button.color_changed.connect(self._emit_dwell_settings)

        for control in (
            self._dwell_time_spin,
            self._movement_tolerance_spin,
            self._resume_time_spin,
            self._ring_diameter_spin,
            self._ring_thickness_spin,
        ):
            control.valueChanged.connect(self._emit_dwell_settings)

        box = QGroupBox("Subsystems")
        layout = QGridLayout(box)
        layout.addWidget(self._record_check, 0, 0)
        layout.addWidget(self._mouse_check, 0, 1)
        layout.addWidget(self._calibration_check, 1, 0)
        layout.addWidget(self._dwell_check, 1, 1)

        dwell_box = QGroupBox("Dwell Click Settings")
        dwell_grid = QGridLayout(dwell_box)
        dwell_grid.setColumnStretch(1, 1)
        dwell_grid.setColumnStretch(3, 1)
        for row, column, text, field, key in (
            (0, 0, "Time", self._dwell_time_spin, "dwell_time_s"),
            (0, 1, "Colour", self._ring_color_button, "ring_color"),
            (1, 0, "Tolerance", self._movement_tolerance_spin, "movement_tolerance_px"),
            (1, 1, "Resume", self._resume_time_spin, "resume_time_s"),
            (2, 0, "Diameter", self._ring_diameter_spin, "ring_diameter_px"),
            (2, 1, "Thickness", self._ring_thickness_spin, "ring_thickness_px"),
        ):
            label = QLabel(text)
            label.setToolTip(DWELL_HELP[key])
            field.setToolTip(DWELL_HELP[key])
            dwell_grid.addWidget(label, row, column * 2)
            dwell_grid.addWidget(field, row, column * 2 + 1)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(box)
        main_layout.addWidget(dwell_box)

    @property
    def recording_enabled(self) -> bool:
        return self._record_check.isChecked()

    @property
    def mouse_control_enabled(self) -> bool:
        return self._mouse_check.isChecked()

    @property
    def calibration_enabled(self) -> bool:
        return self._calibration_check.isChecked()

    @property
    def dwell_click_enabled(self) -> bool:
        return self._dwell_check.isChecked()

    @property
    def dwell_settings(self) -> dict:
        return {
            "dwell_time_s": self._dwell_time_spin.value(),
            "movement_tolerance_px": float(self._movement_tolerance_spin.value()),
            "resume_time_s": self._resume_time_spin.value(),
            "ring_diameter_px": self._ring_diameter_spin.value(),
            "ring_thickness_px": self._ring_thickness_spin.value(),
            "ring_color": self._ring_color_button.color,
        }

    def _emit_dwell_settings(self, *_args) -> None:
        self.dwell_settings_changed.emit(self.dwell_settings)

    def set_dwell_settings(self, values: dict) -> None:
        for control, key in (
            (self._dwell_time_spin, "dwell_time_s"),
            (self._movement_tolerance_spin, "movement_tolerance_px"),
            (self._resume_time_spin, "resume_time_s"),
            (self._ring_diameter_spin, "ring_diameter_px"),
            (self._ring_thickness_spin, "ring_thickness_px"),
        ):
            if key not in values:
                continue
            blocked = control.blockSignals(True)
            control.setValue(round(values[key]) if isinstance(control, QSpinBox) else values[key])
            control.blockSignals(blocked)
        if "ring_color" in values:
            self._ring_color_button.set_color(values["ring_color"])

    def set_mouse_control_enabled(self, enabled: bool) -> None:
        """Set the Mouse Control toggle, emitting so the change is persisted."""
        self._mouse_check.setChecked(enabled)

    def set_recording_enabled(self, enabled: bool) -> None:
        """Set the Recording toggle, emitting so the change is persisted."""
        self._record_check.setChecked(enabled)

    def set_dwell_click_enabled(self, enabled: bool) -> None:
        blocked = self._dwell_check.blockSignals(True)
        self._dwell_check.setChecked(enabled)
        self._dwell_check.blockSignals(blocked)
