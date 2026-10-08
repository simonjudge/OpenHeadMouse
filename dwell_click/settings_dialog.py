"""Settings dialog shared by standalone and HeadMouse tray integration."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
)

from .engine import DwellSettings


class DwellSettingsDialog(QDialog):
    settings_applied = Signal(dict)

    def __init__(self, settings: DwellSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dwell Click Settings")

        self._dwell_time = QDoubleSpinBox()
        self._dwell_time.setRange(0.1, 60.0)
        self._dwell_time.setDecimals(2)
        self._dwell_time.setSingleStep(0.1)
        self._dwell_time.setSuffix(" s")
        self._dwell_time.setValue(settings.dwell_time_s)

        self._movement_tolerance = QSpinBox()
        self._movement_tolerance.setRange(0, 500)
        self._movement_tolerance.setSuffix(" px")
        self._movement_tolerance.setValue(round(settings.movement_tolerance_px))

        self._resume_time = QDoubleSpinBox()
        self._resume_time.setRange(0.0, 60.0)
        self._resume_time.setDecimals(2)
        self._resume_time.setSingleStep(0.1)
        self._resume_time.setSuffix(" s")
        self._resume_time.setValue(settings.resume_time_s)

        form = QFormLayout()
        form.addRow("Dwell time", self._dwell_time)
        form.addRow("Movement tolerance", self._movement_tolerance)
        form.addRow("Resume time", self._resume_time)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        self.settings_applied.emit(
            {
                "dwell_time_s": self._dwell_time.value(),
                "movement_tolerance_px": float(self._movement_tolerance.value()),
                "resume_time_s": self._resume_time.value(),
            }
        )
        self.accept()
