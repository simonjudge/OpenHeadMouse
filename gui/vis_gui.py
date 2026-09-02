"""Live telemetry readout (spec gui/vis_gui.py).

Reduced scope for the M6 vertical slice: just a live Pitch/Yaw/Roll numeric
readout. The 3D mesh viewer, strip charts, and session playback are added in
later milestones (M10/M11).
"""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class TelemetryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._yaw_label = QLabel("Yaw:   --.-°")
        self._pitch_label = QLabel("Pitch: --.-°")
        self._roll_label = QLabel("Roll:  --.-°")
        for label in (self._yaw_label, self._pitch_label, self._roll_label):
            label.setStyleSheet("font-family: monospace; font-size: 14pt;")

        box = QGroupBox("Telemetry")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(self._yaw_label)
        box_layout.addWidget(self._pitch_label)
        box_layout.addWidget(self._roll_label)

        layout = QVBoxLayout(self)
        layout.addWidget(box)

    def update_angles(self, yaw_deg: float, pitch_deg: float, roll_deg: float) -> None:
        self._yaw_label.setText(f"Yaw:   {yaw_deg:+6.1f}°")
        self._pitch_label.setText(f"Pitch: {pitch_deg:+6.1f}°")
        self._roll_label.setText(f"Roll:  {roll_deg:+6.1f}°")
