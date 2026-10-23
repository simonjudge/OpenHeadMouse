"""Setup panel: provider selection and start/stop control (spec gui/setup_gui.py).

AirPods is only offered on macOS because the helper depends on CoreMotion.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from providers.ximu3_settings import MOUNTING_ORIENTATIONS


class SetupPanel(QWidget):
    start_requested = Signal(str)  # provider name, e.g. "uniface"
    stop_requested = Signal()
    provider_changed = Signal(str)
    ximu3_ignore_magnetometer_toggled = Signal(bool)
    ximu3_mounting_orientation_changed = Signal(str)

    def __init__(
        self,
        parent=None,
        show_airpods: bool | None = None,
        initial_provider: str = "uniface",
    ):
        super().__init__(parent)
        if show_airpods is None:
            show_airpods = sys.platform == "darwin"

        self._provider_combo = QComboBox()
        self._provider_combo.addItem("UniFace (webcam)", userData="uniface")
        self._provider_combo.addItem("OpenSeeFace (webcam)", userData="openseeface")
        self._provider_combo.addItem("xIMU3 (IMU)", userData="ximu3")
        if show_airpods:
            self._provider_combo.addItem("AirPods (CoreMotion)", userData="airpods")
        self._provider_combo.setToolTip(
            "Head-tracking input: UniFace uses the webcam with MediaPipe; OpenSeeFace "
            "uses the webcam with OpenSeeFace's models; xIMU3 uses a head-mounted IMU"
            + (", AirPods their motion sensors." if show_airpods else ".")
        )
        provider_index = self._provider_combo.findData(initial_provider)
        if provider_index >= 0:
            self._provider_combo.setCurrentIndex(provider_index)

        self._start_button = QPushButton("Start")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)
        self._start_button.setToolTip("Connect to the selected provider and start head tracking.")
        self._stop_button.setToolTip("Stop head tracking (and save the recording if Recording is on).")
        self._close_button = QPushButton("Close")
        self._close_button.setToolTip("Close the OpenHeadMouse setup window")
        self._close_button.clicked.connect(lambda: self.window().close())

        self._start_button.clicked.connect(self._on_start_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)

        self._ximu3_ignore_mag_button = QPushButton("AHRS: Ignore Magnetometer (Off)")
        self._ximu3_ignore_mag_button.setCheckable(True)
        self._ximu3_ignore_mag_button.setToolTip(
            "Ignore the xIMU3 magnetometer in orientation estimation. Use near metal or "
            "electronics that disturb the compass; yaw may then drift slowly."
        )
        self._ximu3_ignore_mag_button.toggled.connect(self._on_ximu3_ignore_mag_toggled)

        self._ximu3_mounting_label = QLabel("Mounting:")
        self._ximu3_mounting_label.setToolTip("How the xIMU3 sensor is worn on the head.")
        self._ximu3_mounting_combo = QComboBox()
        for name in MOUNTING_ORIENTATIONS:
            self._ximu3_mounting_combo.addItem(name, userData=name)
        self._ximu3_mounting_combo.setToolTip(
            "Horizontal: USB points backward, hanging from glasses.\n"
            "Vertical: USB points down, mounted on the temple.\n"
            "Printed arrows face out of the head in all orientations."
        )
        self._ximu3_mounting_combo.currentIndexChanged.connect(self._on_ximu3_mounting_changed)

        selection_row = QHBoxLayout()
        selection_row.addWidget(self._provider_combo)
        selection_row.addWidget(self._start_button)
        selection_row.addWidget(self._stop_button)
        selection_row.addWidget(self._close_button)

        box = QGroupBox("Input Provider")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(selection_row)
        ximu3_row = QHBoxLayout()
        ximu3_row.addWidget(self._ximu3_ignore_mag_button)
        ximu3_row.addWidget(self._ximu3_mounting_label)
        ximu3_row.addWidget(self._ximu3_mounting_combo)
        box_layout.addLayout(ximu3_row)
        self._connection_status = QLabel()
        self._connection_status.setVisible(False)
        box_layout.addWidget(self._connection_status)

        layout = QVBoxLayout(self)
        layout.addWidget(box)

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._update_ximu3_button_visibility()

    def _on_provider_changed(self) -> None:
        self._update_ximu3_button_visibility()
        self.provider_changed.emit(self._provider_combo.currentData())

    def _update_ximu3_button_visibility(self) -> None:
        visible = self._provider_combo.currentData() == "ximu3"
        self._ximu3_ignore_mag_button.setVisible(visible)
        self._ximu3_mounting_label.setVisible(visible)
        self._ximu3_mounting_combo.setVisible(visible)

    def _on_ximu3_mounting_changed(self) -> None:
        name = self._ximu3_mounting_combo.currentData()
        if name is not None:
            self.ximu3_mounting_orientation_changed.emit(name)

    def set_ximu3_axes_configuration(self, alignment: str, convention: int) -> None:
        """Restore a preset without emitting; leave custom configurations unselected."""
        name = next(
            (name for name, settings in MOUNTING_ORIENTATIONS.items()
             if settings == (alignment.replace(" ", "").upper(), convention)),
            None,
        )
        self._ximu3_mounting_combo.blockSignals(True)
        self._ximu3_mounting_combo.setPlaceholderText("Custom (from config)")
        self._ximu3_mounting_combo.setCurrentIndex(self._ximu3_mounting_combo.findData(name))
        self._ximu3_mounting_combo.blockSignals(False)

    def _on_ximu3_ignore_mag_toggled(self, checked: bool) -> None:
        self._ximu3_ignore_mag_button.setText(
            f"AHRS: Ignore Magnetometer ({'On' if checked else 'Off'})"
        )
        self.ximu3_ignore_magnetometer_toggled.emit(checked)

    @property
    def ximu3_ignore_magnetometer_enabled(self) -> bool:
        return self._ximu3_ignore_mag_button.isChecked()

    def set_ximu3_ignore_magnetometer(self, enabled: bool) -> None:
        """Sync the button's checked state (e.g. from config) without re-emitting the signal."""
        self._ximu3_ignore_mag_button.blockSignals(True)
        self._ximu3_ignore_mag_button.setChecked(enabled)
        self._ximu3_ignore_mag_button.setText(
            f"AHRS: Ignore Magnetometer ({'On' if enabled else 'Off'})"
        )
        self._ximu3_ignore_mag_button.blockSignals(False)

    def _on_start_clicked(self) -> None:
        provider_name = self._provider_combo.currentData()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._provider_combo.setEnabled(False)
        self.start_requested.emit(provider_name)

    def _on_stop_clicked(self) -> None:
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._provider_combo.setEnabled(True)
        self.stop_requested.emit()

    def request_start(self) -> None:
        """Start the selected provider exactly as clicking Start would.

        Used by other windows (e.g. the Fitts' Law test) that need the head
        tracker running with the user's current provider/settings.
        """
        if self._start_button.isEnabled():
            self._on_start_clicked()

    def reset_to_stopped(self) -> None:
        """Reset UI to stopped state after a failed start attempt."""
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._provider_combo.setEnabled(True)

    def set_connection_status(self, text: str) -> None:
        self._connection_status.setText(text)
        self._connection_status.setVisible(bool(text))

    def keep_stop_enabled(self) -> None:
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._provider_combo.setEnabled(False)
