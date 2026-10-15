"""Fitts' Law trial playback window (extension of the session viewer).

Replays the mouse trajectories saved in a Fitts session file
(`fitts_law.results.FittsResultsLogger` JSON) on a scaled drawing of the test
screen, with cursor speed / distance-to-target graphs, and - in the same
layout as the head-movement `SessionVisualizerWindow` - summary and live
statistics on the right-hand side:

* ISO 9241-411 effective-width throughput, We, Ae, IDe, MT and error rate.
* The path accuracy measures of MacKenzie, Kauppinen & Silfverberg (2001),
  "Accuracy measures for evaluating computer pointing devices", CHI 2001:
  TRE, TAC, MDC, ODC (counts per trial) and MV, ME, MO (px).

Playback is driven by a `PlaybackClock`; `attach_clock()` lets the session
viewer share one clock between this window and a head-movement session
recorded at the same time (see `SessionVisualizerWindow.open_synchronised`).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QToolBar, QVBoxLayout, QWidget,
)

from fitts_law.results import FittsResultsLogger, FittsStats, TrialResult
from gui.window_actions import add_close_action
from session_viewer.measure_help import MeasureLabel
from session_viewer.playback_clock import PlaybackClock, PlaybackTransport
from session_viewer.head_session_lookup import find_head_session_for_fitts
from session_viewer.pretest_stats import (
    format_pretest_summary_html, phase_description, pretest_records, pretest_time_range, summarise_pretest,
)

_DEFAULT_FITTS_DIR = project_root / "fitts_sessions"

_PANEL_STYLE = """
    background: #f5f5f5;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 10px;
    font-family: monospace;
    font-size: 10px;
"""


def _fmt(value, spec: str, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:{spec}}{unit}"


class FittsScreenCanvas(QWidget):
    """Scaled drawing of the test screen: targets, task axis, trail, cursor and clicks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 250)
        self.screen_size = (1920.0, 1080.0)
        self.trials: list[TrialResult] = []
        self.trial_index = -1
        self.path = np.zeros((0, 2))
        self.cursor: tuple[float, float] | None = None
        self.clicked = False
        self.phase_text = ""

    def set_session(self, trials: list[TrialResult], screen_size: tuple[float, float]) -> None:
        self.trials = trials
        self.screen_size = screen_size
        self.trial_index = -1
        self.path = np.zeros((0, 2))
        self.cursor = None
        self.clicked = False
        self.phase_text = ""
        self.update()

    def set_state(self, trial_index: int, path: np.ndarray, cursor, clicked: bool) -> None:
        self.trial_index = trial_index
        self.path = path
        self.cursor = cursor
        self.clicked = clicked
        self.update()

    def _transform(self) -> tuple[float, float, float]:
        sw, sh = self.screen_size
        margin = 10.0
        scale = min((self.width() - 2 * margin) / sw, (self.height() - 2 * margin) / sh)
        scale = max(scale, 1e-6)
        ox = (self.width() - sw * scale) / 2
        oy = (self.height() - sh * scale) / 2
        return scale, ox, oy

    def _pt(self, xy) -> QPointF:
        scale, ox, oy = self._transform()
        return QPointF(ox + xy[0] * scale, oy + xy[1] * scale)

    def _draw_click(self, painter: QPainter, trial: TrialResult, size: float, alpha: int = 255) -> None:
        color = QColor(40, 160, 40, alpha) if trial.hit else QColor(210, 40, 40, alpha)
        painter.setPen(QPen(color, 2))
        c = self._pt(trial.click_pos)
        painter.drawLine(c + QPointF(-size, -size), c + QPointF(size, size))
        painter.drawLine(c + QPointF(-size, size), c + QPointF(size, -size))

    def _polyline(self, points) -> QPainterPath:
        path = QPainterPath()
        for i, p in enumerate(points):
            if i == 0:
                path.moveTo(self._pt(p))
            else:
                path.lineTo(self._pt(p))
        return path

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(255, 255, 255))
        scale, ox, oy = self._transform()
        sw, sh = self.screen_size
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.setBrush(QBrush(QColor(250, 250, 250)))
        painter.drawRect(QRectF(ox, oy, sw * scale, sh * scale))

        if not self.trials or self.trial_index < 0:
            painter.setPen(QColor("#333333"))
            painter.drawText(
                QRectF(ox, oy, sw * scale, sh * scale), Qt.AlignCenter, self.phase_text
            )
            if len(self.path) > 1:
                painter.setPen(QPen(QColor("#ff7f0e"), 2))
                painter.drawPath(self._polyline(self.path))
            if self.cursor is not None:
                painter.setBrush(QColor("#d62728"))
                painter.drawEllipse(self._pt(self.cursor), 4, 4)
            painter.end()
            return

        current = self.trials[self.trial_index]

        # Earlier trials of the same condition, faded.
        painter.setBrush(Qt.NoBrush)
        for trial in self.trials[: self.trial_index]:
            if (trial.block_type, trial.condition_label) != (current.block_type, current.condition_label):
                continue
            painter.setPen(QPen(QColor(150, 150, 150, 70), 1))
            painter.drawPath(self._polyline(FittsStats.path_points(trial)))
            self._draw_click(painter, trial, 3, alpha=90)

        # Task axis.
        painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.DashLine))
        painter.drawLine(self._pt(current.start_pos), self._pt(current.target_pos))

        # Target (diameter W) and start marker.
        r = current.width_px / 2 * scale
        painter.setPen(QPen(QColor(31, 119, 180), 2))
        painter.setBrush(QBrush(QColor(31, 119, 180, 50)))
        painter.drawEllipse(self._pt(current.target_pos), r, r)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(90, 90, 90), 2))
        s = self._pt(current.start_pos)
        painter.drawLine(s + QPointF(-5, 0), s + QPointF(5, 0))
        painter.drawLine(s + QPointF(0, -5), s + QPointF(0, 5))

        # Trail up to the playhead.
        if len(self.path) > 1:
            painter.setPen(QPen(QColor(255, 127, 14), 2))
            painter.drawPath(self._polyline(self.path))

        if self.clicked:
            self._draw_click(painter, current, 6)

        if self.cursor is not None:
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.setBrush(QBrush(QColor(214, 39, 40)))
            painter.drawEllipse(self._pt(self.cursor), 4, 4)
        painter.end()


