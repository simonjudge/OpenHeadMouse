"""Headless smoke tests for fitts_law/fitts_test.py (setup -> trial -> results)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from fitts_law.fitts_test import (
    MODE_ISO_STANDARD,
    MODE_TYPING,
    START_HEADMOUSE,
    FittsTestWindow,
)
from fitts_law.results import FittsResultsLogger, TrialResult


def _click_at(canvas, x: float, y: float) -> None:
    point = QPointF(x, y)
    event = QMouseEvent(
        QEvent.MouseButtonPress,
        point,
        point,
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    canvas.mousePressEvent(event)


def _move_event(x: float, y: float) -> QMouseEvent:
    point = QPointF(x, y)
    return QMouseEvent(
        QEvent.MouseMove,
        point,
        point,
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )


def test_window_builds_and_starts_iso_test(qt_app):
    window = FittsTestWindow()

    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")

    window._on_start_clicked()

    assert window._canvas is not None
    assert window._stack.currentWidget() is window._canvas
    assert len(window._canvas._specs) == 9  # 1 condition * 9 targets * 1 repetition


def test_click_on_target_registers_a_hit(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")
    window._on_start_clicked()

    canvas = window._canvas
    first_target = canvas._specs[0]["target_pos"]
    _click_at(canvas, *first_target)

    assert len(window._logger.trials) == 1
    trial = window._logger.trials[0]
    assert trial.hit is True
    assert trial.movement_time_s >= 0.0


def test_click_off_target_registers_a_miss(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")
    window._on_start_clicked()

    canvas = window._canvas
    tx, ty = canvas._specs[0]["target_pos"]
    _click_at(canvas, tx + 1000, ty + 1000)

    assert window._logger.trials[0].hit is False


def test_completing_all_trials_closes_window_and_prepares_results(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")
    window._on_start_clicked()

    canvas = window._canvas
    for spec in list(canvas._specs):
        _click_at(canvas, *spec["target_pos"])

    assert window._stack.currentWidget() is window._results_page
    assert not window.isVisible()
    assert window._results_table.rowCount() == 2  # 1 "Overall" row + 1 condition row


def test_trajectory_is_recorded_during_a_trial(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")
    window._on_start_clicked()

    canvas = window._canvas
    target = canvas._specs[0]["target_pos"]
    canvas.mouseMoveEvent(_move_event(target[0] - 50, target[1]))
    canvas._last_trajectory_sample_time = None  # bypass the ~60Hz throttle for the test
    canvas.mouseMoveEvent(_move_event(target[0] - 10, target[1]))
    _click_at(canvas, *target)

    trial = window._logger.trials[0]
    assert len(trial.trajectory) == 2
    assert trial.trajectory[0][1:] == [target[0] - 50, target[1]]
    assert trial.trajectory[-1][0] <= trial.movement_time_s


def test_completed_run_autosaves_raw_trials_to_disk(qt_app, tmp_path):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")
    window._on_start_clicked()
    window._logger.output_dir = tmp_path

    canvas = window._canvas
    for spec in list(canvas._specs):
        _click_at(canvas, *spec["target_pos"])

    saved_files = list(tmp_path.glob("*.json"))
    assert len(saved_files) == 1
    reloaded = FittsResultsLogger(output_dir=tmp_path).load(saved_files[0])
    assert len(reloaded) == 9


def test_open_results_loads_a_saved_session_into_the_results_page(qt_app, tmp_path, monkeypatch):
    logger = FittsResultsLogger(output_dir=tmp_path)
    logger.add_trial(
        TrialResult(
            condition_label="A=100 W=50",
            block_type="circle",
            amplitude_px=100.0,
            width_px=50.0,
            start_pos=(0.0, 0.0),
            target_pos=(100.0, 0.0),
            click_pos=(100.0, 0.0),
            start_time=0.0,
            end_time=0.5,
            movement_time_s=0.5,
            hit=True,
        )
    )
    saved_path = logger.save("previous_session.json")

    window = FittsTestWindow()
    monkeypatch.setattr(
        "fitts_law.fitts_test.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(saved_path), ""),
    )
    window._on_open_results()

    assert window._stack.currentWidget() is window._results_page
    assert window._results_table.rowCount() == 2  # 1 "Overall" row + 1 condition row
    assert len(window._logger.trials) == 1


def test_setup_page_hides_iso_only_fields_for_typing_mode(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_TYPING)
    assert window._stimulus_combo.isHidden()
    assert window._amplitudes_edit.isHidden()
    assert not window._typing_key_size_combo.isHidden()
    assert not window._typing_word_count_spin.isHidden()
    assert not window._widths_edit.isHidden()  # reused for typing mode's key-size widths list


def test_typing_mode_click_on_correct_key_registers_a_hit(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_TYPING)
    window._typing_word_count_spin.setValue(1)
    window._on_start_clicked()

    canvas = window._canvas
    first_spec = canvas._specs[0]
    target_x, target_y = first_spec["target_pos"]
    _click_at(canvas, target_x, target_y)

    assert len(window._logger.trials) == 1
    trial = window._logger.trials[0]
    assert trial.hit is True
    assert trial.block_type == "typing"
    assert trial.condition_label.startswith("Word 1:")


def test_typing_mode_click_on_wrong_key_registers_a_miss(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_TYPING)
    window._typing_word_count_spin.setValue(1)
    window._on_start_clicked()

    canvas = window._canvas
    first_spec = canvas._specs[0]
    wrong_letter = next(letter for letter in window._typing_layout if letter != first_spec["target_letter"] and letter != " ")
    wrong_x, wrong_y = window._typing_layout[wrong_letter].center
    _click_at(canvas, wrong_x, wrong_y)

    assert window._logger.trials[0].hit is False


def test_typing_mode_completes_and_shows_results(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_TYPING)
    window._typing_word_count_spin.setValue(1)
    window._on_start_clicked()

    canvas = window._canvas
    for spec in list(canvas._specs):
        _click_at(canvas, *spec["target_pos"])

    assert window._stack.currentWidget() is window._results_page
    assert window._results_table.rowCount() >= 1


def test_typing_mode_groups_results_into_one_row_per_word(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_TYPING)
    window._typing_word_count_spin.setValue(3)
    window._on_start_clicked()

    canvas = window._canvas
    for spec in list(canvas._specs):
        _click_at(canvas, *spec["target_pos"])

    # 1 "Overall" row + 1 row per word (words may repeat text but are still
    # distinct occurrences, since condition_label embeds the word index).
    num_words = len({s["repetition_index"] for s in canvas._specs})
    assert window._results_table.rowCount() == num_words + 1

def test_start_with_headmouse_option_is_disabled_without_a_host_app(qt_app):
    window = FittsTestWindow()

    assert window._start_mode_combo.model().item(2).isEnabled() is False


def test_start_with_headmouse_starts_the_test_once_tracking_is_ready(qt_app):
    window = FittsTestWindow()
    window._mode_combo.setCurrentText(MODE_ISO_STANDARD)
    window._amplitudes_edit.setText("100")
    window._widths_edit.setText("50")
    window._repetitions_spin.setValue(1)
    window._num_targets_combo.setCurrentText("9")

    pending = {}

    def start_provider(on_ready, mouse_control=True):
        pending["on_ready"] = on_ready  # calibration still running
        pending["mouse_control"] = mouse_control
        return True

    window.set_head_start_provider(start_provider)
    assert window._start_mode_combo.model().item(2).isEnabled() is True
    window._start_mode_combo.setCurrentText(START_HEADMOUSE)

    window._start_button.click()

    # Nothing starts until the head tracker reports it is ready.
    assert window._canvas is None
    assert window._start_mode_combo.isEnabled() is False
    assert window._access_method_combo.currentText() == "HeadMouse"
    # Driving the cursor with the head records it too.
    assert pending["mouse_control"] is True
    assert window._record_head_checkbox.isChecked() is True

    pending["on_ready"](True)

    assert window._canvas is not None
    assert window._stack.currentWidget() is window._canvas
    assert window._start_mode_combo.isEnabled() is True
    assert window._logger.metadata["record_head_movement"] is True


def test_start_with_headmouse_does_not_start_the_test_if_tracking_fails(qt_app, monkeypatch):
    window = FittsTestWindow()
    warnings = []
    monkeypatch.setattr(
        "fitts_law.fitts_test.QMessageBox.warning",
        lambda *args, **kwargs: warnings.append(args),
    )

    def start_provider(on_ready, mouse_control=True):
        on_ready(False)  # user cancelled calibration
        return True

    window.set_head_start_provider(start_provider)
    window._on_start_with_headmouse_clicked()

    assert window._canvas is None
    assert len(warnings) == 1
    assert window._start_mode_combo.isEnabled() is True


def test_start_with_headmouse_re_enables_buttons_when_the_provider_refuses(qt_app):
    window = FittsTestWindow()
    window.set_head_start_provider(lambda on_ready, mouse_control=True: False)

    window._on_start_with_headmouse_clicked()

    assert window._canvas is None
    assert window._start_mode_combo.isEnabled() is True
    assert window._start_button.isEnabled() is True
