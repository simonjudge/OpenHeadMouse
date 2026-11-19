import re

import numpy as np
import pytest

from colab_session_viewer.app import NotebookViewer
from colab_session_viewer.data import Session, field_description, participant_rows, participant_tables
from core import quaternion_math as qm
from core.recorder import RecordedSample


def test_axis_columns_are_contiguous_and_ordered_in_every_summary_and_session_table():
    viewer = NotebookViewer()
    viewer.sessions = [Session("P", "Test")]
    viewer.include_dwell.value = True
    tables = participant_tables(viewer.summary_rows())
    for label, rows in tables.items():
        axes = []
        for key in rows[0]:
            axis = next((axis for axis in ("yaw", "pitch", "roll") if axis in key), None)
            if axis is None and "_directions_" in key:
                axis = "yaw" if any(f"_{d}_" in key for d in ("left", "right")) else "pitch"
            if axis:
                axes.append(("yaw", "pitch", "roll").index(axis))
        assert axes == sorted(axes), label
    columns = list(tables["Initial head positions"][0])
    for axis in ("pitch", "roll"):
        assert columns[columns.index(f"head_initial_rest_{axis}_mean_deg_session_mean") + 1] == (
            f"head_initial_rest_{axis}_std_deg_session_mean"
        )
    markup = viewer.table.value
    # Every main/session table and its field key share exactly the same order.
    assert re.findall(r'<th title="([^"]+)"', markup) == re.findall(r"<code>([^<]+)</code>", markup)


def test_rom_population_sd_uses_exercise_or_directional_hold_samples_and_session_means():
    def sample(time, phase, yaw):
        return RecordedSample(
            time, *qm.from_euler_zyx(np.radians(yaw), 0, 0), yaw, 0, 0,
            metadata={"task_phase": phase}, host_time=1000 + time,
        )

    first = Session("P", "One", samples=[
        sample(0, "range_of_movement.left.move", 0),
        sample(1, "range_of_movement.left.hold", 2),
        sample(2, "range_of_movement.left.hold", 6),
        sample(3, "fitts_test", 100),  # Neither exercise nor held SD includes this.
    ])
    second = Session("P", "Two", samples=[
        sample(0, "range_of_movement.left.hold", 10),
        sample(1, "range_of_movement.left.hold", 10),
    ])
    row = participant_rows([first, second], head_opening_exclude_s=100)[0]
    task = "head_range_of_movement_task_yaw_std_deg_session_mean"
    held = "head_range_of_movement_directions_left_angle_std_deg_session_mean"
    assert row[task] == pytest.approx((np.std([0, 2, 6]) + 0) / 2)
    assert row[held] == pytest.approx((np.std([2, 6]) + 0) / 2)
    assert "population" in field_description(task).lower()
    assert "population" in field_description(held).lower()
    table = participant_tables([row])["Range of movement stats"][0]
    keys = list(table)
    mean = "head_range_of_movement_directions_left_angle_mean_deg_session_mean"
    assert keys[keys.index(mean) + 1] == held
    missing = participant_tables(participant_rows([Session("Q", "Empty")]))["Range of movement stats"][0]
    assert missing[task] is None
    assert missing[held] is None