class FittsPlaybackWindow(QMainWindow):
    """Replays a Fitts session's trajectories with summary and live statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fitts Playback")
        self.setGeometry(140, 140, 1600, 1000)

        self.trials: list[TrialResult] = []
        self.metadata: dict = {}
        self.uses_host_time = False
        self._starts = np.zeros(0)
        self._ends = np.zeros(0)
        self._traj_times: list[np.ndarray] = []
        self._traj_xy: list[np.ndarray] = []
        self._traj_speed: list[np.ndarray] = []
        self._time_origin = 0.0
        self._trial_marker_items: list = []
        self._graph_zoom = 1.0
        self.speed_range = [0.0, 1.0]
        self.dist_range = [0.0, 1.0]
        self.synced = False
        self._head_samples: list = []
        self.pretest_summary: dict = {}
        self._head_task_range: tuple[float, float] | None = None
        self._play_range: tuple[float, float] = (0.0, 0.0)
        self._own_clock = PlaybackClock(self)
        self.clock = self._own_clock
        self.clock.time_changed.connect(self._on_clock_time)
        self.transport = PlaybackTransport(self)

        self._create_ui()
        self.transport.set_clock(self.clock)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _create_ui(self) -> None:
        open_action = QAction("Open Fitts Session...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_fitts_session)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(open_action)
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(open_action)
        self.addToolBar(toolbar)
        self._close_action = add_close_action(self, file_menu)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        left_layout = QVBoxLayout()
        self.canvas = FittsScreenCanvas()
        left_layout.addWidget(self.canvas, 2)

        self.graphs_widget = pg.GraphicsLayoutWidget()
        self.graphs_widget.ci.layout.setSpacing(2)
        self.graphs_widget.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.speed_plot = self.graphs_widget.addPlot(row=0, col=0)
        self.speed_plot.setLabel("left", "Speed (px/s)")
        self.speed_plot.showAxis("bottom", False)
        self.speed_curve = self.speed_plot.plot([], [], pen=pg.mkPen("#1f77b4", width=1.5), connect="finite")
        self.speed_plot.getViewBox().setDefaultPadding(0)
        self.speed_line = self.speed_plot.addLine(x=0, pen=pg.mkPen("#007bff", width=2, style=Qt.DashLine))
        self.dist_plot = self.graphs_widget.addPlot(row=1, col=0)
        self.dist_plot.setLabel("left", "Dist (px)")
        self.dist_plot.setLabel("bottom", "Time (s)")
        self.dist_curve = self.dist_plot.plot([], [], pen=pg.mkPen("#ff7f0e", width=1.5), connect="finite")
        self.dist_plot.getViewBox().setDefaultPadding(0)
        self.dist_line = self.dist_plot.addLine(x=0, pen=pg.mkPen("#007bff", width=2, style=Qt.DashLine))
        self.dist_plot.setXLink(self.speed_plot)
        left_layout.addWidget(self.graphs_widget, 1)
        self._trial_marker_legend = QLabel(
            "Vertical lines: <span style='color:#2e7d32'><b>┆ trial start</b></span> &nbsp; "
            "<span style='color:#8e24aa'><b>┆ trial end (click)</b></span>"
        )
        self._trial_marker_legend.setStyleSheet("font-size: 10px; color: #555;")
        left_layout.addWidget(self._trial_marker_legend)

        self.play_button = self.transport.play_button
        self.slider = self.transport.slider
        self.time_label = self.transport.time_label
        left_layout.addLayout(self.transport.make_layout())
        main_layout.addLayout(left_layout, 3)

        right_layout = QVBoxLayout()
        title = QLabel("<b>Fitts Session Data</b>")
        title.setStyleSheet("font-size: 13px;")
        right_layout.addWidget(title)

        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        exclude_row = QHBoxLayout()
        exclude_row.addWidget(QLabel("Exclude first N repetitions:"))
        self.exclude_spin = QSpinBox()
        self.exclude_spin.setRange(0, 100)
        self.exclude_spin.setToolTip(
            "Exclude the first N repetitions of each condition from summary statistics, "
            "for example to omit practice trials. Playback retains all trials."
        )
        self.exclude_spin.valueChanged.connect(self._refresh_summary)
        exclude_row.addWidget(self.exclude_spin)
        exclude_row.addStretch(1)
        stats_layout.addLayout(exclude_row)
        self.stats_label = MeasureLabel("No Fitts session loaded.")
        self.stats_label.setStyleSheet("font-size: 11px; font-family: monospace;")
        self.stats_label.setWordWrap(True)
        stats_layout.addWidget(self.stats_label)
        stats_layout.addSpacing(10)
        right_layout.addWidget(stats_widget)

        self.pretest_box = QGroupBox("Initial Pose & Range of Movement")
        pretest_layout = QVBoxLayout(self.pretest_box)
        self.pretest_label = MeasureLabel("No pre-test data recorded.")
        self.pretest_label.setStyleSheet("font-size: 11px; font-family: monospace;")
        self.pretest_label.setWordWrap(True)
        self.pretest_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        pretest_layout.addWidget(self.pretest_label)
        self.pretest_box.setVisible(False)
        right_layout.addWidget(self.pretest_box)

        self.raw_data_widget = MeasureLabel("No Fitts session loaded.<br>Use File → Open Fitts Session… to load one.")
        self.raw_data_widget.setStyleSheet(_PANEL_STYLE)
        self.raw_data_widget.setWordWrap(True)
        self.raw_data_widget.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        right_layout.addWidget(QLabel("📈 Live Data"))
        right_layout.addWidget(self.raw_data_widget, 1)
        right_layout.addWidget(self._create_controls_box())
        main_layout.addLayout(right_layout, 1)

    def _create_controls_box(self) -> QWidget:
        box = QGroupBox("Controls")
        box_layout = QVBoxLayout(box)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Graph Zoom:"))
        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setMaximumWidth(32)
        zoom_out_btn.clicked.connect(self._on_zoom_out)
        zoom_row.addWidget(zoom_out_btn)
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setMaximumWidth(32)
        zoom_in_btn.clicked.connect(self._on_zoom_in)
        zoom_row.addWidget(zoom_in_btn)
        zoom_row.addStretch(1)
        box_layout.addLayout(zoom_row)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed:"))
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setRange(0.1, 5.0)
        self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setMaximumWidth(60)
        self.transport.bind_speed_spinbox(self.speed_spinbox)
        speed_row.addWidget(self.speed_spinbox)
        speed_row.addStretch(1)
        box_layout.addLayout(speed_row)
        return box

    def _on_zoom_in(self) -> None:
        self._graph_zoom = min(self._graph_zoom * 1.25, 20.0)
        self._apply_graph_zoom()

    def _on_zoom_out(self) -> None:
        self._graph_zoom = max(self._graph_zoom / 1.25, 1.0)
        self._apply_graph_zoom()

    def _apply_graph_zoom(self) -> None:
        for plot, base in ((self.speed_plot, self.speed_range), (self.dist_plot, self.dist_range)):
            center = (base[0] + base[1]) / 2
            half = (base[1] - base[0]) / 2 / self._graph_zoom
            plot.setYRange(center - half, center + half, padding=0)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _on_open_fitts_session(self) -> None:
        start_dir = _DEFAULT_FITTS_DIR if _DEFAULT_FITTS_DIR.exists() else Path.home()
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Fitts Session File", str(start_dir), "Fitts Sessions (*.json);;All Files (*)"
        )
        if not filepath:
            return
        try:
            logger = FittsResultsLogger()
            logger.load(filepath)
            self._head_samples = []
            self.load_fitts_session(logger.trials, logger.metadata, Path(filepath).stem)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load Fitts session:\n\n{e}")
            return
        match = find_head_session_for_fitts(filepath, logger.metadata, logger.trials)
        if match:
            self.set_head_session_samples(match[2])
            self.statusBar().showMessage(f"Head-pose statistics from {match[0].name}")

    def set_head_session_samples(self, samples) -> None:
        """Supply the head-movement samples recorded alongside this Fitts session.

        Their ``metadata["task_phase"]`` labels add head-pose statistics to the
        pre-test summary; the Fitts file alone only yields cursor statistics.
        """
        self._head_samples = list(samples or [])
        self._refresh_pretest_summary()

    def load_fitts_session(self, trials: list[TrialResult], metadata: dict | None = None, name: str = "Fitts") -> None:
        """Load (or replace) the Fitts session being replayed (standalone playback)."""
        head_range = pretest_time_range(metadata)
        if not trials and head_range is None:
            raise ValueError("The Fitts session has no trials or timed head-movement tasks")
        self.setWindowTitle(f"Fitts Playback - {name}")
        self.trials = sorted(trials, key=lambda t: (t.start_time, t.trial_index))
        self.metadata = metadata or {}
        # Host time is used whenever every trial has it, so this window and a
        # synchronised head session share units; legacy files fall back to
        # their (self-consistent) monotonic start/end times.
        self.uses_host_time = all(
            t.start_host_time is not None and t.end_host_time is not None for t in self.trials
        )
        if self.uses_host_time:
            self._starts = np.array([t.start_host_time for t in self.trials], dtype=float)
            self._ends = np.array([t.end_host_time for t in self.trials], dtype=float)
        else:
            self._starts = np.array([t.start_time for t in self.trials], dtype=float)
            self._ends = np.array([t.end_time for t in self.trials], dtype=float)
            head_range = None  # head-task times are host times; they can't share this axis
        self._head_task_range = head_range
        spans = [head_range] if head_range is not None else []
        if self.trials:
            spans.append((float(self._starts[0]), float(self._ends.max())))
        self._play_range = (min(s for s, _ in spans), max(e for _, e in spans))
        self._time_origin = self._play_range[0]
        self._prepare_series()
        self._draw_trial_markers()
        self.canvas.set_session(self.trials, self._screen_size())
        self.graphs_widget.setVisible(bool(self.trials))
        self._trial_marker_legend.setVisible(bool(self.trials))
        self.exclude_spin.setEnabled(bool(self.trials))
        self._graph_zoom = 1.0
        self._apply_graph_zoom()
        self._refresh_summary()
        self._refresh_pretest_summary()
        self.transport.set_enabled(True)
        self.attach_clock(self._own_clock, synced=False)
        self.clock.set_range(*self._play_range)

    def attach_clock(self, clock: PlaybackClock, synced: bool = False) -> None:
        """Drive this window from `clock` (a shared clock when synchronised)."""
        if synced and not self.uses_host_time:
            raise ValueError("Synchronised playback requires host timestamps in the Fitts session")
        if self.clock is not clock:
            self.clock.pause()
            self.clock.time_changed.disconnect(self._on_clock_time)
            self.clock = clock
            clock.time_changed.connect(self._on_clock_time)
        self.synced = synced
        self.transport.set_clock(clock)

    def _screen_size(self) -> tuple[float, float]:
        w = self.metadata.get("screen_width_px")
        h = self.metadata.get("screen_height_px")
        if w and h:
            return float(w), float(h)
        # Legacy files: fit the bounding box of every target, click and path point.
        xs, ys = [], []
        for t in self.trials:
            for x, y in (t.start_pos, t.target_pos, t.click_pos):
                xs.append(x + t.width_px / 2)
                ys.append(y + t.width_px / 2)
            for p in t.trajectory or []:
                xs.append(p[1])
                ys.append(p[2])
        if not xs:
            return 1920.0, 1080.0
        return max(max(xs) + 20.0, 100.0), max(max(ys) + 20.0, 100.0)

    def _prepare_series(self) -> None:
        """Per-trial absolute times/points/speeds and the concatenated graph series."""
        self._traj_times, self._traj_xy, self._traj_speed = [], [], []
        graph_t, graph_speed, graph_dist = [], [], []
        for trial, start in zip(self.trials, self._starts):
            traj = np.asarray(trial.trajectory or [], dtype=float).reshape(-1, 3)
            times = start + traj[:, 0]
            xy = traj[:, 1:3]
            speed = np.zeros(len(xy))
            if len(xy) > 1:
                dt = np.diff(times)
                step = np.linalg.norm(np.diff(xy, axis=0), axis=1)
                with np.errstate(divide="ignore", invalid="ignore"):
                    speed[1:] = np.where(dt > 0, step / dt, 0.0)
            self._traj_times.append(times)
            self._traj_xy.append(xy)
            self._traj_speed.append(speed)
            if len(xy):
                dist = np.linalg.norm(xy - np.asarray(trial.target_pos, dtype=float), axis=1)
                graph_t.extend((times - self._time_origin).tolist() + [np.nan])
                graph_speed.extend(speed.tolist() + [np.nan])
                graph_dist.extend(dist.tolist() + [np.nan])

        t_arr = np.asarray(graph_t, dtype=float)
        self.speed_curve.setData(t_arr, np.asarray(graph_speed, dtype=float))
        self.dist_curve.setData(t_arr, np.asarray(graph_dist, dtype=float))
        for values, rng in ((graph_speed, self.speed_range), (graph_dist, self.dist_range)):
            finite = np.asarray(values, dtype=float)
            finite = finite[np.isfinite(finite)]
            lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
            pad = (hi - lo) * 0.03 if hi > lo else 1.0
            rng[0], rng[1] = lo - pad, hi + pad
        end = self._play_range[1]
        self.speed_plot.setXRange(0.0, max(end - self._time_origin, 0.001), padding=0)

    def _draw_trial_markers(self) -> None:
        """Vertical lines on both graphs marking each trial's start and its click."""
        for plot, item in self._trial_marker_items:
            plot.removeItem(item)
        self._trial_marker_items = []
        starts = self._starts - self._time_origin
        ends = self._ends - self._time_origin
        styles = ((starts, "#2e7d32", Qt.DashLine), (ends, "#8e24aa", Qt.DotLine))
        for plot in (self.speed_plot, self.dist_plot):
            for times, color, style in styles:
                for t in times:
                    line = plot.addLine(x=float(t), pen=pg.mkPen(color, width=1, style=style))
                    self._trial_marker_items.append((plot, line))

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def _filtered_trials(self) -> list[TrialResult]:
        n = self.exclude_spin.value()
        return [t for t in self.trials if t.repetition_index >= n]

    def _refresh_summary(self) -> None:
        if not self.trials:
            self.stats_label.setText(
                "<b>HeadMovement only</b><br>No mouse trials were recorded.<br>"
                "Pointing measures do not apply to this session."
            )
            return
        trials = self._filtered_trials()
        if not trials:
            self.stats_label.setText("No trials left after exclusion.")
            return
        iso = FittsStats(trials).summary()
        acc = FittsStats.accuracy_summary(trials)
        hits = sum(1 for t in trials if t.hit)
        duration = float(self._ends.max() - self._starts.min())
        access = self.metadata.get("access_method") or "—"
        if access == "Other" and self.metadata.get("access_method_other"):
            access = self.metadata["access_method_other"]
        self.stats_label.setText(
            f"<b>Access method:</b> {access}<br>"
            f"<b>Duration:</b> {duration:.1f}s<br>"
            f"<b>Trials:</b> {len(trials)} ({hits} hit)<br>"
            f"<b>Error rate:</b> {iso['overall_error_rate'] * 100:.1f}%<br>"
            f"<b>Mean MT:</b> {iso['overall_mean_movement_time_s'] * 1000:.0f} ms<br>"
            f"<b>Ae:</b> {iso['overall_effective_amplitude_px']:.1f} px<br>"
            f"<b>We:</b> {iso['overall_effective_width_px']:.1f} px<br>"
            f"<b>IDe:</b> {iso['overall_index_of_difficulty_bits']:.2f} bits<br>"
            f"<b>Throughput:</b> {iso['overall_throughput_bits_per_s']:.2f} bits/s<br>"
            f"<br><b>Path accuracy</b> (MacKenzie et al. 2001, "
            f"{acc['trial_count']} paths)<br>"
            f"<b>TRE:</b> {_fmt(acc['tre'], '.2f')} /trial<br>"
            f"<b>TAC:</b> {_fmt(acc['tac'], '.2f')} /trial<br>"
            f"<b>MDC:</b> {_fmt(acc['mdc'], '.2f')} /trial<br>"
            f"<b>ODC:</b> {_fmt(acc['odc'], '.2f')} /trial<br>"
            f"<b>MV:</b> {_fmt(acc['mv_px'], '.1f', ' px')}<br>"
            f"<b>ME:</b> {_fmt(acc['me_px'], '.1f', ' px')}<br>"
            f"<b>MO:</b> {_fmt(acc['mo_px'], '+.1f', ' px')}"
        )

    def _refresh_pretest_summary(self) -> None:
        """Initial rest position / range of movement stats for the loaded session."""
        summary = summarise_pretest(self.metadata, self._head_samples)
        self.pretest_summary = summary
        self.pretest_box.setVisible(bool(summary["available"]))
        self.pretest_label.setText(format_pretest_summary_html(summary))

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _on_clock_time(self, t: float) -> None:
        if not self.trials or (self._head_task_range is not None and t < self._starts[0]):
            if self._head_task_range is not None:
                self._show_head_task(t)
            return
        x = t - self._time_origin
        self.speed_line.setPos(x)
        self.dist_line.setPos(x)

        idx = int(np.searchsorted(self._starts, t, side="right")) - 1
        note = ""
        if idx < 0:
            idx = 0
            note = "Before Fitts session starts"
        trial = self.trials[idx]
        start, end = float(self._starts[idx]), float(self._ends[idx])
        clicked = t >= end
        if idx == len(self.trials) - 1 and t > end:
            note = "Fitts session has ended"

        mask = self._traj_times[idx] <= t
        points = self._traj_xy[idx][mask]
        speed = float(self._traj_speed[idx][mask][-1]) if mask.any() else 0.0
        if clicked:
            points = FittsStats.path_points(trial)
            speed = 0.0 if not len(self._traj_speed[idx]) else float(self._traj_speed[idx][-1])
        if len(points):
            cursor = (float(points[-1][0]), float(points[-1][1]))
        elif idx > 0:
            cursor = tuple(self.trials[idx - 1].click_pos)
        else:
            cursor = tuple(trial.start_pos)
        if note == "Before Fitts session starts":
            points = np.zeros((0, 2))
        self.canvas.set_state(idx, np.asarray(points, dtype=float).reshape(-1, 2), cursor, clicked)

        measures = FittsStats.path_measures(trial, np.asarray(points, dtype=float).reshape(-1, 2))
        elapsed = min(max(t, start), end) - start
        distance = math.dist(cursor, trial.target_pos)
        if clicked:
            outcome = "<span style='color:#28a028'><b>HIT</b></span>" if trial.hit else "<span style='color:#d22828'><b>MISS</b></span>"
        else:
            outcome = "in progress"
        m = measures or {}
        note_html = f"<span style='color:#b35900'><b>{note}</b></span><br>" if note else ""
        self.raw_data_widget.setText(
            note_html +
            f"<b>Time:</b> {x:.3f}s<br>"
            f"<b>Task:</b> {phase_description('fitts_test')}<br><br>"
            f"<b>Trial:</b> {idx + 1} / {len(self.trials)}<br>"
            f"<b>Block:</b> {trial.block_type}<br>"
            f"<b>Condition:</b> {trial.condition_label}<br>"
            f"<b>Repetition:</b> {trial.repetition_index + 1}<br>"
            f"<b>A:</b> {trial.amplitude_px:.0f} px &nbsp; <b>W:</b> {trial.width_px:.0f} px<br>"
            f"<b>Target:</b> ({trial.target_pos[0]:.0f}, {trial.target_pos[1]:.0f})<br><br>"
            f"<b>Cursor:</b> ({cursor[0]:.0f}, {cursor[1]:.0f})<br>"
            f"<b>Speed:</b> {speed:.0f} px/s<br>"
            f"<b>Distance to target:</b> {distance:.1f} px<br>"
            f"<b>Elapsed MT:</b> {elapsed * 1000:.0f} ms<br>"
            f"<b>Outcome:</b> {outcome}<br><br>"
            f"<b>Path accuracy (this trial):</b><br>"
            f"TRE: {m.get('tre', 'n/a')} &nbsp; TAC: {m.get('tac', 'n/a')}<br>"
            f"MDC: {m.get('mdc', 'n/a')} &nbsp; ODC: {m.get('odc', 'n/a')}<br>"
            f"MV: {_fmt(m.get('mv_px'), '.1f', ' px')}<br>"
            f"ME: {_fmt(m.get('me_px'), '.1f', ' px')}<br>"
            f"MO: {_fmt(m.get('mo_px'), '+.1f', ' px')}"
        )

    def _show_head_task(self, t: float) -> None:
        records = pretest_records(self.metadata)
        current = next(
            (record for record in reversed(records)
             if record.get("start_host_time") is not None and record["start_host_time"] <= t),
            records[0],
        )
        after_exercise = self._head_task_range is not None and t > self._head_task_range[1]
        points = [] if after_exercise else [
            point for point in current.get("cursor_trajectory", [])
            if point["host_time"] <= t
        ]
        if after_exercise:
            phase = None
            description = "Head-movement exercise finished - waiting for the mouse task to start"
        else:
            phase = current.get("phase", "HeadMovement")
            description = phase_description(phase)
            if current.get("cancelled"):
                description += " (cancelled)"
        self.canvas.phase_text = description
        self.canvas.set_state(
            -1, np.array([[p["x_px"], p["y_px"]] for p in points]).reshape(-1, 2),
            (points[-1]["x_px"], points[-1]["y_px"]) if points else None, False,
        )
        x = t - self._time_origin
        self.speed_line.setPos(x)
        self.dist_line.setPos(x)
        footer = (
            f"Mouse task starts in {self._starts[0] - t:.1f}s." if self.trials
            else "No mouse task in this recording."
        )
        phase_html = f"<b>Phase:</b> {phase}<br>" if phase else ""
        self.raw_data_widget.setText(
            f"<b>Time:</b> {x:.3f}s<br>"
            f"<b>Task:</b> {description}<br>"
            + phase_html +
            f"<b>Cursor points:</b> {len(points)}<br><br>"
            + footer
        )

    def closeEvent(self, event) -> None:
        self.clock.pause()
        super().closeEvent(event)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = FittsPlaybackWindow()
    window.show()
    sys.exit(app.exec())
