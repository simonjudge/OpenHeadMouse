"""Main HeadMouse window (spec gui/headmouse_gui.py).

DSP gain tuning (Mode A only for M6), Recenter, and the ACTIVE/PAUSED/
DISABLED status indicator. Wires the setup panel and telemetry widget
together; main.py owns the provider -> dsp_engine -> headmouse consumer loop
and calls into this window's update methods.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.setup_gui import SetupPanel
from gui.vis_gui import TelemetryWidget
from gui.control_panel import ControlPanel

_STATE_COLORS = {
    "active": "#2e7d32",
    "paused": "#f9a825",
    "disabled": "#c62828",
}


class HeadMouseWindow(QMainWindow):
    recenter_requested = Signal()
    kx_changed = Signal(float)
    ky_changed = Signal(float)
    load_session_requested = Signal()
    fitts_test_requested = Signal()

    def __init__(self, initial_kx: float, initial_ky: float, hotkey: str = "<cmd>+<alt>+m", parent=None):
        super().__init__(parent)
        self.setWindowTitle("HeadMouse")

        self._create_menu()

        self.setup_panel = SetupPanel()
        self.telemetry = TelemetryWidget()
        self.controls = ControlPanel()

        self._kx_spin = QDoubleSpinBox()
        self._kx_spin.setRange(-1000.0, 1000.0)
        self._kx_spin.setDecimals(2)
        self._kx_spin.setValue(initial_kx)
        self._ky_spin = QDoubleSpinBox()
        self._ky_spin.setRange(-1000.0, 1000.0)
        self._ky_spin.setDecimals(2)
        self._ky_spin.setValue(initial_ky)
        self._kx_spin.valueChanged.connect(self.kx_changed)
        self._ky_spin.valueChanged.connect(self.ky_changed)

        gain_box = QGroupBox("Mode A Gains")
        gain_form = QFormLayout(gain_box)
        gain_form.addRow("kx", self._kx_spin)
        gain_form.addRow("ky", self._ky_spin)

        self._recenter_button = QPushButton("Recenter")
        self._recenter_button.clicked.connect(self.recenter_requested)

        self._status_label = QLabel("PAUSED")
        self._status_label.setStyleSheet(f"font-weight: bold; color: {_STATE_COLORS['paused']};")

        self._hotkey_label = QLabel(f"Toggle tracking: {hotkey}")
        self._hotkey_label.setStyleSheet("color: #666; font-size: 10pt;")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.setup_panel)
        layout.addWidget(self.controls)
        layout.addWidget(gain_box)
        layout.addWidget(self._recenter_button)
        layout.addWidget(self._status_label)
        layout.addWidget(self._hotkey_label)
        layout.addWidget(self.telemetry)
        self.setCentralWidget(central)

    def _create_menu(self) -> None:
        open_visualizer_action = QAction("Open Session Viewer...", self)
        open_visualizer_action.triggered.connect(self.load_session_requested)

        open_fitts_action = QAction("Open Fitts' Law Test...", self)
        open_fitts_action.triggered.connect(self.fitts_test_requested)

        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(open_visualizer_action)
        file_menu.addAction(open_fitts_action)

    def set_status(self, state_name: str) -> None:
        color = _STATE_COLORS.get(state_name, "#000000")
        self._status_label.setText(state_name.upper())
        self._status_label.setStyleSheet(f"font-weight: bold; color: {color};")

    def update_telemetry(self, yaw_deg: float, pitch_deg: float, roll_deg: float) -> None:
        self.telemetry.update_angles(yaw_deg, pitch_deg, roll_deg)
