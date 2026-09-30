"""Control panel for toggling subsystems independently."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)


class ControlPanel(QWidget):
    """Toggles for recording, mouse control, and initial calibration."""

    recording_toggled = Signal(bool)
    mouse_control_toggled = Signal(bool)
    calibration_toggled = Signal(bool)

    def __init__(
        self,
        parent=None,
        recording_enabled: bool = True,
        mouse_control_enabled: bool = True,
        calibration_enabled: bool = False,
    ):
        super().__init__(parent)

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

        box = QGroupBox("Subsystems")
        layout = QVBoxLayout(box)
        layout.addWidget(self._record_check)
        layout.addWidget(self._mouse_check)
        layout.addWidget(self._calibration_check)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(box)

    @property
    def recording_enabled(self) -> bool:
        return self._record_check.isChecked()

    @property
    def mouse_control_enabled(self) -> bool:
        return self._mouse_check.isChecked()

    @property
    def calibration_enabled(self) -> bool:
        return self._calibration_check.isChecked()
