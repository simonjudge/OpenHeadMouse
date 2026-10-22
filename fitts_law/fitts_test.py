"""Standalone Fitts' Law test suite window.

Run directly (`python fitts_law/fitts_test.py`) or launch from the main
OpenHeadMouse window's File menu (see main.py's _on_fitts_test_requested).
Deliberately has zero coupling to core/headmouse.py - it only reacts to
ordinary Qt mouse events on its own canvas, agnostic to whatever is actually
driving the OS cursor (head tracking, a trackpad, a switch, etc).
The "Start with OpenHeadMouse" mode and the "Record Head Movement" option keep
that separation: the host application registers a callable
(set_head_start_provider) that starts its own tracker, with or without letting
it drive the cursor.
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.clock import host_time
from core import recording_paths
from gui.window_actions import add_close_action
from fitts_law.conditions import (
    build_condition_set,
    generate_circle_positions,
    generate_edge_extremity_targets,
    max_safe_circle_radius,
    multi_directional_sequence,
)
from fitts_law.keyboard import PROMPT_HEIGHT_PX, TYPING_WORDS, build_keyboard_layout, key_at
from fitts_law.pretest import PreTestTaskOverlay, build_pretest_stages
from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult
from fitts_law.fitts_settings import FittsSettings
from fitts_law.stimuli import EMOJI_CHOICES, STIMULUS_CHOICES, CircleStimulus, EmojiStimulus, ImageStimulus, Stimulus

MODE_ISO_STANDARD = "ISO Standard"
MODE_ISO_EDGE = "ISO Standard + Edge Targets"
MODE_TYPING = "Typing Test"

KEY_SIZE_CONSISTENT = "Consistent key size"
KEY_SIZE_RANDOM = "Random key size"

TASK_COMBINED = "HeadMovement and Mouse Task"
TASK_HEAD_ONLY = "HeadMovement only"
TASK_MOUSE_ONLY = "Mouse Task only"

START_NORMAL = "Start test"
START_DWELL = "Start with dwell click"
START_HEADMOUSE = "Start with OpenHeadMouse"


def _parse_number_list(text: str) -> list[float]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if part:
            values.append(float(part))
    return values


class FittsTrialCanvas(QWidget):
    """Paints the current target and turns mouse clicks into TrialResults."""

    trial_completed = Signal(object)  # TrialResult
    all_trials_completed = Signal()

    _TRAJECTORY_MIN_INTERVAL_S = 1.0 / 60.0  # cap trajectory sampling to ~60Hz

    def __init__(self, trial_specs: list[dict], stimulus: Stimulus, parent=None):
        super().__init__(parent)
        self._specs = trial_specs
        self._stimulus = stimulus
        self._index = 0
        self._trial_start_time: float | None = None
        self._trajectory: list[list[float]] = []
        self._last_trajectory_sample_time: float | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(400, 300)
        self._start_current_trial()

    def _start_current_trial(self) -> None:
        if self._index >= len(self._specs):
            self.all_trials_completed.emit()
            return
        self._trial_start_time = time.monotonic()
        self._trial_start_host_time = host_time()
        self._trajectory = []
        self._last_trajectory_sample_time = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._index < len(self._specs):
            spec = self._specs[self._index]
            w = spec["width_px"]
            x, y = spec["target_pos"]
            rect = QRectF(x - w / 2, y - w / 2, w, w)
            self._stimulus.paint(painter, rect, True)
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        if self._trial_start_time is None or self._index >= len(self._specs):
            return
        now = time.monotonic()
        if (
            self._last_trajectory_sample_time is not None
            and now - self._last_trajectory_sample_time < self._TRAJECTORY_MIN_INTERVAL_S
        ):
            return
        self._last_trajectory_sample_time = now
        pos = event.position()
        self._trajectory.append([now - self._trial_start_time, pos.x(), pos.y()])

    def mousePressEvent(self, event) -> None:
        if self._index >= len(self._specs) or self._trial_start_time is None:
            return
        spec = self._specs[self._index]
        pos = event.position()
        click_pos = (pos.x(), pos.y())
        end_time = time.monotonic()
        end_host_time = host_time()
        distance = math.dist(spec["target_pos"], click_pos)
        hit = distance <= spec["width_px"] / 2

        result = TrialResult(
            condition_label=spec["condition_label"],
            block_type=spec["block_type"],
            amplitude_px=spec["amplitude_px"],
            width_px=spec["width_px"],
            start_pos=spec["start_pos"],
            target_pos=spec["target_pos"],
            click_pos=click_pos,
            start_time=self._trial_start_time,
            end_time=end_time,
            movement_time_s=end_time - self._trial_start_time,
            hit=hit,
            trial_index=spec["trial_index"],
            repetition_index=spec["repetition_index"],
            trajectory=self._trajectory,
            start_host_time=self._trial_start_host_time,
            end_host_time=end_host_time,
        )
        self._index += 1
        self.trial_completed.emit(result)
        self._start_current_trial()


class TypingTrialCanvas(QWidget):
    """Paints a full-screen on-screen keyboard and a typing prompt; each
    keystroke becomes a TrialResult (previous key center -> next key center).
    Hit-testing is rectangular (real key shapes), unlike FittsTrialCanvas's
    circular stimulus hit-test - documented in fitts_law/keyboard.py.
    """

    trial_completed = Signal(object)  # TrialResult
    all_trials_completed = Signal()

    _TRAJECTORY_MIN_INTERVAL_S = 1.0 / 60.0

    def __init__(self, trial_specs: list[dict], layout: dict, parent=None):
        super().__init__(parent)
        self._specs = trial_specs
        self._layout = layout
        self._index = 0
        self._trial_start_time: float | None = None
        self._trajectory: list[list[float]] = []
        self._last_trajectory_sample_time: float | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(400, 300)
        self._start_current_trial()

    def _start_current_trial(self) -> None:
        if self._index >= len(self._specs):
            self.all_trials_completed.emit()
            return
        self._trial_start_time = time.monotonic()
        self._trial_start_host_time = host_time()
        self._trajectory = []
        self._last_trajectory_sample_time = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        spec = self._specs[self._index] if self._index < len(self._specs) else None
        layout = spec["layout"] if spec is not None else self._layout

        for letter, rect in layout.items():
            key_rect = QRectF(rect.x, rect.y, rect.width, rect.height)
            is_target = spec is not None and letter == spec["target_letter"]
            painter.setPen(QColor("#455a64"))
            painter.setBrush(QColor("#e53935") if is_target else QColor("#eceff1"))
            painter.drawRoundedRect(key_rect, 6, 6)
            painter.setPen(Qt.white if is_target else Qt.black)
            painter.drawText(key_rect, Qt.AlignCenter, letter)

        prompt_rect = QRectF(0, self.height() - PROMPT_HEIGHT_PX, self.width(), PROMPT_HEIGHT_PX)
        painter.fillRect(prompt_rect, QColor("#263238"))
        if spec is not None:
            word = spec["word"]
            word_start_index = next(i for i, s in enumerate(self._specs) if s["word"] == word and s["repetition_index"] == spec["repetition_index"])
            typed_so_far = self._index - word_start_index
            font = QFont()
            font.setPixelSize(28)
            painter.setFont(font)
            painter.setPen(QColor("#78909c"))
            painter.drawText(prompt_rect.adjusted(20, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, word[:typed_so_far])
            remaining_rect = QRectF(prompt_rect)
            metrics = painter.fontMetrics()
            offset = metrics.horizontalAdvance(word[:typed_so_far])
            remaining_rect.adjust(20 + offset, 0, 0, 0)
            painter.setPen(Qt.white)
            painter.drawText(remaining_rect, Qt.AlignVCenter | Qt.AlignLeft, word[typed_so_far:])


    def mouseMoveEvent(self, event) -> None:
        if self._trial_start_time is None or self._index >= len(self._specs):
            return
        now = time.monotonic()
        if (
            self._last_trajectory_sample_time is not None
            and now - self._last_trajectory_sample_time < self._TRAJECTORY_MIN_INTERVAL_S
        ):
            return
        self._last_trajectory_sample_time = now
        pos = event.position()
        self._trajectory.append([now - self._trial_start_time, pos.x(), pos.y()])

    def mousePressEvent(self, event) -> None:
        if self._index >= len(self._specs) or self._trial_start_time is None:
            return
        spec = self._specs[self._index]
        pos = event.position()
        click_pos = (pos.x(), pos.y())
        end_time = time.monotonic()
        end_host_time = host_time()
        clicked_letter = key_at(spec["layout"], *click_pos)
        hit = clicked_letter == spec["target_letter"]

        result = TrialResult(
            condition_label=spec["condition_label"],
            block_type=spec["block_type"],
            amplitude_px=spec["amplitude_px"],
            width_px=spec["width_px"],
            start_pos=spec["start_pos"],
            target_pos=spec["target_pos"],
            click_pos=click_pos,
            start_time=self._trial_start_time,
            end_time=end_time,
            movement_time_s=end_time - self._trial_start_time,
            hit=hit,
            trial_index=spec["trial_index"],
            repetition_index=spec["repetition_index"],
            trajectory=self._trajectory,
            start_host_time=self._trial_start_host_time,
            end_host_time=end_host_time,
        )
        self._index += 1
        self.trial_completed.emit(result)
        self._start_current_trial()
        self.update()


class FittsTestWindow(QMainWindow):
    _RECORD_HEAD_TOOLTIP = (
        "Record head movement alongside the Fitts data, using the tracker, "
        "calibration and recording settings of the OpenHeadMouse window.\n"
        "Start Test records head movement without driving the cursor, so the "
        "test is done with the access method being measured; Start with "
        "OpenHeadMouse records it and drives the cursor with the head.\n"
        "Selecting HeadMovement tasks also requests head recording."
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fitts' Law Test")
        self.resize(900, 700)

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else None
        self._screen_w = float(geometry.width()) if geometry else 1920.0
        self._screen_h = float(geometry.height()) if geometry else 1080.0

        self._logger = FittsResultsLogger()
        self._canvas: FittsTrialCanvas | TypingTrialCanvas | None = None
        self._typing_layout: dict | None = None
        self._settings = FittsSettings()
        self._saved_path: Path | None = None
        # Set by the host application (see main.py) so this window can stop and
        # save a running head recording before replaying it. Returns its path.
        self._head_session_provider = None
        # Set by the host application so the "Start with OpenHeadMouse" button can
        # bring up the selected head tracker before the test begins.
        self._head_start_provider = None
        self._head_phase_provider = None
        self._use_head_phase_provider = False
        # Set by the host application so results land in the user's chosen
        # recordings folder (and it can be asked for when first needed).
        self._recordings_dir_provider = None
        self._waiting_for_headmouse = False
        self._head_tracking_started = False
        self._dwell_provider = None
        self._dwell_service = None
        self._test_dwell_active = False
        self._mouse_task_provider = None
        self._host_run_active = False
        self._saved_head_path: Path | None = None
        self._finishing_run = False
        self._pretest_overlay: PreTestTaskOverlay | None = None
        self._pending_test: tuple[str, list[dict], Stimulus | None] | None = None
        self._playback_windows: list = []  # keep references so Qt/PySide doesn't GC them

        # Results default to the shared recordings folder when one is set.
        self._apply_results_dir(prompt=False)

        self._create_menu()

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._setup_page = self._build_setup_page()
        self._results_page = self._build_results_page()
        self._stack.addWidget(self._setup_page)  # index 0
        self._stack.addWidget(self._results_page)  # index 1 (trial canvas inserted at index 2 when started)

    def _current_screen_geometry(self) -> tuple[float, float]:
        """The width/height of whichever screen this window is actually
        displayed on right now (not necessarily the primary screen) - used so
        fullscreen layouts don't leave dead space when the window is on a
        secondary monitor with a different resolution."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen is not None else None
        w = float(geometry.width()) if geometry else 1920.0
        h = float(geometry.height()) if geometry else 1080.0
        return w, h

    def set_head_session_provider(self, provider) -> None:
        """Register a callable that stops the host app's head recording and
        returns the saved session path (or None if nothing was recorded)."""
        self._head_session_provider = provider

    def set_head_start_provider(self, provider) -> None:
        """Register a callable that starts the host app's head tracking.

        It is called as `provider(on_ready, mouse_control=...)`, must return
        False if the tracker could not be started, and must invoke
        `on_ready(started: bool)` once the selected provider is connected,
        has delivered data, and tracking is live (after any initial
        calibration), or once startup has been abandoned. `mouse_control` is
        False for a "Record Head Movement" run, where head movement is
        recorded but the cursor stays with the access method being tested.
        """
        self._head_start_provider = provider
        self._update_headmouse_button_state()

    def set_head_phase_provider(self, provider) -> None:
        """Register ``provider(phase, completed=True)`` for head-data labels."""
        self._head_phase_provider = provider

    def set_dwell_provider(self, provider) -> None:
        """Register ``provider(active: bool)`` for temporary test dwell clicking."""
        self._dwell_provider = provider

    def set_mouse_task_provider(self, provider) -> None:
        """Register ``provider(active: bool | None)`` to gate host dwell clicking."""
        self._mouse_task_provider = provider

    def _set_mouse_task_active(self, active: bool | None) -> None:
        if self._mouse_task_provider is not None:
            self._mouse_task_provider(active)

    def _stop_run_input(self) -> None:
        if not self._host_run_active:
            return
        self._set_mouse_task_active(False)
        self._stop_test_dwell()
        self._host_run_active = False
        self._head_tracking_started = False
        try:
            if self._head_session_provider is not None:
                path = self._head_session_provider()
                if path is not None:
                    self._saved_head_path = Path(path)
        except Exception as exc:
            QMessageBox.warning(self, "Could not stop recording", str(exc))
        finally:
            self._set_mouse_task_active(None)

    def _stop_test_dwell(self) -> None:
        if not self._test_dwell_active:
            return
        if self._dwell_provider is not None:
            self._dwell_provider(False)
        elif self._dwell_service is not None:
            self._dwell_service.stop()
        self._test_dwell_active = False

    def _start_test_dwell(self) -> None:
        if self._start_mode_combo.currentText() != START_DWELL:
            return
        try:
            if self._dwell_provider is not None:
                self._dwell_provider(True)
            else:
                from dwell_click.service import DwellClickService

                if self._dwell_service is None:
                    self._dwell_service = DwellClickService(parent=self)
                self._dwell_service.start()
            self._test_dwell_active = True
        except (OSError, RuntimeError, ImportError) as exc:
            self._on_back_to_setup_clicked()
            QMessageBox.critical(self, "Could not start dwell clicking", str(exc))

    def set_recordings_dir_provider(self, provider) -> None:
        """Register ``provider(prompt: bool) -> Path | None`` for the base folder.

        It returns the recordings folder chosen in the OpenHeadMouse window,
        asking the user for one when `prompt` is True and none has been set.
        Without a provider this window asks (and remembers the answer)
        itself, so a standalone run behaves the same way.
        """
        self._recordings_dir_provider = provider
        self._apply_results_dir(prompt=False)

    def _apply_results_dir(self, *, prompt: bool):
        """Point the results logger at the configured recordings folder.

        With `prompt` the folder is created and checked (asking for one if
        needed) and the returned folder is None only if the user cancelled.
        Without it the configured folder is just read, falling back to the
        logger's own default when none has been chosen.
        """
        if self._recordings_dir_provider is not None:
            base_dir = self._recordings_dir_provider(prompt=prompt)
        else:
            base_dir = self._standalone_base_dir(prompt=prompt)
        if base_dir is not None:
            self._logger.output_dir = recording_paths.fitts_sessions_dir(base_dir)
        return base_dir

    def _standalone_base_dir(self, *, prompt: bool):
        """The recordings folder when running without an OpenHeadMouse window.

        The shared config.json is read first so a folder chosen in the
        OpenHeadMouse GUI is honoured, then this window's own settings. With
        `prompt` the folder is made ready, asking for one when it is unset or
        unusable, and the answer is saved to both so it is only asked once.
        """
        base_dir = recording_paths.load_base_dir()
        if base_dir is None:
            stored = self._settings.get("recordings_base_dir")
            if stored is not None and str(stored).strip():
                base_dir = Path(str(stored)).expanduser()

        if not prompt:
            return base_dir

        ready = recording_paths.ensure_usable_base_dir(
            base_dir, self, "needed before the test can be recorded"
        )
        if ready is not None and ready != base_dir:
            self._save_standalone_base_dir(ready)
        return ready

    def _save_standalone_base_dir(self, base_dir: Path) -> None:
        """Remember the folder for the next standalone run."""
        self._settings.set("recordings_base_dir", str(base_dir))
        self._settings.save()
        # Best effort: sharing it through config.json keeps the OpenHeadMouse
        # window and this one pointing at the same folder.
        recording_paths.save_base_dir(base_dir)

    def _update_headmouse_button_state(self) -> None:
        if not hasattr(self, "_start_mode_combo"):
            return
        available = self._head_start_provider is not None
        item = self._start_mode_combo.model().item(2)
        item.setEnabled(available)
        self._start_mode_combo.setEnabled(not self._waiting_for_headmouse)
        start_button = getattr(self, "_start_button", None)
        if start_button is not None:
            start_button.setEnabled(
                not self._waiting_for_headmouse
                and (available or self._start_mode_combo.currentText() != START_HEADMOUSE)
            )
        record_head = getattr(self, "_record_head_checkbox", None)
        if record_head is not None:
            record_head.setToolTip(
                self._RECORD_HEAD_TOOLTIP
                if available
                else self._RECORD_HEAD_TOOLTIP + "\n\nUnavailable here: open this window "
                "from the OpenHeadMouse app (File > Fitts' Law Test) to record head movement. "
                "The pre-test tasks still record the cursor into the Fitts session file."
            )

    def _create_menu(self) -> None:
        open_action = QAction("Open Results...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_results)

        save_action = QAction("Save Results As...", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._on_save_results)

        self._sync_action = QAction("Open Synchronised Playback...", self)
        self._sync_action.setShortcut("Ctrl+Shift+O")
        self._sync_action.triggered.connect(self._on_open_synchronised)

        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(open_action)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        file_menu.addAction(self._sync_action)
        self._close_action = add_close_action(self, file_menu)

    # -- Setup page ----------------------------------------------------

    def _save_settings(self) -> None:
        """Save all current UI values to settings file."""
        self._settings.set("mode", self._mode_combo.currentText())
        self._settings.set("num_targets", self._num_targets_combo.currentText())
        self._settings.set("amplitudes", self._amplitudes_edit.text())
        self._settings.set("widths", self._widths_edit.text())
        self._settings.set("repetitions", self._repetitions_spin.value())
        self._settings.set("randomize_targets", self._randomize_targets_checkbox.isChecked())
        self._settings.set("stimulus", self._stimulus_combo.currentText())
        self._settings.set("icon", self._icon_combo.currentText())
        self._settings.set("typing_key_size", self._typing_key_size_combo.currentText())
        self._settings.set("typing_word_count", self._typing_word_count_spin.value())
        self._settings.set("typing_reassign_widths", self._typing_reassign_widths_checkbox.isChecked())
        self._settings.set("record_head_movement", self._record_head_checkbox.isChecked())
        self._settings.set("task_selection", self._task_combo.currentText())
        self._settings.set("start_mode", self._start_mode_combo.currentText())
        self._settings.set("pretest_move_duration_s", self._pretest_move_duration_spin.value())
        self._settings.set("pretest_hold_duration_s", self._pretest_hold_duration_spin.value())
        self._settings.save()

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        form = QFormLayout()
        self._task_combo = QComboBox()
        self._task_combo.addItems([TASK_COMBINED, TASK_HEAD_ONLY, TASK_MOUSE_ONLY])
        self._task_combo.setCurrentText(self._settings.get("task_selection", TASK_MOUSE_ONLY))
        self._task_combo.setToolTip(
            "HeadMovement includes both the initial rest position and range-of-movement "
            "tasks, and records the head tracker when launched from OpenHeadMouse."
        )
        form.addRow("Tasks", self._task_combo)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([MODE_ISO_STANDARD, MODE_ISO_EDGE, MODE_TYPING])
        self._mode_combo.setCurrentText(self._settings.get("mode", MODE_ISO_STANDARD))
        self._mode_combo.currentTextChanged.connect(self._update_mode_visibility)
        self._mode_combo.currentTextChanged.connect(lambda _: self._save_settings())
        form.addRow("Test mode", self._mode_combo)

        self._stimulus_combo = QComboBox()
        self._stimulus_combo.addItems(list(STIMULUS_CHOICES.keys()))
        self._stimulus_combo.setCurrentText(self._settings.get("stimulus", "Circle (ISO standard)"))
        self._stimulus_combo.currentTextChanged.connect(lambda _: self._save_settings())
        self._stimulus_combo.currentTextChanged.connect(self._update_stimulus_visibility)
        form.addRow("Stimulus", self._stimulus_combo)

        self._icon_combo = QComboBox()
        self._icon_combo.addItems(EMOJI_CHOICES)
        self._icon_combo.setCurrentText(self._settings.get("icon", "🎯"))
        self._icon_combo.currentTextChanged.connect(lambda _: self._save_settings())
        form.addRow("Icon", self._icon_combo)

        self._num_targets_combo = QComboBox()
        self._num_targets_combo.addItems(["3", "6", "9", "12", "13"])
        self._num_targets_combo.setCurrentText(self._settings.get("num_targets", "13"))
        self._num_targets_combo.currentTextChanged.connect(lambda _: self._save_settings())
        form.addRow("Circle targets (ISO modes)", self._num_targets_combo)

        self._amplitudes_edit = QLineEdit(self._settings.get("amplitudes", "300,600"))
        self._amplitudes_edit.textChanged.connect(lambda _: self._save_settings())
        form.addRow("Amplitudes (px, comma-separated)", self._amplitudes_edit)

        self._widths_edit = QLineEdit(self._settings.get("widths", "30,60"))
        self._widths_edit.textChanged.connect(lambda _: self._save_settings())
        form.addRow("Widths (px, comma-separated)", self._widths_edit)

        self._repetitions_spin = QSpinBox()
        self._repetitions_spin.setRange(1, 10)
        self._repetitions_spin.setValue(self._settings.get("repetitions", 2))
        self._repetitions_spin.valueChanged.connect(lambda _: self._save_settings())
        form.addRow("Repetitions per condition (ISO modes)", self._repetitions_spin)

        self._randomize_targets_checkbox = QCheckBox("Randomize target order")
        self._randomize_targets_checkbox.setChecked(self._settings.get("randomize_targets", False))
        self._randomize_targets_checkbox.stateChanged.connect(lambda _: self._save_settings())
        form.addRow("", self._randomize_targets_checkbox)

        self._typing_key_size_combo = QComboBox()
        self._typing_key_size_combo.addItems([KEY_SIZE_CONSISTENT, KEY_SIZE_RANDOM])
        self._typing_key_size_combo.setCurrentText(self._settings.get("typing_key_size", KEY_SIZE_CONSISTENT))
        self._typing_key_size_combo.currentTextChanged.connect(lambda _: self._save_settings())
        form.addRow("Key size (Typing Test)", self._typing_key_size_combo)

        self._typing_word_count_spin = QSpinBox()
        self._typing_word_count_spin.setRange(1, 30)
        self._typing_word_count_spin.setValue(self._settings.get("typing_word_count", 8))
        self._typing_word_count_spin.valueChanged.connect(lambda _: self._save_settings())
        form.addRow("Number of words (Typing Test)", self._typing_word_count_spin)

        self._typing_reassign_widths_checkbox = QCheckBox("Reassign random key sizes each word")
        self._typing_reassign_widths_checkbox.setChecked(self._settings.get("typing_reassign_widths", False))
        self._typing_reassign_widths_checkbox.stateChanged.connect(lambda _: self._save_settings())
        form.addRow("", self._typing_reassign_widths_checkbox)

        form.addRow(QLabel(""))  # blank separator line
        self._record_head_checkbox = QCheckBox("Record Head Movement")
        self._record_head_checkbox.setChecked(
            self._settings.get("record_head_movement", False)
        )
        self._record_head_checkbox.setToolTip(self._RECORD_HEAD_TOOLTIP)
        self._record_head_checkbox.stateChanged.connect(
            lambda _: self._save_settings()
        )
        self._record_head_checkbox.stateChanged.connect(
            lambda _: self._update_pretest_visibility()
        )
        form.addRow("Head movement", self._record_head_checkbox)

        self._pretest_move_duration_spin = QDoubleSpinBox()
        self._pretest_move_duration_spin.setRange(0.1, 60.0)
        self._pretest_move_duration_spin.setDecimals(1)
        self._pretest_move_duration_spin.setSingleStep(0.5)
        self._pretest_move_duration_spin.setSuffix(" s")
        self._pretest_move_duration_spin.setValue(
            self._settings.get("pretest_move_duration_s", 5.0)
        )
        self._pretest_move_duration_spin.valueChanged.connect(
            lambda _: self._save_settings()
        )
        form.addRow(
            "Time to move/read instructions",
            self._pretest_move_duration_spin,
        )

        self._pretest_hold_duration_spin = QDoubleSpinBox()
        self._pretest_hold_duration_spin.setRange(0.1, 60.0)
        self._pretest_hold_duration_spin.setDecimals(1)
        self._pretest_hold_duration_spin.setSingleStep(0.5)
        self._pretest_hold_duration_spin.setSuffix(" s")
        self._pretest_hold_duration_spin.setValue(
            self._settings.get("pretest_hold_duration_s", 5.0)
        )
        self._pretest_hold_duration_spin.valueChanged.connect(
            lambda _: self._save_settings()
        )
        form.addRow(
            "Time to hold/record position",
            self._pretest_hold_duration_spin,
        )

        form.addRow(QLabel(""))  # blank separator line
        self._access_method_combo = QComboBox()
        self._access_method_combo.addItems(["OpenHeadMouse", "EyeGaze", "Mouse", "Other"])
        self._access_method_combo.setCurrentText("OpenHeadMouse")
        self._access_method_combo.currentTextChanged.connect(self._update_access_method_visibility)
        form.addRow("Access method", self._access_method_combo)

        self._access_method_other_edit = QLineEdit()
        self._access_method_other_edit.setPlaceholderText("Specify other access method")
        form.addRow("Other (specify)", self._access_method_other_edit)

        self._session_notes_edit = QPlainTextEdit()
        self._session_notes_edit.setMaximumHeight(60)
        form.addRow("Session notes", self._session_notes_edit)

        self._iso_only_fields = [self._num_targets_combo, self._amplitudes_edit, self._repetitions_spin, self._randomize_targets_checkbox]
        self._iso_and_typing_fields = [self._widths_edit]
        self._typing_only_fields = [self._typing_key_size_combo, self._typing_word_count_spin, self._typing_reassign_widths_checkbox]
        self._non_typing_fields = [self._stimulus_combo]
        self._pretest_fields = [
            self._pretest_move_duration_spin,
            self._pretest_hold_duration_spin,
        ]
        self._form = form

        group = QGroupBox("Setup")
        group.setLayout(form)
        layout.addWidget(group)

        start_button = QPushButton("Start Test")
        start_button.setToolTip(
            "Start the selected tasks using the start mode chosen on the right."
        )
        start_button.clicked.connect(self._on_start_clicked)
        self._start_button = start_button

        self._start_mode_combo = QComboBox()
        self._start_mode_combo.addItems([START_NORMAL, START_DWELL, START_HEADMOUSE])
        self._start_mode_combo.setCurrentText(self._settings.get("start_mode", START_NORMAL))
        self._start_mode_combo.setToolTip(
            "Use the current cursor input, add dwell clicking, or start OpenHeadMouse "
            "using the main GUI settings (including its dwell-click setting)."
        )
        self._start_mode_combo.currentTextChanged.connect(lambda _: self._save_settings())
        self._start_mode_combo.currentTextChanged.connect(lambda _: self._update_headmouse_button_state())
        self._update_headmouse_button_state()

        start_row = QHBoxLayout()
        start_row.addWidget(start_button)
        start_row.addWidget(self._start_mode_combo)
        layout.addLayout(start_row)
        layout.addStretch()

        self._task_combo.currentTextChanged.connect(lambda _: self._save_settings())
        self._task_combo.currentTextChanged.connect(lambda _: self._update_pretest_visibility())
        self._update_mode_visibility(self._mode_combo.currentText())
        self._update_pretest_visibility()
        self._update_access_method_visibility(self._access_method_combo.currentText())
        self._update_stimulus_visibility(self._stimulus_combo.currentText())
        return page

    def _set_fields_visible(self, fields: list, visible: bool) -> None:
        for field in fields:
            field.setVisible(visible)
            label = self._form.labelForField(field)
            if label is not None:
                label.setVisible(visible)

    def _update_mode_visibility(self, mode: str) -> None:
        mouse_task = self._task_combo.currentText() != TASK_HEAD_ONLY
        is_iso = mouse_task and mode in (MODE_ISO_STANDARD, MODE_ISO_EDGE)
        is_typing = mouse_task and mode == MODE_TYPING

        self._set_fields_visible(self._iso_only_fields, is_iso)
        self._set_fields_visible(self._iso_and_typing_fields, is_iso or is_typing)
        self._set_fields_visible(self._typing_only_fields, is_typing)
        self._set_fields_visible(self._non_typing_fields, is_iso)
        self._update_stimulus_visibility(self._stimulus_combo.currentText())

    def _update_pretest_visibility(self) -> None:
        self._set_fields_visible(
            self._pretest_fields, self._task_combo.currentText() != TASK_MOUSE_ONLY
        )
        self._set_fields_visible([self._mode_combo], self._task_combo.currentText() != TASK_HEAD_ONLY)
        self._update_mode_visibility(self._mode_combo.currentText())

    def _update_access_method_visibility(self, access_method: str) -> None:
        is_other = access_method == "Other"
        self._access_method_other_edit.setVisible(is_other)
        label = self._form.labelForField(self._access_method_other_edit)
        if label is not None:
            label.setVisible(is_other)

    def _update_stimulus_visibility(self, stimulus: str) -> None:
        is_icon = (
            stimulus == "Icon"
            and self._task_combo.currentText() != TASK_HEAD_ONLY
            and self._mode_combo.currentText() != MODE_TYPING
        )
        self._icon_combo.setVisible(is_icon)
        label = self._form.labelForField(self._icon_combo)
        if label is not None:
            label.setVisible(is_icon)

    def _resolve_stimulus(self) -> Stimulus | None:
        choice = self._stimulus_combo.currentText()
        if choice == "Custom image...":
            filepath, _ = QFileDialog.getOpenFileName(self, "Choose target image", "", "Images (*.png *.jpg *.jpeg *.gif)")
            if not filepath:
                return None
            pixmap = QPixmap(filepath)
            if pixmap.isNull():
                QMessageBox.warning(self, "Invalid image", "Could not load that image; using circle stimulus instead.")
                return CircleStimulus()
            return ImageStimulus(pixmap)
        if choice == "Icon":
            return EmojiStimulus(self._icon_combo.currentText())
        return CircleStimulus()

    def _on_start_with_headmouse_clicked(self) -> None:
        """Bring up the head tracker selected in the OpenHeadMouse window, then run the test."""
        if self._head_start_provider is None:
            QMessageBox.information(
                self,
                "OpenHeadMouse unavailable",
                "Open this window from the OpenHeadMouse app (File > Fitts' Law Test) to start "
                "the head tracker from here.",
            )
            self._stop_run_input()
            return

        self._access_method_combo.setCurrentText("OpenHeadMouse")
        # Driving the cursor with the head always records it too.
        self._record_head_checkbox.setChecked(True)
        self._start_button.setText("Starting OpenHeadMouse...")
        self._head_tracking_started = True
        if not self._request_head_tracking(mouse_control=True):
            self._head_tracking_started = False
            self._stop_run_input()

    def _request_head_tracking(self, *, mouse_control: bool) -> bool:
        """Ask the host app to bring head tracking up; True while we wait for it.

        `mouse_control` decides whether head movement also drives the cursor:
        a "Record Head Movement" run leaves the cursor to the access method
        actually being measured.
        """
        self._waiting_for_headmouse = True
        if not mouse_control:
            self._start_button.setText("Starting head recording...")
        self._update_headmouse_button_state()

        try:
            started = self._head_start_provider(
                self._on_headmouse_ready, mouse_control=mouse_control
            )
        except Exception as exc:
            self._reset_headmouse_button()
            QMessageBox.critical(self, "Could not start OpenHeadMouse", str(exc))
            return False
        if started is False:
            self._reset_headmouse_button()
            return False
        return True

    def _reset_headmouse_button(self) -> None:
        self._waiting_for_headmouse = False
        self._start_button.setText("Start Test")
        self._update_headmouse_button_state()

    def _on_headmouse_ready(self, started: bool) -> None:
        """Called back once head tracking (and any calibration) is live."""
        if not self._waiting_for_headmouse:
            return
        self._reset_headmouse_button()
        if not started:
            self._head_tracking_started = False
            self._stop_run_input()
            QMessageBox.warning(
                self,
                "OpenHeadMouse not started",
                "Head tracking was cancelled or stopped, so the test has not been started.",
            )
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._on_start_clicked()

    def _on_start_clicked(self) -> None:
        if self._waiting_for_headmouse:
            return
        if not self._host_run_active:
            self._host_run_active = True
            self._saved_head_path = None
            self._set_mouse_task_active(False)
        if (
            self._start_mode_combo.currentText() == START_HEADMOUSE
            and not self._head_tracking_started
        ):
            self._on_start_with_headmouse_clicked()
            return
        includes_head = self._task_combo.currentText() != TASK_MOUSE_ONLY
        mouse_task = self._task_combo.currentText() != TASK_HEAD_ONLY
        if (
            (self._record_head_checkbox.isChecked() or includes_head)
            and self._head_start_provider is not None
            and not self._head_tracking_started
        ):
            # Record head movement without taking the cursor away from the
            # access method being tested; the test starts from the callback.
            self._head_tracking_started = True
            if not self._request_head_tracking(mouse_control=False):
                self._head_tracking_started = False
                self._stop_run_input()
            return

        mode = self._mode_combo.currentText()
        if self._apply_results_dir(prompt=True) is None:
            # Running the test anyway would collect results that can't be saved.
            self._stop_run_input()
            QMessageBox.warning(
                self,
                "No recordings folder",
                "No usable recordings folder was chosen, so the test has not been started.\n\n"
                "Choose a folder when asked (or in OpenHeadMouse: File > Set Recordings Folder...).",
            )
            return
        self._screen_w, self._screen_h = self._current_screen_geometry()
        stimulus = None
        if mouse_task and mode != MODE_TYPING:
            stimulus = self._resolve_stimulus()
            if stimulus is None:
                self._stop_run_input()
                return  # user cancelled the image picker

        self._logger = FittsResultsLogger()
        access_method = self._access_method_combo.currentText()
        record_head = self._record_head_checkbox.isChecked() or includes_head
        # The folder was made ready above; this just points the new logger at it.
        self._apply_results_dir(prompt=False)
        # Head samples are phase-labelled whenever head movement is recorded,
        # whichever access method is actually driving the cursor.
        self._use_head_phase_provider = record_head
        self._logger.metadata["access_method"] = access_method
        self._logger.metadata["record_head_movement"] = record_head
        self._logger.metadata["task_selection"] = self._task_combo.currentText()
        self._logger.metadata["start_mode"] = self._start_mode_combo.currentText()
        if access_method == "Other":
            self._logger.metadata["access_method_other"] = self._access_method_other_edit.text() or None
        self._logger.metadata["notes"] = self._session_notes_edit.toPlainText() or None
        self._logger.metadata["screen_width_px"] = self._screen_w
        self._logger.metadata["screen_height_px"] = self._screen_h
        self._exclude_repetitions_spin.blockSignals(True)
        self._exclude_repetitions_spin.setValue(0)
        self._exclude_repetitions_spin.blockSignals(False)

        try:
            if not mouse_task:
                trial_specs = []
            elif mode == MODE_TYPING:
                trial_specs = self._build_typing_specs()
            else:
                trial_specs = self._build_iso_specs(include_edge=(mode == MODE_ISO_EDGE))
        except ValueError as exc:
            self._stop_run_input()
            QMessageBox.warning(self, "Invalid setup", str(exc))
            return

        include_initial_rest = includes_head
        include_range_of_movement = includes_head
        stages = build_pretest_stages(
            include_initial_rest,
            include_range_of_movement,
            self._pretest_move_duration_spin.value(),
            self._pretest_hold_duration_spin.value(),
        )
        self._logger.metadata["pretest_configuration"] = {
            "initial_rest_enabled": include_initial_rest,
            "range_of_movement_enabled": include_range_of_movement,
            "move_duration_s": self._pretest_move_duration_spin.value(),
            "hold_duration_s": self._pretest_hold_duration_spin.value(),
        }
        self._logger.metadata["pretest_phases"] = []
        self._logger.metadata["fitts_task"] = {
            "phase": "fitts_test",
            "start_host_time": None,
            "end_host_time": None,
        }
        self._saved_path = None
        self._pending_test = (mode, trial_specs, stimulus)

        if stages:
            self._start_pretest(stages)
        else:
            self._start_trial_canvas()

    def _start_pretest(self, stages: list[dict]) -> None:
        if self._pretest_overlay is not None:
            self._stack.removeWidget(self._pretest_overlay)
            self._pretest_overlay.deleteLater()
        self._pretest_overlay = PreTestTaskOverlay(stages)
        self._pretest_overlay.phase_changed.connect(
            self._on_pretest_phase_changed
        )
        self._pretest_overlay.completed.connect(self._on_pretest_completed)
        self._pretest_overlay.cancelled.connect(self._on_pretest_cancelled)
        self._stack.addWidget(self._pretest_overlay)
        self._stack.setCurrentWidget(self._pretest_overlay)
        self.showFullScreen()
        self._pretest_overlay.start()

    def _on_pretest_phase_changed(self, phase: str) -> None:
        if (
            self._head_phase_provider is None
            or not self._use_head_phase_provider
        ):
            return
        try:
            self._head_phase_provider(phase or None, True)
        except Exception as exc:
            self._on_pretest_cancelled()
            QMessageBox.critical(
                self,
                "Head recording error",
                f"Could not label the head recording phase:\n\n{exc}",
            )

    def _on_pretest_completed(self) -> None:
        if self._pretest_overlay is not None:
            self._logger.metadata["pretest_phases"] = list(
                self._pretest_overlay.records
            )
        self._remove_pretest_overlay()
        if self._pending_test is not None and not self._pending_test[1]:
            self._pending_test = None
            self._on_all_trials_completed()
        else:
            self._start_trial_canvas()

    def _on_pretest_cancelled(self, *, restore_setup: bool = True) -> None:
        self._stop_test_dwell()
        if self._pretest_overlay is not None:
            self._pretest_overlay.stop(cancelled=True)
            self._logger.metadata["pretest_phases"] = list(
                self._pretest_overlay.records
            )
        if (
            self._head_phase_provider is not None
            and self._use_head_phase_provider
        ):
            try:
                self._head_phase_provider(None, False)
            except Exception as exc:
                QMessageBox.warning(self, "Head recording error", str(exc))
        self._remove_pretest_overlay()
        self._pending_test = None
        self._stop_run_input()
        if restore_setup:
            self.showNormal()
            self._stack.setCurrentWidget(self._setup_page)

    def _remove_pretest_overlay(self) -> None:
        overlay = self._pretest_overlay
        self._pretest_overlay = None
        if overlay is not None:
            overlay.stop()
            self._stack.removeWidget(overlay)
            overlay.deleteLater()

    def _start_trial_canvas(self) -> None:
        if self._pending_test is None:
            return
        mode, trial_specs, stimulus = self._pending_test
        self._pending_test = None

        if self._canvas is not None:
            self._stack.removeWidget(self._canvas)
            self._canvas.deleteLater()

        if mode == MODE_TYPING:
            self._canvas = TypingTrialCanvas(trial_specs, self._typing_layout)
        else:
            self._canvas = FittsTrialCanvas(trial_specs, stimulus)
        self._canvas.trial_completed.connect(self._on_trial_completed)
        self._canvas.all_trials_completed.connect(self._on_all_trials_completed)
        self._stack.addWidget(self._canvas)
        self._stack.setCurrentWidget(self._canvas)
        self._logger.metadata["fitts_task"]["start_host_time"] = host_time()
        if (
            self._head_phase_provider is not None
            and self._use_head_phase_provider
        ):
            self._head_phase_provider("fitts_test", True)
        self.showFullScreen()
        self._set_mouse_task_active(True)
        self._start_test_dwell()

    def _build_iso_specs(self, include_edge: bool) -> list[dict]:
        num_targets = int(self._num_targets_combo.currentText())
        amplitudes = _parse_number_list(self._amplitudes_edit.text())
        widths = _parse_number_list(self._widths_edit.text())
        if not amplitudes or not widths:
            raise ValueError("Provide at least one amplitude and one width")
        repetitions = self._repetitions_spin.value()
        randomize = self._randomize_targets_checkbox.isChecked()
        center = (self._screen_w / 2, self._screen_h / 2)

        specs: list[dict] = []
        trial_index = 0
        conditions = build_condition_set(amplitudes, widths)
        for condition in conditions:
            safe_amplitude = min(
                condition.amplitude_px,
                max_safe_circle_radius(self._screen_w, self._screen_h, condition.width_px),
            )
            positions = generate_circle_positions(center, num_targets, safe_amplitude)
            sequence = multi_directional_sequence(num_targets)
            last_pos = center
            for rep in range(repetitions):
                rep_sequence = list(sequence)
                if randomize:
                    random.shuffle(rep_sequence)
                for idx in rep_sequence:
                    target_pos = positions[idx]
                    specs.append(
                        {
                            "condition_label": condition.label,
                            "block_type": "circle",
                            "amplitude_px": condition.amplitude_px,
                            "width_px": condition.width_px,
                            "start_pos": last_pos,
                            "target_pos": target_pos,
                            "trial_index": trial_index,
                            "repetition_index": rep,
                        }
                    )
                    trial_index += 1
                    last_pos = target_pos

        if include_edge:
            edge_points = generate_edge_extremity_targets(self._screen_w, self._screen_h, num_targets=num_targets)
            for width in widths:
                last_pos = center
                for rep in range(repetitions):
                    rep_edge_points = list(edge_points)
                    if randomize:
                        random.shuffle(rep_edge_points)
                    for point in rep_edge_points:
                        amplitude = math.dist(last_pos, point)
                        specs.append(
                            {
                                "condition_label": f"edge W={width:.0f}",
                                "block_type": "edge",
                                "amplitude_px": amplitude,
                                "width_px": width,
                                "start_pos": last_pos,
                                "target_pos": point,
                                "trial_index": trial_index,
                                "repetition_index": rep,
                            }
                        )
                        trial_index += 1
                        last_pos = point
                        # step back to center between reaches, alternating out and back
                        specs.append(
                            {
                                "condition_label": f"edge W={width:.0f}",
                                "block_type": "edge",
                                "amplitude_px": amplitude,
                                "width_px": width,
                                "start_pos": last_pos,
                                "target_pos": center,
                                "trial_index": trial_index,
                                "repetition_index": rep,
                            }
                        )
                        trial_index += 1
                        last_pos = center
        return specs

    def _build_typing_specs(self) -> list[dict]:
        key_size_mode = "random" if self._typing_key_size_combo.currentText() == KEY_SIZE_RANDOM else "consistent"
        widths = _parse_number_list(self._widths_edit.text())
        if not widths:
            raise ValueError("Provide at least one width")
        reassign_each_word = self._typing_reassign_widths_checkbox.isChecked()
        rng = random.Random()

        def _make_layout() -> dict:
            return build_keyboard_layout(
                self._screen_w,
                self._screen_h,
                key_size_mode,
                possible_widths=widths,
                rng=rng,
            )

        layout = _make_layout()
        self._typing_layout = layout

        num_words = self._typing_word_count_spin.value()
        words = [rng.choice(TYPING_WORDS) for _ in range(num_words)]

        specs: list[dict] = []
        trial_index = 0
        last_pos = (self._screen_w / 2, self._screen_h / 2)
        for word_index, word in enumerate(words):
            if word_index > 0 and reassign_each_word and key_size_mode == "random":
                layout = _make_layout()
            for letter in word:
                key = layout[letter]
                dist = math.dist(last_pos, key.center)
                specs.append(
                    {
                        "condition_label": f"Word {word_index + 1}: {word}",
                        "block_type": "typing",
                        "amplitude_px": dist,
                        "width_px": key.width,
                        "start_pos": last_pos,
                        "target_pos": key.center,
                        "target_letter": letter,
                        "word": word,
                        "layout": layout,
                        "trial_index": trial_index,
                        "repetition_index": word_index,
                    }
                )
                trial_index += 1
                last_pos = key.center
        return specs

    # -- Trial callbacks -------------------------------------------------

    def _on_trial_completed(self, result: TrialResult) -> None:
        self._logger.add_trial(result)

    def _on_all_trials_completed(self) -> None:
        self._set_mouse_task_active(False)
        self._stop_test_dwell()
        self._end_fitts_task(True)
        self._populate_results_page()
        self._stack.setCurrentWidget(self._results_page)
        app = QApplication.instance()
        quit_on_close = app.quitOnLastWindowClosed()
        app.setQuitOnLastWindowClosed(False)
        self._finishing_run = True
        try:
            # Standalone runs must stay alive while save-recovery dialogs are open.
            self.close()
            self._saved_path = self._save_results_with_retry()
            if self._saved_path is None:
                self.showNormal()
            else:
                print(f"[Fitts] Raw trial data auto-saved to {self._saved_path}")
        finally:
            self._finishing_run = False
            app.setQuitOnLastWindowClosed(quit_on_close)
        if quit_on_close and not any(w.isVisible() for w in app.topLevelWidgets()):
            app.quit()

    # -- Results page ------------------------------------------------------

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self._score_label = QLabel("")
        layout.addWidget(self._score_label)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(QLabel("Exclude first N repetitions per condition (e.g. practice trial):"))
        self._exclude_repetitions_spin = QSpinBox()
        self._exclude_repetitions_spin.setRange(0, 10)
        self._exclude_repetitions_spin.valueChanged.connect(lambda _: self._populate_results_page())
        exclude_row.addWidget(self._exclude_repetitions_spin)
        exclude_row.addStretch()
        layout.addLayout(exclude_row)

        self._results_table = QTableWidget()
        self._results_table.setColumnCount(7)
        self._results_table.setHorizontalHeaderLabels(
            ["Condition", "Trials", "IDe (bits)", "Mean MT (s)", "Error rate", "Throughput (bits/s)", "We (px)"]
        )
        self._results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self._results_table)

        button_row = QHBoxLayout()
        # Save/Open live in the File menu; this page keeps only the actions
        # that follow on from the run that just finished.
        self._sync_button = QPushButton("Open Synchronised Playback...")
        self._sync_button.setToolTip(
            "Replay these trials alongside the head-movement recording made at the same time"
        )
        self._sync_button.clicked.connect(self._on_open_synchronised)
        again_button = QPushButton("Run Again")
        again_button.clicked.connect(self._on_start_clicked)
        back_button = QPushButton("Back to Setup")
        back_button.clicked.connect(self._on_back_to_setup_clicked)
        button_row.addWidget(self._sync_button)
        button_row.addWidget(again_button)
        button_row.addWidget(back_button)
        layout.addLayout(button_row)
        return page

    def _on_back_to_setup_clicked(self) -> None:
        if self._host_run_active:
            self._set_mouse_task_active(False)
        self._stop_test_dwell()
        self._end_fitts_task(False)
        self._stop_run_input()
        self._remove_pretest_overlay()
        self._pending_test = None
        # The next run re-applies the recording/mouse-control settings for
        # whichever start mode is used.
        self._head_tracking_started = False
        self.showNormal()
        self._stack.setCurrentWidget(self._setup_page)

    def _end_fitts_task(self, completed: bool) -> None:
        task = self._logger.metadata.get("fitts_task")
        if not task or task.get("start_host_time") is None:
            return
        if task.get("end_host_time") is None:
            task["end_host_time"] = host_time()
            task["completed"] = completed
            if (
                self._head_phase_provider is not None
                and self._use_head_phase_provider
            ):
                try:
                    self._head_phase_provider(None, completed)
                except Exception as exc:
                    QMessageBox.warning(
                        self,
                        "Head recording error",
                        f"Could not finish the head recording phase:\n\n{exc}",
                    )

    def _populate_results_page(self) -> None:
        exclude_repetitions = self._exclude_repetitions_spin.value()
        stats = FittsStats(self._logger.trials)
        summary = stats.summary(exclude_repetitions=exclude_repetitions)

        self._score_label.setText(
            f"Overall throughput: {summary['overall_throughput_bits_per_s']:.2f} bits/s   "
            f"Overall error rate: {summary['overall_error_rate']:.1%}   "
            f"({summary['trial_count']} of {len(self._logger.trials)} trials, after exclusions)"
        )

        conditions = summary["conditions"]
        self._results_table.setRowCount(len(conditions) + 1)
        overall_values = [
            "Overall",
            str(summary["trial_count"]),
            f"{summary['overall_index_of_difficulty_bits']:.2f}",
            f"{summary['overall_mean_movement_time_s']:.3f}",
            f"{summary['overall_error_rate']:.1%}",
            f"{summary['overall_throughput_bits_per_s']:.2f}",
            f"{summary['overall_effective_width_px']:.1f}",
        ]
        for col, value in enumerate(overall_values):
            self._results_table.setItem(0, col, QTableWidgetItem(value))
        for row, data in enumerate(conditions.values(), start=1):
            values = [
                data["condition_label"],
                str(data["trial_count"]),
                f"{data['index_of_difficulty_bits']:.2f}",
                f"{data['mean_movement_time_s']:.3f}",
                f"{data['error_rate']:.1%}",
                f"{data['throughput_bits_per_s']:.2f}",
                f"{data['effective_width_px']:.1f}",
            ]
            for col, value in enumerate(values):
                self._results_table.setItem(row, col, QTableWidgetItem(value))

    def _on_save_results(self) -> None:
        default_name = str(self._logger.output_dir / f"fitts_session_{int(time.time())}.json")
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Fitts' Law results", default_name, "JSON (*.json)")
        if not filepath:
            return
        self._saved_path = self._save_results_with_retry(filepath)
        if self._saved_path is not None:
            QMessageBox.information(self, "Saved", f"Results saved to {self._saved_path}")

    def _save_results_with_retry(self, filepath: str | Path | None = None) -> Path | None:
        if self._saved_head_path is not None:
            self._logger.metadata["head_session_file"] = str(self._saved_head_path)
        while True:
            try:
                return self._logger.save(filepath)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Could not save results",
                    f"Results could not be saved:\n\n{exc}\n\nSelect another folder to retry.",
                )
                folder = QFileDialog.getExistingDirectory(
                    self, "Choose a folder for Fitts results", str(Path.home())
                )
                if not folder:
                    QMessageBox.warning(
                        self, "Results not saved",
                        "The results are still in this window. Use File > Save Results As "
                        "to save them before closing it.",
                    )
                    return None
                filename = (
                    Path(filepath).name if filepath is not None
                    else f"fitts_session_{int(time.time())}.json"
                )
                self._logger.output_dir = Path(folder)
                filepath = Path(folder) / filename

    def _on_open_results(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Fitts' Law results", str(self._logger.output_dir), "JSON (*.json)"
        )
        if not filepath:
            return
        self._logger = FittsResultsLogger()
        self._logger.load(filepath)
        self._apply_results_dir(prompt=False)
        self._saved_path = Path(filepath)
        self._populate_results_page()
        self.showNormal()
        self._stack.setCurrentWidget(self._results_page)

    # -- Synchronised playback ---------------------------------------------

    def _fitts_session_path(self) -> Path | None:
        """Path of the current trials on disk, saving them first if needed."""
        if not self._logger.trials and not self._logger.metadata.get("pretest_phases"):
            QMessageBox.warning(self, "No session", "Run or open a Fitts session first.")
            return None
        if self._saved_path is None or not Path(self._saved_path).exists():
            self._saved_path = self._save_results_with_retry()
        if self._saved_path is None:
            return None
        return Path(self._saved_path)

    def _head_session_path(self) -> Path | None:
        """Stop the host app's recording and use it, or ask for a session file."""
        if self._saved_head_path is not None:
            return self._saved_head_path
        if self._head_session_provider is not None:
            try:
                path = self._head_session_provider()
            except Exception as exc:  # a failure here must not lose the results
                QMessageBox.warning(self, "Could not stop recording", str(exc))
                path = None
            if path is not None:
                return Path(path)
        start_dir = project_root / "sessions"
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open the head-movement session recorded during this test",
            str(start_dir if start_dir.exists() else Path.home()),
            "JSON Sessions (*.json);;CSV Sessions (*.csv);;All Files (*)",
        )
        return Path(filepath) if filepath else None

    def _on_open_synchronised(self) -> None:
        """Replay these trials alongside the head recording made at the same time."""
        fitts_path = self._fitts_session_path()
        if fitts_path is None:
            return
        head_path = self._head_session_path()
        if head_path is None:
            return
        from session_viewer.session_viewer import open_synchronised_playback

        try:
            windows = open_synchronised_playback(head_path, fitts_path, screen=self.screen())
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to open synchronised playback:\n\n{exc}")
            return
        self._playback_windows = [w for w in windows if w is not None]

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self._canvas is not None and self._stack.currentWidget() is self._canvas:
            self._on_back_to_setup_clicked()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        was_active = self._host_run_active
        self.hide()
        if self._host_run_active:
            self._set_mouse_task_active(False)
        self._stop_test_dwell()
        self._end_fitts_task(False)
        if self._pretest_overlay is not None:
            self._on_pretest_cancelled(restore_setup=False)
        self._pending_test = None
        self._head_tracking_started = False
        self._reset_headmouse_button()
        super().closeEvent(event)
        self._stop_run_input()
        if was_active and not self._finishing_run and (
            self._logger.trials or self._logger.metadata.get("pretest_phases")
        ):
            self._saved_path = self._save_results_with_retry()
            if self._saved_path is None:
                event.ignore()
                self._populate_results_page()
                self._stack.setCurrentWidget(self._results_page)
                self.showNormal()


def launch_fitts_test() -> FittsTestWindow:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = FittsTestWindow()
    window.show()
    return window


if __name__ == "__main__":
    app_window = launch_fitts_test()
    from PySide6.QtWidgets import QApplication

    sys.exit(QApplication.instance().exec())
