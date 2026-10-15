"""Settings dialog shared by standalone and HeadMouse tray integration."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from .engine import DwellSettings
from .widgets import DWELL_HELP, ColorButton


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

        self._ring_diameter = QSpinBox()
        self._ring_diameter.setRange(8, 400)
        self._ring_diameter.setSuffix(" px")
        self._ring_diameter.setValue(settings.ring_diameter_px)

        self._ring_thickness = QSpinBox()
        self._ring_thickness.setRange(1, 100)
        self._ring_thickness.setSuffix(" px")
        self._ring_thickness.setValue(settings.ring_thickness_px)

        self._ring_color = ColorButton(settings.ring_color)

        form = QFormLayout()
        for text, field, key in (
            ("Time", self._dwell_time, "dwell_time_s"),
            ("Tolerance", self._movement_tolerance, "movement_tolerance_px"),
            ("Resume", self._resume_time, "resume_time_s"),
            ("Diameter", self._ring_diameter, "ring_diameter_px"),
            ("Thickness", self._ring_thickness, "ring_thickness_px"),
            ("Colour", self._ring_color, "ring_color"),
        ):
            label = QLabel(text)
            label.setToolTip(DWELL_HELP[key])
            field.setToolTip(DWELL_HELP[key])
            form.addRow(label, field)

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
                "ring_diameter_px": self._ring_diameter.value(),
                "ring_thickness_px": self._ring_thickness.value(),
                "ring_color": self._ring_color.color,
            }
        )
        self.accept()
