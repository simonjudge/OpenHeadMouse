"""Main HeadMouse window (spec gui/headmouse_gui.py)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QPushButton,
    QSpinBox,
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

ONE_EURO_PRESETS = {
    "Low filtering": {"min_cutoff": 2.0, "beta": 0.5, "d_cutoff": 1.0},
    "Medium filtering": {"min_cutoff": 1.0, "beta": 0.3, "d_cutoff": 1.0},
    "High filtering": {"min_cutoff": 0.4, "beta": 0.15, "d_cutoff": 1.0},
}


class HeadMouseWindow(QMainWindow):
    recenter_requested = Signal()
    kx_changed = Signal(float)
    ky_changed = Signal(float)
    dsp_config_changed = Signal(dict)
    load_session_requested = Signal()
    fitts_test_requested = Signal()
    recordings_folder_requested = Signal()
    tracking_state_changed = Signal(str)

    def __init__(
        self,
        initial_kx: float,
        initial_ky: float,
        hotkey: str = "<esc>",
        initial_dsp_config: dict | None = None,
        initial_provider: str = "uniface",
        initial_ui_config: dict | None = None,
        research_tools_enabled: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._close_handler = None
        self.setWindowTitle("HeadMouse")
        self.tracking_state_changed.connect(self.set_status)

        self._create_menu(research_tools_enabled)

        ui_config = initial_ui_config or {}
        self.setup_panel = SetupPanel(initial_provider=initial_provider)
        self.telemetry = TelemetryWidget()
        self.controls = ControlPanel(
            recording_enabled=bool(ui_config.get("recording_enabled", True)),
            mouse_control_enabled=bool(ui_config.get("mouse_control_enabled", True)),
            calibration_enabled=bool(ui_config.get("calibration_enabled", False)),
            dwell_config=ui_config.get("dwell_click", {}),
        )
        dsp_config = initial_dsp_config or {}
        velocity_config = dsp_config.get("velocity", {})
        auto_zero_config = dsp_config.get("auto_zero", {})
        drift_config = dsp_config.get("yaw_drift_correction", {})

        self._kx_spin = self._make_gain_spin(initial_kx)
        self._ky_spin = self._make_gain_spin(initial_ky)
        self._kx_spin.valueChanged.connect(lambda value: self.kx_changed.emit(float(value)))
        self._ky_spin.valueChanged.connect(lambda value: self.ky_changed.emit(float(value)))

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Absolute", userData="absolute")
        self._mode_combo.addItem("Velocity", userData="velocity")
        mode_index = self._mode_combo.findData(dsp_config.get("mode", "absolute"))
        self._mode_combo.setCurrentIndex(max(mode_index, 0))
        self._mode_combo.setToolTip(
            "Absolute maps head angle to a screen position. Velocity maps head movement speed to cursor movement."
        )
        self._velocity_kx_spin = self._make_gain_spin(float(velocity_config.get("kx", 1.0)))
        self._velocity_ky_spin = self._make_gain_spin(float(velocity_config.get("ky", 1.0)))
        self._gamma_spin = self._make_spin(float(velocity_config.get("gamma", 1.0)), 0.1, 5.0, step=0.1)
        self._deadband_spin = self._make_spin(
            float(velocity_config.get("deadband_deg_s", 3.0)), 0.0, 1000.0, step=0.1
        )
        filter_config = dsp_config.get("one_euro_filter", {})
        self._one_euro_check = QCheckBox("One Euro filter")
        self._one_euro_check.setChecked(bool(filter_config.get("enabled", False)))
        self._filter_preset_combo = QComboBox()
        for name, values in ONE_EURO_PRESETS.items():
            self._filter_preset_combo.addItem(name, userData=values)
        self._select_filter_preset(filter_config)
        self._auto_zero_check = QCheckBox("Auto-zero")
        self._auto_zero_check.setChecked(bool(auto_zero_config.get("enabled", False)))
        self._drift_check = QCheckBox("Yaw drift")
        self._drift_check.setChecked(bool(drift_config.get("enabled", False)))

        gain_box = QGroupBox("DSP", self)
        self._absolute_options = QGroupBox("Absolute mapping", gain_box)
        absolute_grid = self._make_pair_grid(self._absolute_options)
        self._add_grid_field(
            absolute_grid, 0, 0, "H gain", self._kx_spin,
            "Horizontal gain: pixels moved horizontally per degree of yaw. Positive values move the cursor in the direction you turn; negative values invert it.",
        )
        self._add_grid_field(
            absolute_grid, 0, 1, "V gain", self._ky_spin,
            "Vertical gain: pixels moved vertically per degree of pitch. Positive values move the cursor up when you look up; negative values invert it.",
        )
        self._filter_options = QGroupBox("Adaptive smoothing", gain_box)
        filter_form = QFormLayout(self._filter_options)
        filter_form.addRow(self._one_euro_check)
        self._add_labeled_row(
            filter_form,
            "Level",
            self._filter_preset_combo,
            "Filtering level: Low is most responsive, Medium balances stability and response, and High gives the steadiest fine pointing.",
        )

        self._velocity_options = QGroupBox("Velocity mapping", gain_box)
        velocity_grid = self._make_pair_grid(self._velocity_options)
        self._add_grid_field(
            velocity_grid, 0, 0, "H gain", self._velocity_kx_spin,
            "Horizontal gain: scales horizontal cursor speed produced by yaw speed. Positive values move the cursor in the direction you turn; negative values invert it.",
        )
        self._add_grid_field(
            velocity_grid, 0, 1, "V gain", self._velocity_ky_spin,
            "Vertical gain: scales vertical cursor speed produced by pitch speed. Positive values move the cursor up when you look up; negative values invert it.",
        )
        self._add_grid_field(
            velocity_grid, 1, 0, "Accel (γ)", self._gamma_spin,
            "Acceleration (gamma): controls non-linear acceleration. 1.0 is linear; values above 1 make fast head movements accelerate more strongly.",
        )
        self._add_grid_field(
            velocity_grid, 1, 1, "Deadband", self._deadband_spin,
            "Deadband (deg/s): ignores angular speeds below this threshold to prevent small involuntary movements from moving the cursor.",
        )
        self._one_euro_check.setToolTip(
            "Optionally smooths absolute-mode jitter: stronger smoothing during slow movement and less during fast movement."
        )
        self._auto_zero_check.setToolTip(
            "Auto-zero bias: learns and removes a small stationary motion bias after the head remains still."
        )
        self._drift_check.setToolTip(
            "Yaw drift correction: slowly estimates and removes yaw-rate drift after sustained low-speed motion in Velocity mode."
        )
        velocity_grid.addWidget(self._auto_zero_check, 2, 0, 1, 2)
        velocity_grid.addWidget(self._drift_check, 2, 2, 1, 2)

        self._dsp_box = gain_box
        gain_box.setVisible(self.controls.mouse_control_enabled)
        self.controls.mouse_control_toggled.connect(gain_box.setVisible)
        gain_form = QFormLayout(gain_box)
        self._add_labeled_row(
            gain_form,
            "Mode",
            self._mode_combo,
            "Select whether head angle sets an absolute cursor position or head speed drives cursor velocity.",
        )
        gain_form.addRow(self._absolute_options)
        gain_form.addRow(self._velocity_options)
        gain_form.addRow(self._filter_options)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        for control in (
            self._velocity_kx_spin,
            self._velocity_ky_spin,
            self._gamma_spin,
            self._deadband_spin,
        ):
            control.valueChanged.connect(self._emit_dsp_config)
        self._filter_preset_combo.currentIndexChanged.connect(self._emit_dsp_config)
        for control in (self._one_euro_check, self._auto_zero_check, self._drift_check):
            control.toggled.connect(self._emit_dsp_config)
        self._update_mode_visibility()

        self._recenter_button = QPushButton("Recenter")
        self._recenter_button.setToolTip(
            "Take the current head pose as the new neutral (centre) position."
        )
        self._recenter_button.clicked.connect(self.recenter_requested)

        self._status_label = QLabel("PAUSED")
        self._status_label.setToolTip(
            "Tracking state: ACTIVE moves the pointer, PAUSED holds it, DISABLED means no provider is running."
        )
        self._status_label.setStyleSheet(f"font-weight: bold; color: {_STATE_COLORS['paused']};")

        self._hotkey_label = QLabel(f"Toggle tracking: {hotkey}")
        self._hotkey_label.setToolTip("Press this key at any time to pause or resume pointer tracking.")
        self._hotkey_label.setStyleSheet("color: #666; font-size: 10pt;")

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.addWidget(self._recenter_button)
        status_row.addWidget(self._status_label)
        status_row.addWidget(self._hotkey_label, stretch=1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addWidget(self.setup_panel)
        options = QWidget()
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.addWidget(self.controls, 1, Qt.AlignTop)
        mouse_options = QVBoxLayout()
        mouse_options.addWidget(gain_box)
        mouse_options.addWidget(self.telemetry)
        mouse_options.addStretch()
        options_layout.addLayout(mouse_options, 1)
        self._options_scroll = QScrollArea()
        self._options_scroll.setWidgetResizable(True)
        self._options_scroll.setWidget(options)
        layout.addWidget(self._options_scroll, 1)
        layout.addLayout(status_row)
        self.setCentralWidget(central)
        screen = self.screen()
        area = screen.availableGeometry() if screen is not None else None
        self.resize(
            min(1000, max(1, area.width() - 40)) if area is not None else 1000,
            min(740, max(1, area.height() - 60)) if area is not None else 740,
        )

    def _create_menu(self, research_tools_enabled: bool) -> None:
        from gui.window_actions import add_close_action

        self._recordings_folder_action = QAction("Set Recordings Folder...", self)
        self._recordings_folder_action.triggered.connect(self.recordings_folder_requested)
        # Read-only line under it, so the folder in use is visible at a glance.
        self._recordings_folder_info_action = QAction("", self)
        self._recordings_folder_info_action.setEnabled(False)
        self._recordings_dir_text = ""
        self.set_recordings_dir(None)

        file_menu = self.menuBar().addMenu("File")
        file_menu.setToolTipsVisible(True)
        if research_tools_enabled:
            open_visualizer_action = QAction("Open Session Viewer...", self)
            open_visualizer_action.triggered.connect(self.load_session_requested)
            file_menu.addAction(open_visualizer_action)

            open_fitts_action = QAction("Open Fitts' Law Test...", self)
            open_fitts_action.triggered.connect(self.fitts_test_requested)
            file_menu.addAction(open_fitts_action)
            file_menu.addSeparator()
        file_menu.addAction(self._recordings_folder_action)
        file_menu.addAction(self._recordings_folder_info_action)
        self._close_action = add_close_action(self, file_menu)

    _NO_RECORDINGS_DIR_TEXT = "Not set - you'll be asked when recording starts"

    @property
    def recordings_dir_text(self) -> str:
        return self._recordings_dir_text

    def set_recordings_dir(self, base_dir) -> None:
        """Show the folder head sessions and Fitts results are written under."""
        self._recordings_dir_text = (
            self._NO_RECORDINGS_DIR_TEXT if base_dir is None else str(base_dir)
        )
        help_text = (
            "Choose the folder that head-movement sessions and Fitts' Law results are saved "
            "under, in its 'sessions' and 'fitts_sessions' subfolders.\n"
            f"Current folder: {self._recordings_dir_text}"
        )
        self._recordings_folder_action.setToolTip(help_text)
        self._recordings_folder_action.setStatusTip(f"Recordings folder: {self._recordings_dir_text}")
        self._recordings_folder_info_action.setText(f"    Saving to: {self._recordings_dir_text}")

    def set_status(self, state_name: str) -> None:
        color = _STATE_COLORS.get(state_name, "#000000")
        self._status_label.setText(state_name.upper())
        self._status_label.setStyleSheet(f"font-weight: bold; color: {color};")

    def set_close_handler(self, handler) -> None:
        """Register a cleanup callback; returning False keeps the window open."""
        self._close_handler = handler

    def closeEvent(self, event) -> None:
        if self._close_handler is not None and self._close_handler() is False:
            event.ignore()
            return
        super().closeEvent(event)

    def set_hotkey_status(self, status: str) -> None:
        self._hotkey_label.setText(status)
        self._hotkey_label.setStyleSheet("color: #c62828; font-size: 10pt;")

    def set_dsp_config(self, config: dict) -> None:
        """Replace all movement controls without emitting live-edit signals."""
        absolute = config.get("absolute", {})
        velocity = config.get("velocity", {})
        one_euro = config.get("one_euro_filter", {})
        auto_zero = config.get("auto_zero", {})
        drift = config.get("yaw_drift_correction", {})
        controls = (
            self._kx_spin,
            self._ky_spin,
            self._mode_combo,
            self._velocity_kx_spin,
            self._velocity_ky_spin,
            self._gamma_spin,
            self._deadband_spin,
            self._one_euro_check,
            self._filter_preset_combo,
            self._auto_zero_check,
            self._drift_check,
        )
        previous = [control.blockSignals(True) for control in controls]
        try:
            self._kx_spin.setValue(round(float(absolute.get("kx", 1.0))))
            self._ky_spin.setValue(round(float(absolute.get("ky", 1.0))))
            mode_index = self._mode_combo.findData(config.get("mode", "absolute"))
            self._mode_combo.setCurrentIndex(max(mode_index, 0))
            self._velocity_kx_spin.setValue(float(velocity.get("kx", 1.0)))
            self._velocity_ky_spin.setValue(float(velocity.get("ky", 1.0)))
            self._gamma_spin.setValue(float(velocity.get("gamma", 1.0)))
            self._deadband_spin.setValue(float(velocity.get("deadband_deg_s", 0.0)))
            self._one_euro_check.setChecked(bool(one_euro.get("enabled", False)))
            self._select_filter_preset(one_euro)
            self._auto_zero_check.setChecked(bool(auto_zero.get("enabled", False)))
            self._drift_check.setChecked(bool(drift.get("enabled", False)))
            self._update_mode_visibility()
        finally:
            for control, was_blocked in zip(controls, previous):
                control.blockSignals(was_blocked)

    def update_telemetry(self, yaw_deg: float, pitch_deg: float, roll_deg: float) -> None:
        self.telemetry.update_angles(yaw_deg, pitch_deg, roll_deg)

    @staticmethod
    def _make_spin(
        value: float,
        minimum: float,
        maximum: float,
        step: float = 1.0,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    @staticmethod
    def _make_gain_spin(value: float) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-1000, 1000)
        spin.setSingleStep(1)
        spin.setValue(round(value))
        return spin

    @staticmethod
    def _make_pair_grid(parent: QWidget) -> QGridLayout:
        """Two label/field pairs per row."""
        grid = QGridLayout(parent)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return grid

    @staticmethod
    def _add_grid_field(
        grid: QGridLayout, row: int, column: int, text: str, field: QWidget, tooltip: str
    ) -> None:
        label = QLabel(text)
        label.setToolTip(tooltip)
        field.setToolTip(tooltip)
        grid.addWidget(label, row, column * 2)
        grid.addWidget(field, row, column * 2 + 1)

    @staticmethod
    def _add_labeled_row(form: QFormLayout, text: str, field: QWidget, tooltip: str) -> None:
        label = QLabel(text)
        label.setToolTip(tooltip)
        field.setToolTip(tooltip)
        form.addRow(label, field)

    def _on_mode_changed(self) -> None:
        self._update_mode_visibility()
        self._emit_dsp_config()

    def _update_mode_visibility(self) -> None:
        velocity_mode = self._mode_combo.currentData() == "velocity"
        self._absolute_options.setVisible(not velocity_mode)
        self._velocity_options.setVisible(velocity_mode)

    def _select_filter_preset(self, config: dict) -> None:
        custom_index = self._filter_preset_combo.findText("Custom (from config)")
        if custom_index >= 0:
            self._filter_preset_combo.removeItem(custom_index)
        target = {
            "min_cutoff": float(config.get("min_cutoff", 1.0)),
            "beta": float(config.get("beta", 0.3)),
            "d_cutoff": float(config.get("d_cutoff", 1.0)),
        }
        for index in range(self._filter_preset_combo.count()):
            if self._filter_preset_combo.itemData(index) == target:
                self._filter_preset_combo.setCurrentIndex(index)
                return
        self._filter_preset_combo.addItem("Custom (from config)", userData=target)
        self._filter_preset_combo.setCurrentIndex(self._filter_preset_combo.count() - 1)

    def _emit_dsp_config(self) -> None:
        filter_preset = self._filter_preset_combo.currentData()
        self.dsp_config_changed.emit(
            {
                "mode": self._mode_combo.currentData(),
                "velocity_kx": self._velocity_kx_spin.value(),
                "velocity_ky": self._velocity_ky_spin.value(),
                "gamma": self._gamma_spin.value(),
                "deadband_deg_s": self._deadband_spin.value(),
                "one_euro_enabled": self._one_euro_check.isChecked(),
                "one_euro_min_cutoff": filter_preset["min_cutoff"],
                "one_euro_beta": filter_preset["beta"],
                "one_euro_d_cutoff": filter_preset["d_cutoff"],
                "auto_zero_enabled": self._auto_zero_check.isChecked(),
                "yaw_drift_enabled": self._drift_check.isChecked(),
            }
        )
