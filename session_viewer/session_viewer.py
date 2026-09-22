"""Native PyQt-based session visualizer with interactive 3D mesh and line graphs.

Uses PyQtGraph for fast, responsive line charts and pyqtgraph.opengl for native,
flicker-free 3D mesh rendering. Opens as a standalone window with no session
loaded; use File > Open Session (or the toolbar button) to load a recording.

Call: visualize_session_native(recorded_samples=None, session_name, recorder)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path for standalone execution
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import numpy as np
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSlider, QPushButton, QLabel,
    QDoubleSpinBox, QGroupBox, QToolBar, QFileDialog, QMessageBox
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QTimer
import pyqtgraph as pg
import pyqtgraph.opengl as gl

from core.recorder import Recorder
from core.session_stats import SessionStats
from core import quaternion_math as qm
from session_viewer.mesh_visualizer import (
    calculate_neutral_quaternion, download_mediapipe_model,
    load_obj_wireframe, centre_and_scale, rotation_from_quaternion,
    rotation_from_euler
)

# Axis colors matching the previous Plotly X/Y/Z convention
_AXIS_COLORS = [(0.84, 0.15, 0.16, 1.0), (0.17, 0.63, 0.17, 1.0), (0.12, 0.47, 0.71, 1.0)]

_DEFAULT_SESSIONS_DIR = project_root / "sessions"


class SessionVisualizerWindow(QMainWindow):
    """Native PyQt window for session visualization.

    Opens with an empty shell (mesh view, empty graphs, no live data). Call
    `load_session()` - or use File > Open Session / the toolbar button - to
    populate it with a recording.
    """

    def __init__(self, recorded_samples: list | None = None, session_name: str = "Head Movement", recorder=None):
        super().__init__()
        self.setWindowTitle("Session Visualizer")
        self.setGeometry(100, 100, 1900, 1200)

        self.recorded_samples = []
        self.recorder = None
        self.current_frame_index = 0
        self.is_playing = False
        self.playback_speed = 1.0
        self._graph_zoom = 1.0

        self.frame_data: list = []
        self.timestamps: list = []
        self.yaws: list = []
        self.pitches: list = []
        self.velocities: list = []
        self.accelerations: list = []
        self.yaw_range = [-1.0, 1.0]
        self.pitch_range = [-1.0, 1.0]
        self.vel_range = [-1.0, 1.0]
        self.acc_range = [-1.0, 1.0]
        self.neutral_q = np.array([1.0, 0.0, 0.0, 0.0])

        # Mesh geometry doesn't depend on a session - load it once up front.
        self._load_mesh_data()

        # Playback timer
        self.playback_timer = QTimer()
        self.playback_timer.timeout.connect(self._on_playback_tick)
        self.last_playback_time = 0

        # Create UI (empty shell)
        self._create_ui()

        if recorded_samples:
            self.load_session(recorded_samples, session_name, recorder)

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------

    def _prepare_frame_data(self) -> list:
        """Prepare frame metadata for visualization."""
        frame_data = []
        start_time = self.recorded_samples[0].timestamp if self.recorded_samples else 0

        for sample in self.recorded_samples:
            try:
                q = sample.quaternion if isinstance(sample.quaternion, np.ndarray) else np.array(sample.quaternion)
                if len(q) == 4:
                    q_rel = qm.relative_rotation(self.neutral_q, q)
                    yaw_rel, pitch_rel, roll_rel = qm.to_euler_zyx(q_rel)
                    yaw_deg_label = np.degrees(yaw_rel)
                    pitch_deg_label = np.degrees(pitch_rel)
                    roll_deg_label = np.degrees(roll_rel)
                else:
                    raise ValueError("Invalid quaternion")
            except (AttributeError, ValueError, TypeError):
                yaw_deg_label = sample.yaw_deg
                pitch_deg_label = sample.pitch_deg
                roll_deg_label = sample.roll_deg

            frame_data.append({
                'timestamp': sample.timestamp,
                'time_rel': sample.timestamp - start_time,
                'quaternion': [float(sample.quaternion_w), float(sample.quaternion_x),
                              float(sample.quaternion_y), float(sample.quaternion_z)],
                'yaw_deg': float(sample.yaw_deg),
                'pitch_deg': float(sample.pitch_deg),
                'roll_deg': float(sample.roll_deg),
                'yaw_rel_deg': float(yaw_deg_label),
                'pitch_rel_deg': float(pitch_deg_label),
                'roll_rel_deg': float(roll_deg_label),
            })

        return frame_data

    def _calculate_velocities(self) -> list:
        """Calculate angular velocities."""
        velocities = [0]
        for i in range(1, len(self.frame_data)):
            dt = self.frame_data[i]['time_rel'] - self.frame_data[i-1]['time_rel']
            if dt > 0:
                dyaw = self.frame_data[i]['yaw_rel_deg'] - self.frame_data[i-1]['yaw_rel_deg']
                dpitch = self.frame_data[i]['pitch_rel_deg'] - self.frame_data[i-1]['pitch_rel_deg']
                vel = np.sqrt(dyaw**2 + dpitch**2) / dt
                velocities.append(vel)
            else:
                velocities.append(velocities[-1] if velocities else 0)
        return velocities

    def _calculate_accelerations(self) -> list:
        """Calculate angular accelerations."""
        accelerations = [0, 0]
        for i in range(2, len(self.velocities)):
            dt = self.frame_data[i]['time_rel'] - self.frame_data[i-1]['time_rel']
            if dt > 0:
                acc = abs(self.velocities[i] - self.velocities[i-1]) / dt
                accelerations.append(acc)
            else:
                accelerations.append(0)
        return accelerations

    def _load_mesh_data(self):
        """Load and prepare mesh data."""
        model_path = download_mediapipe_model()
        self.base_vertices, faces, self.edges = load_obj_wireframe(model_path)
        self.base_vertices = centre_and_scale(self.base_vertices)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _create_ui(self):
        """Create the main UI layout (empty shell - no session data yet)."""
        self._create_menu_and_toolbar()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)

        # Left side: 3D mesh (native OpenGL) + PyQtGraph line charts
        left_layout = QVBoxLayout()

        # Mesh widget (native 3D visualization, no browser/HTML)
        self.mesh_widget = QWidget()
        self.mesh_layout = QVBoxLayout(self.mesh_widget)
        self.mesh_layout.setContentsMargins(0, 0, 0, 0)
        self._create_mesh_visualization()
        left_layout.addWidget(self.mesh_widget, 2)

        # Line graphs
        self.graphs_widget = pg.GraphicsLayoutWidget()
        self._create_line_graphs()
        left_layout.addWidget(self.graphs_widget, 1)

        # Slider + playback controls
        control_layout = QHBoxLayout()

        self.play_button = QPushButton("▶ Play")
        self.play_button.clicked.connect(self._on_play_pause)
        self.play_button.setMaximumWidth(80)
        self.play_button.setEnabled(False)
        control_layout.addWidget(self.play_button)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.setValue(0)
        self.slider.setEnabled(False)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        control_layout.addWidget(self.slider)

        self.time_label = QLabel("0.000s")
        self.time_label.setMaximumWidth(80)
        control_layout.addWidget(self.time_label)

        left_layout.addLayout(control_layout)

        main_layout.addLayout(left_layout, 3)

        # Right side: Stats, live data, and the zoom/speed controls box
        right_layout = QVBoxLayout()

        # Stats panel
        title_label = QLabel("<b>Session Data</b>")
        title_label.setStyleSheet("font-size: 13px;")
        right_layout.addWidget(title_label)
        stats_widget = self._create_stats_widget()
        right_layout.addWidget(stats_widget)

        # Raw/live data panel
        self.raw_data_widget = QLabel("No session loaded.<br>Use File → Open Session… to load a recording.")
        self.raw_data_widget.setStyleSheet("""
            background: #f5f5f5;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 10px;
            font-family: monospace;
            font-size: 10px;
        """)
        self.raw_data_widget.setWordWrap(True)
        right_layout.addWidget(QLabel("📈 Live Data"))
        right_layout.addWidget(self.raw_data_widget, 1)

        # Bottom-right controls box: graph Y-zoom + playback speed
        right_layout.addWidget(self._create_controls_box())

        main_layout.addLayout(right_layout, 1)

    def _create_menu_and_toolbar(self):
        """Create the File menu and a toolbar button, both loading a session file."""
        open_action = QAction("Open Session...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._on_open_session)

        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(open_action)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(open_action)
        self.addToolBar(toolbar)

    def _create_controls_box(self) -> QWidget:
        """Bottom-right box: graph Y-axis zoom buttons + playback speed control."""
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
        self.speed_spinbox.setMinimum(0.1)
        self.speed_spinbox.setMaximum(5.0)
        self.speed_spinbox.setValue(1.0)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setMaximumWidth(60)
        self.speed_spinbox.valueChanged.connect(self._on_speed_changed)
        speed_row.addWidget(self.speed_spinbox)
        speed_row.addStretch(1)
        box_layout.addLayout(speed_row)

        return box

    def _mesh_line_segments(self, R: np.ndarray) -> np.ndarray:
        """Rotate mesh vertices and return an (E*2, 3) array of edge endpoints for GLLinePlotItem(mode='lines').

        Remaps axes (old_x, old_y, old_z) -> (old_z, old_x, old_y) so the mesh renders under
        GLViewWidget's fixed Z-up convention exactly as it did under the previous Plotly camera
        (eye=(0,0,z), up=(0,1,0)): old_z becomes the camera-facing axis, old_y becomes screen-up.
        """
        v = self.base_vertices @ R.T
        v = v[:, [2, 0, 1]]
        return v[self.edges].reshape(-1, 3)

    def _create_mesh_visualization(self):
        """Create the 3D mesh visualization using a native OpenGL widget (no browser/HTML/JS)."""
        self.gl_view = gl.GLViewWidget()
        self.gl_view.setBackgroundColor(255, 255, 255, 255)
        self.gl_view.setCameraPosition(distance=4, elevation=0, azimuth=0)

        R = np.eye(3)
        pos = self._mesh_line_segments(R)
        self.mesh_gl_item = gl.GLLinePlotItem(
            pos=pos, color=(0.12, 0.12, 0.12, 1.0), width=1.5, mode='lines', antialias=True
        )
        self.gl_view.addItem(self.mesh_gl_item)

        self.axis_gl_items = []
        for color in _AXIS_COLORS:
            item = gl.GLLinePlotItem(
                pos=np.array([[0, 0, 0], [0, 0, 0]], dtype=float),
                color=color, width=3, antialias=True
            )
            self.gl_view.addItem(item)
            self.axis_gl_items.append(item)

        self.mesh_layout.addWidget(self.gl_view, 1)

    def _create_line_graphs(self):
        """Create PyQtGraph line plots for dynamics (empty until a session is loaded)."""
        self.graphs_widget.ci.layout.setSpacing(2)
        self.graphs_widget.ci.layout.setContentsMargins(0, 0, 0, 0)

        # Yaw plot
        self.yaw_plot = self.graphs_widget.addPlot(row=0, col=0)
        self.yaw_plot.setLabel('left', 'Yaw')
        self.yaw_plot.showAxis('bottom', False)
        self.yaw_curve = self.yaw_plot.plot([], [], pen=pg.mkPen('#d62728', width=1.5))
        self.yaw_plot.setYRange(*self.yaw_range, padding=0)
        self.yaw_plot.getViewBox().setDefaultPadding(0)
        self.yaw_line = self.yaw_plot.addLine(x=0, pen=pg.mkPen('#007bff', width=2, style=Qt.DashLine))

        # Pitch plot
        self.pitch_plot = self.graphs_widget.addPlot(row=1, col=0)
        self.pitch_plot.setLabel('left', 'Pitch')
        self.pitch_plot.showAxis('bottom', False)
        self.pitch_curve = self.pitch_plot.plot([], [], pen=pg.mkPen('#2ca02c', width=1.5))
        self.pitch_plot.setYRange(*self.pitch_range, padding=0)
        self.pitch_plot.getViewBox().setDefaultPadding(0)
        self.pitch_line = self.pitch_plot.addLine(x=0, pen=pg.mkPen('#007bff', width=2, style=Qt.DashLine))

        # Velocity plot
        self.vel_plot = self.graphs_widget.addPlot(row=2, col=0)
        self.vel_plot.setLabel('left', 'Vel')
        self.vel_plot.showAxis('bottom', False)
        self.vel_curve = self.vel_plot.plot([], [], pen=pg.mkPen('#1f77b4', width=1.5))
        self.vel_plot.setYRange(*self.vel_range, padding=0)
        self.vel_plot.getViewBox().setDefaultPadding(0)
        self.vel_line = self.vel_plot.addLine(x=0, pen=pg.mkPen('#007bff', width=2, style=Qt.DashLine))

        # Acceleration plot
        self.acc_plot = self.graphs_widget.addPlot(row=3, col=0)
        self.acc_plot.setLabel('left', 'Accel')
        self.acc_plot.showAxis('bottom', False)
        self.acc_curve = self.acc_plot.plot([], [], pen=pg.mkPen('#ff7f0e', width=1.5))
        self.acc_plot.setYRange(*self.acc_range, padding=0)
        self.acc_plot.getViewBox().setDefaultPadding(0)
        self.acc_line = self.acc_plot.addLine(x=0, pen=pg.mkPen('#007bff', width=2, style=Qt.DashLine))

    def _on_zoom_in(self):
        """Zoom in on the Y axis of all four graphs."""
        self._graph_zoom = min(self._graph_zoom * 1.25, 20.0)
        self._apply_graph_zoom()

    def _on_zoom_out(self):
        """Zoom out on the Y axis of all four graphs (capped at the full data range)."""
        self._graph_zoom = max(self._graph_zoom / 1.25, 1.0)
        self._apply_graph_zoom()

    def _apply_graph_zoom(self):
        """Apply the current zoom factor, centered, to all four graphs' Y ranges."""
        for plot, base_range in (
            (self.yaw_plot, self.yaw_range), (self.pitch_plot, self.pitch_range),
            (self.vel_plot, self.vel_range), (self.acc_plot, self.acc_range),
        ):
            center = (base_range[0] + base_range[1]) / 2
            half = (base_range[1] - base_range[0]) / 2 / self._graph_zoom
            plot.setYRange(center - half, center + half, padding=0)

    def _create_stats_widget(self) -> QWidget:
        """Create the statistics panel (empty until a session is loaded)."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.stats_label = QLabel("No session loaded.")
        self.stats_label.setStyleSheet("font-size: 11px; font-family: monospace;")
        layout.addWidget(self.stats_label)
        layout.addSpacing(10)
        return widget

    def _refresh_stats_widget(self):
        try:
            stats = SessionStats(self.recorded_samples)
            rom = stats.cervical_rom()
            vel = stats.angular_velocity_stats()
            acc = stats.angular_acceleration_stats()
            duration = stats.session_duration()

            stats_text = (
                f"<b>Duration:</b> {duration:.1f}s<br>"
                f"<b>Yaw:</b> [{rom['yaw_min']:+.1f}°, {rom['yaw_max']:+.1f}°] "
                f"({rom['yaw_range']:.1f}°)<br>"
                f"<b>Pitch:</b> [{rom['pitch_min']:+.1f}°, {rom['pitch_max']:+.1f}°] "
                f"({rom['pitch_range']:.1f}°)<br>"
                f"<b>Roll:</b> [{rom['roll_min']:+.1f}°, {rom['roll_max']:+.1f}°] "
                f"({rom['roll_range']:.1f}°)<br>"
                f"<b>Vel max:</b> {vel['max']:.2f}°/s<br>"
                f"<b>Acc max:</b> {acc['max']:.2f}°/s²"
            )
        except Exception as e:
            stats_text = f"Error: {str(e)}"
        self.stats_label.setText(stats_text)

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _on_open_session(self):
        """File > Open Session / toolbar button handler."""
        start_dir = _DEFAULT_SESSIONS_DIR if _DEFAULT_SESSIONS_DIR.exists() else Path.home()
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Session File", str(start_dir),
            "JSON Sessions (*.json);;CSV Sessions (*.csv);;All Files (*)"
        )
        if not filepath:
            return
        try:
            recorder = Recorder()
            samples = recorder.load(Path(filepath))
            self.load_session(samples, f"Session - {Path(filepath).stem}", recorder=recorder)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load session:\n\n{e}")

    def load_session(self, recorded_samples: list, session_name: str = "Head Movement", recorder=None):
        """Load (or replace) the session being visualized."""
        self.setWindowTitle(f"Session Visualizer - {session_name}")
        self.recorded_samples = recorded_samples
        self.recorder = recorder
        self.current_frame_index = 0
        self.is_playing = False
        self.playback_timer.stop()
        self.play_button.setText("▶ Play")
        self._graph_zoom = 1.0

        self.neutral_q = calculate_neutral_quaternion(recorded_samples, recorder=recorder)
        self.frame_data = self._prepare_frame_data()
        self.timestamps = [f['time_rel'] for f in self.frame_data]
        self.yaws = [f['yaw_rel_deg'] for f in self.frame_data]
        self.pitches = [f['pitch_rel_deg'] for f in self.frame_data]
        self.velocities = self._calculate_velocities()
        self.accelerations = self._calculate_accelerations()

        # Calculate data ranges for plot scaling
        self.yaw_range = [min(self.yaws), max(self.yaws)]
        self.pitch_range = [min(self.pitches), max(self.pitches)]
        self.vel_range = [min(self.velocities), max(self.velocities)]
        self.acc_range = [min(self.accelerations), max(self.accelerations)]

        # Add small padding to ranges (just enough to avoid clipping the trace at the edges)
        for r in [self.yaw_range, self.pitch_range, self.vel_range, self.acc_range]:
            pad = (r[1] - r[0]) * 0.03 if r[1] > r[0] else 1
            r[0] -= pad
            r[1] += pad

        self.yaw_curve.setData(self.timestamps, self.yaws)
        self.pitch_curve.setData(self.timestamps, self.pitches)
        self.vel_curve.setData(self.timestamps, self.velocities)
        self.acc_curve.setData(self.timestamps, self.accelerations)
        self._apply_graph_zoom()

        self.slider.blockSignals(True)
        self.slider.setMinimum(0)
        self.slider.setMaximum(len(self.frame_data) - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.slider.setEnabled(True)
        self.play_button.setEnabled(True)

        self._refresh_stats_widget()
        self._update_display(0)
        self._update_mesh_for_current_frame()

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _on_slider_moved(self, value: int):
        """Handle slider movement."""
        self.is_playing = False
        self.playback_timer.stop()
        self.play_button.setText("▶ Play")
        self._update_display(value)
        # Immediately update mesh when slider moves
        self._update_mesh_for_current_frame()

    def _on_play_pause(self):
        """Toggle playback."""
        if self.is_playing:
            self.is_playing = False
            self.playback_timer.stop()
            self.play_button.setText("▶ Play")
        else:
            self.is_playing = True
            self.play_button.setText("⏸ Pause")
            self.last_playback_time = self.timestamps[self.current_frame_index]
            # 40ms timer = ~25 fps update rate
            self.playback_timer.start(40)

    def _on_speed_changed(self, value: float):
        """Handle playback speed change."""
        self.playback_speed = value

    def _on_playback_tick(self):
        """Update frame during playback using real-time speed."""
        self.last_playback_time += 0.04 * self.playback_speed  # 40ms * speed factor

        # Find the frame at this time
        frame_index = 0
        for i, t in enumerate(self.timestamps):
            if t <= self.last_playback_time:
                frame_index = i
            else:
                break

        # If we've reached the end, stop playback
        if frame_index >= len(self.frame_data) - 1:
            self.is_playing = False
            self.playback_timer.stop()
            self.play_button.setText("▶ Play")
            frame_index = len(self.frame_data) - 1

        self._update_display(frame_index)
        self._update_mesh_for_current_frame()

    def _update_mesh_for_current_frame(self):
        """Update mesh visualization for current frame via direct GL buffer updates (no HTML/JS, no flicker)."""
        frame = self.frame_data[self.current_frame_index]

        try:
            q = np.array(frame['quaternion'])
            q_rel = qm.relative_rotation(self.neutral_q, q)
            R = rotation_from_quaternion(q_rel[0], q_rel[1], q_rel[2], q_rel[3])
        except Exception:
            R = rotation_from_euler(frame['yaw_rel_deg'], frame['pitch_rel_deg'],
                                   frame['roll_rel_deg'], degrees=True)

        pos = self._mesh_line_segments(R)
        self.mesh_gl_item.setData(pos=pos)

        tips = (np.eye(3) * 1.25) @ R.T
        tips = tips[:, [2, 0, 1]]
        for item, tip in zip(self.axis_gl_items, tips):
            item.setData(pos=np.array([[0, 0, 0], tip]))

    def _update_display(self, frame_index: int):
        """Update all visualizations for the current frame."""
        if frame_index < 0 or frame_index >= len(self.frame_data):
            return

        self.current_frame_index = frame_index
        frame = self.frame_data[frame_index]

        # Update slider
        self.slider.blockSignals(True)
        self.slider.setValue(frame_index)
        self.slider.blockSignals(False)

        # Update time label
        self.time_label.setText(f"{frame['time_rel']:.3f}s")

        # Update indicator lines on graphs
        time_val = frame['time_rel']
        self.yaw_line.setPos(time_val)
        self.pitch_line.setPos(time_val)
        self.vel_line.setPos(time_val)
        self.acc_line.setPos(time_val)

        # Update raw/live data display
        q = frame['quaternion']
        raw_html = (
            f"<b>Time:</b> {frame['time_rel']:.3f}s<br><br>"
            f"<b>Quaternion:</b><br>"
            f"[{q[0]:.4f}, {q[1]:.4f}, {q[2]:.4f}, {q[3]:.4f}]<br><br>"
            f"<b>Absolute:</b><br>"
            f"Yaw: {frame['yaw_deg']:+.1f}°<br>"
            f"Pitch: {frame['pitch_deg']:+.1f}°<br>"
            f"Roll: {frame['roll_deg']:+.1f}°<br><br>"
            f"<b>Relative:</b><br>"
            f"Yaw: {frame['yaw_rel_deg']:+.1f}°<br>"
            f"Pitch: {frame['pitch_rel_deg']:+.1f}°<br>"
            f"Roll: {frame['roll_rel_deg']:+.1f}°<br><br>"
            f"<b>Dynamics:</b><br>"
            f"Velocity: {self.velocities[frame_index]:.1f}°/s<br>"
            f"Acceleration: {self.accelerations[frame_index]:.1f}°/s²"
        )
        self.raw_data_widget.setText(raw_html)


def visualize_session_native(recorded_samples: list | None = None, session_name: str = "Head Movement", recorder=None) -> SessionVisualizerWindow:
    """Launch a native PyQt visualization window.

    Opens immediately, with or without a session; if no samples are given the
    window starts empty and the user loads a file via File > Open Session or
    the toolbar button.

    Args:
        recorded_samples: Optional list of RecordedSample objects
        session_name: Title for the window (only used if recorded_samples is given)
        recorder: Optional Recorder instance with events
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    window = SessionVisualizerWindow(recorded_samples, session_name, recorder)
    window.show()

    # Don't block - return immediately so main app continues
    return window


if __name__ == '__main__':
    """Standalone mode - opens empty; use File > Open Session to load a recording."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = SessionVisualizerWindow()
    window.show()

    sys.exit(app.exec())
