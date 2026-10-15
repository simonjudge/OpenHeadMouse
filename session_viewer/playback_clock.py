"""Shared playback clock, transport controls and recording-sync checks.

A `PlaybackClock` owns the playback timer, the current time, speed and the
[t_min, t_max] range. Viewer windows never advance time themselves - they
listen to `time_changed` and show whatever frame is at that time. Standalone
windows each own a clock; synchronised windows share one clock in shared
host-time units (core.clock.host_time), so Play/slider/speed in either window
drives both.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider


class PlaybackClock(QObject):
    """Playback time source shared by one or more viewer windows."""

    time_changed = Signal(float)
    playing_changed = Signal(bool)
    range_changed = Signal(float, float)
    speed_changed = Signal(float)

    TICK_MS = 40  # ~25 fps

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t_min = 0.0
        self.t_max = 0.0
        self.time = 0.0
        self.speed = 1.0
        self.is_playing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

    def set_range(self, t_min: float, t_max: float) -> None:
        """Set the playable range and rewind to its start."""
        self.pause()
        self.t_min = float(t_min)
        self.t_max = max(float(t_max), self.t_min)
        self.range_changed.emit(self.t_min, self.t_max)
        self.seek(self.t_min)

    def seek(self, t: float) -> None:
        self.time = min(max(float(t), self.t_min), self.t_max)
        self.time_changed.emit(self.time)

    def set_speed(self, speed: float) -> None:
        if speed != self.speed:
            self.speed = float(speed)
            self.speed_changed.emit(self.speed)

    def play(self) -> None:
        if self.is_playing or self.t_max <= self.t_min:
            return
        if self.time >= self.t_max:
            self.seek(self.t_min)
        self.is_playing = True
        self._timer.start(self.TICK_MS)
        self.playing_changed.emit(True)

    def pause(self) -> None:
        if not self.is_playing:
            return
        self.is_playing = False
        self._timer.stop()
        self.playing_changed.emit(False)

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def _on_tick(self) -> None:
        t = self.time + self.TICK_MS / 1000.0 * self.speed
        if t >= self.t_max:
            self.seek(self.t_max)
            self.pause()
        else:
            self.seek(t)


class PlaybackTransport(QObject):
    """Play button + slider + time label (and optional speed spinbox) bound to a clock.

    The slider works in milliseconds from `clock.t_min`. Calling `set_clock`
    rebinds all controls to another clock (e.g. a shared synchronised one).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clock: PlaybackClock | None = None
        self.speed_spinbox: QDoubleSpinBox | None = None

        self.play_button = QPushButton("▶ Play")
        self.play_button.setMaximumWidth(80)
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._on_play_clicked)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        self.slider.sliderMoved.connect(self._on_slider_moved)

        self.time_label = QLabel("0.000s")
        self.time_label.setMaximumWidth(80)

    def make_layout(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self.play_button)
        row.addWidget(self.slider)
        row.addWidget(self.time_label)
        return row

    def bind_speed_spinbox(self, spinbox: QDoubleSpinBox) -> None:
        self.speed_spinbox = spinbox
        spinbox.valueChanged.connect(self._on_speed_edited)

    def set_clock(self, clock: PlaybackClock) -> None:
        if self.clock is clock:
            return
        if self.clock is not None:
            self.clock.time_changed.disconnect(self._on_time_changed)
            self.clock.playing_changed.disconnect(self._on_playing_changed)
            self.clock.range_changed.disconnect(self._on_range_changed)
            self.clock.speed_changed.disconnect(self._on_speed_changed)
        self.clock = clock
        clock.time_changed.connect(self._on_time_changed)
        clock.playing_changed.connect(self._on_playing_changed)
        clock.range_changed.connect(self._on_range_changed)
        clock.speed_changed.connect(self._on_speed_changed)
        self._on_range_changed(clock.t_min, clock.t_max)
        self._on_playing_changed(clock.is_playing)
        self._on_speed_changed(clock.speed)
        self._on_time_changed(clock.time)

    def set_enabled(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled)
        self.slider.setEnabled(enabled)

    def _on_play_clicked(self) -> None:
        if self.clock is not None:
            self.clock.toggle()

    def _on_slider_moved(self, value: int) -> None:
        if self.clock is not None:
            self.clock.pause()
            self.clock.seek(self.clock.t_min + value / 1000.0)

    def _on_speed_edited(self, value: float) -> None:
        if self.clock is not None:
            self.clock.set_speed(value)

    def _on_time_changed(self, t: float) -> None:
        rel = t - self.clock.t_min
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(rel * 1000)))
        self.slider.blockSignals(False)
        self.time_label.setText(f"{rel:.3f}s")

    def _on_playing_changed(self, playing: bool) -> None:
        self.play_button.setText("⏸ Pause" if playing else "▶ Play")

    def _on_range_changed(self, t_min: float, t_max: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setRange(0, int(round((t_max - t_min) * 1000)))
        self.slider.blockSignals(False)

    def _on_speed_changed(self, speed: float) -> None:
        if self.speed_spinbox is not None and self.speed_spinbox.value() != speed:
            self.speed_spinbox.blockSignals(True)
            self.speed_spinbox.setValue(speed)
            self.speed_spinbox.blockSignals(False)


@dataclass
class SyncCheck:
    """Result of `check_sync`: either a shared host-time range or a refusal reason."""

    ok: bool
    reason: str = ""
    head_range: tuple[float, float] | None = None
    fitts_range: tuple[float, float] | None = None

    @property
    def t_min(self) -> float:
        return min(self.head_range[0], self.fitts_range[0])

    @property
    def t_max(self) -> float:
        return max(self.head_range[1], self.fitts_range[1])


def check_sync(head_samples: list, fitts_trials: list, metadata: dict | None = None) -> SyncCheck:
    """Check that a head-movement recording and a Fitts session share the host clock and overlap.

    Both files must carry `host_time` stamps (head samples) and
    `start_host_time`/`end_host_time` (Fitts trials) - files recorded before
    these were added can only be played back on their own.
    """
    if not head_samples:
        return SyncCheck(False, "The head-movement session has no samples.")
    from session_viewer.pretest_stats import pretest_time_range

    head_task_range = pretest_time_range(metadata)
    if not fitts_trials and head_task_range is None:
        return SyncCheck(False, "The Fitts session has no trials or timed head-movement tasks.")
    if any(getattr(s, "host_time", None) is None for s in head_samples):
        return SyncCheck(
            False,
            "The head-movement session has no shared host timestamps (host_time). "
            "It was recorded before synchronised playback was supported.",
        )
    if any(t.start_host_time is None or t.end_host_time is None for t in fitts_trials):
        return SyncCheck(
            False,
            "The Fitts session has no shared host timestamps (start_host_time/end_host_time). "
            "It was recorded before synchronised playback was supported.",
        )
    head_range = (min(s.host_time for s in head_samples), max(s.host_time for s in head_samples))
    # The Fitts range spans the head-movement exercise (if any) and the trials.
    spans = [head_task_range] if head_task_range is not None else []
    if fitts_trials:
        spans.append((
            min(t.start_host_time for t in fitts_trials), max(t.end_host_time for t in fitts_trials)
        ))
    fitts_range = (min(s for s, _ in spans), max(e for _, e in spans))
    if head_range[1] < fitts_range[0] or fitts_range[1] < head_range[0]:
        gap = max(fitts_range[0] - head_range[1], head_range[0] - fitts_range[1])
        return SyncCheck(
            False,
            f"The recordings do not overlap in time (they are {gap:.1f}s apart). "
            "Choose a head-movement session recorded while the Fitts test was running.",
            head_range,
            fitts_range,
        )
    return SyncCheck(True, "", head_range, fitts_range)
