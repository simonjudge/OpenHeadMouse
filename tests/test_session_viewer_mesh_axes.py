"""Tests that the session viewer's wireframe rotates about the correct mesh axes."""

import numpy as np
import pytest

from core import quaternion_math as qm
from providers.uniface_provider import _CAMERA_TO_HEAD_FRAME
from session_viewer.mesh_visualizer import head_rotation_to_mesh_frame, rotation_from_quaternion

# Canonical mesh frame: X = lateral, Y = up, Z = out of the face.
MESH_LATERAL, MESH_UP, MESH_OUT = np.eye(3)


def _mesh_rotation(yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0):
    q = qm.from_euler_zyx(*np.radians([yaw_deg, pitch_deg, roll_deg]))
    return head_rotation_to_mesh_frame(rotation_from_quaternion(*q))


@pytest.mark.parametrize(
    "angles, fixed_axis",
    [
        ({"yaw_deg": 30.0}, MESH_UP),        # turning the head spins about the vertical axis
        ({"pitch_deg": 30.0}, MESH_LATERAL),  # nodding spins about the ear-to-ear axis
        ({"roll_deg": 30.0}, MESH_OUT),       # tilting spins about the nose axis
    ],
)
def test_each_euler_angle_rotates_mesh_about_expected_axis(angles, fixed_axis):
    R = _mesh_rotation(**angles)
    np.testing.assert_allclose(R @ fixed_axis, fixed_axis, atol=1e-9)
    assert not np.allclose(R, np.eye(3))


def test_inverts_uniface_camera_to_head_mapping():
    r_mesh = rotation_from_quaternion(*qm.from_euler_zyx(0.3, -0.2, 0.1))
    r_head = _CAMERA_TO_HEAD_FRAME @ r_mesh @ _CAMERA_TO_HEAD_FRAME.T
    np.testing.assert_allclose(head_rotation_to_mesh_frame(r_head), r_mesh, atol=1e-12)


@pytest.mark.parametrize("angles", [(0, 0, 0), (25, -12.5, 10), (-30, 15, -20)])
def test_desktop_front_on_camera_and_angle_labels_follow_axis_tips(qt_app, monkeypatch, angles):
    from core.recorder import Recorder
    from session_viewer.session_viewer import SessionVisualizerWindow

    vertices = np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 1]], dtype=float)
    edges = np.array([[0, 1], [1, 2], [0, 2]])

    def load_mesh(window):
        window.base_vertices, window.edges = vertices, edges

    monkeypatch.setattr(SessionVisualizerWindow, "_load_mesh_data", load_mesh)
    recorder = Recorder()
    recorder.set_calibration([(0, qm.IDENTITY_QUATERNION)], qm.IDENTITY_QUATERNION)
    recorder.record_sample(0, qm.IDENTITY_QUATERNION)
    recorder.record_sample(1, qm.from_euler_zyx(*np.radians(angles)))
    window = SessionVisualizerWindow(recorder._samples, recorder=recorder)
    try:
        assert window.gl_view.opts["elevation"] == 0
        assert window.gl_view.opts["azimuth"] == 0
        np.testing.assert_allclose(
            window.mesh_gl_item.pos, vertices[:, [2, 0, 1]][edges].reshape(-1, 3),
        )
        for time, (yaw, pitch, roll) in ((0, (0, 0, 0)), (1, angles)):
            window._on_clock_time(time)
            tips = (np.eye(3) * 1.25 @ _mesh_rotation(yaw, pitch, roll).T)[:, [2, 0, 1]]
            for index, (axis, value) in enumerate((("Pitch", pitch), ("Yaw", yaw))):
                label = window.axis_label_items[index]
                assert label.text == f"{axis} {value:+.1f} deg"
                np.testing.assert_allclose(label.pos, tips[index], atol=1e-9)
                np.testing.assert_allclose(label.pos, window.axis_gl_items[index].pos[1])
    finally:
        window.close()


def test_reference_toggle_changes_only_mesh_and_labels(qt_app, monkeypatch):
    import copy
    import pyqtgraph as pg
    from core.recorder import Recorder
    from session_viewer.mesh_visualizer import ANGLE_COLORS
    from session_viewer.session_viewer import SessionVisualizerWindow

    vertices = np.eye(3)
    edges = np.array([[0, 1], [1, 2]])

    def load_mesh(window):
        window.base_vertices, window.edges = vertices, edges

    monkeypatch.setattr(SessionVisualizerWindow, "_load_mesh_data", load_mesh)
    recorder = Recorder()
    reference = qm.from_euler_zyx(*np.radians([30, 20, -10]))
    recorder.set_calibration([(0, reference)], reference)
    recorder.record_sample(0, reference)
    recorder.record_sample(1, qm.from_euler_zyx(*np.radians([-15, -5, 12])))
    window = SessionVisualizerWindow()
    try:
        window.relative_pose_checkbox.setChecked(False)  # Safe before loading data.
        window.relative_pose_checkbox.setChecked(True)
        window.load_session(recorder._samples, recorder=recorder)
        assert window.reference_pose_label.text() == "Reference pose: Calibration pose"
        assert window.relative_pose_checkbox.isChecked()
        before_frames = copy.deepcopy(window.frame_data)
        before_stats = window.stats_label.text()
        before_graphs = [curve.getData()[1].copy() for curve in (
            window.yaw_curve, window.pitch_curve, window.vel_curve, window.acc_curve,
        )]
        for time in (0, 1):
            window.clock.seek(time)
            relative_pos = window.mesh_gl_item.pos.copy()
            for checked in (False, True, False, True):
                window.relative_pose_checkbox.setChecked(checked)
                q = recorder._samples[time].quaternion
                if checked:
                    q = qm.relative_rotation(reference, q)
                rotation = head_rotation_to_mesh_frame(rotation_from_quaternion(*q))
                expected = (vertices @ rotation.T)[:, [2, 0, 1]][edges].reshape(-1, 3)
                np.testing.assert_allclose(window.mesh_gl_item.pos, expected, atol=1e-7)
                yaw, pitch, _ = np.degrees(qm.to_euler_zyx(q))
                assert window.axis_label_items[0].text == f"Pitch {pitch:+.1f} deg"
                assert window.axis_label_items[1].text == f"Yaw {yaw:+.1f} deg"
                if checked:
                    np.testing.assert_allclose(window.mesh_gl_item.pos, relative_pos)
            assert window.current_frame_index == time
        assert window.frame_data == before_frames
        assert window.stats_label.text() == before_stats
        for curve, values in zip(
            (window.yaw_curve, window.pitch_curve, window.vel_curve, window.acc_curve), before_graphs,
        ):
            np.testing.assert_array_equal(curve.getData()[1], values)
        assert window.yaw_curve.opts["pen"].color().name() == ANGLE_COLORS["yaw"]
        assert window.pitch_curve.opts["pen"].color().name() == ANGLE_COLORS["pitch"]
        for item, axis in zip(window.axis_gl_items, ("pitch", "yaw", "roll")):
            np.testing.assert_allclose(item.color, pg.glColor(ANGLE_COLORS[axis]))
        for item, axis in zip(window.axis_label_items, ("pitch", "yaw")):
            assert item.color.name() == ANGLE_COLORS[axis]
    finally:
        window.close()
