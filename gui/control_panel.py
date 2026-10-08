"""Control panel for toggling subsystems independently."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    """Toggles for recording, mouse control, and initial calibration."""

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

        self._record_check = QCheckBox("Recording")
        self._record_check.setChecked(recording_enabled)
        self._record_check.toggled.connect(self.recording_toggled)

        self._mouse_check = QCheckBox("Mouse Control")
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
        self._dwell_time_spin.setValue(float(dwell_config.get("dwell_time_s", 1.0)))

        self._movement_tolerance_spin = QSpinBox()
        self._movement_tolerance_spin.setRange(0, 500)
        self._movement_tolerance_spin.setSuffix(" px")
        self._movement_tolerance_spin.setValue(
            round(float(dwell_config.get("movement_tolerance_px", 10.0)))
        )

        self._resume_time_spin = QDoubleSpinBox()
        self._resume_time_spin.setRange(0.0, 60.0)
        self._resume_time_spin.setDecimals(2)
        self._resume_time_spin.setSingleStep(0.1)
        self._resume_time_spin.setSuffix(" s")
        self._resume_time_spin.setValue(float(dwell_config.get("resume_time_s", 0.5)))

        for control in (
            self._dwell_time_spin,
            self._movement_tolerance_spin,
            self._resume_time_spin,
        ):
            control.valueChanged.connect(self._emit_dwell_settings)

        box = QGroupBox("Subsystems")
        layout = QGridLayout(box)
        layout.addWidget(self._record_check, 0, 0)
        layout.addWidget(self._mouse_check, 0, 1)
        layout.addWidget(self._calibration_check, 1, 0)
        layout.addWidget(self._dwell_check, 1, 1)

        dwell_box = QGroupBox("Dwell Click Settings")
        dwell_form = QFormLayout(dwell_box)
        dwell_form.addRow("Dwell time", self._dwell_time_spin)
        dwell_form.addRow("Movement tolerance", self._movement_tolerance_spin)
        dwell_form.addRow("Resume time", self._resume_time_spin)

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
        }

    def _emit_dwell_settings(self) -> None:
        self.dwell_settings_changed.emit(self.dwell_settings)

    def set_dwell_settings(self, values: dict) -> None:
        for control, value in (
            (self._dwell_time_spin, values["dwell_time_s"]),
            (self._movement_tolerance_spin, values["movement_tolerance_px"]),
            (self._resume_time_spin, values["resume_time_s"]),
        ):
            blocked = control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(blocked)

    def set_dwell_click_enabled(self, enabled: bool) -> None:
        blocked = self._dwell_check.blockSignals(True)
        self._dwell_check.setChecked(enabled)
        self._dwell_check.blockSignals(blocked)
