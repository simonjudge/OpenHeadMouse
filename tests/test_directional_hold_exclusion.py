import copy

import numpy as np
import pytest

from colab_session_viewer.data import (
    Session, head_pretest_summary, head_range_diagnostics, participant_rows, playback_payload,
)
from core import quaternion_math as qm
from core.recorder import Recorder


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
@pytest.mark.parametrize("clock", ["event", "provider_event", "metadata", "fallback"])
def test_each_directional_hold_is_trimmed_in_all_head_measures_and_playback(tmp_path, direction, clock):
    recorder = Recorder(tmp_path)
    recorder.set_calibration([(0, qm.IDENTITY_QUATERNION)], qm.IDENTITY_QUATERNION)
    phase = f"range_of_movement.{direction}.hold"
    for time, label, angle in [
        (0, f"range_of_movement.{direction}.move", 0),
        (1, phase, 80), (1.5, phase, 60), (2, phase, 10), (2.5, phase, 12),
        (3, "fitts_test", 0),
    ]:
        recorder.record_sample(time, qm.from_euler_zyx(np.radians(angle), np.radians(angle / 2), 0),
                               metadata={"task_phase": label}, host_time=1000 + time)
    if clock in ("event", "provider_event"):
        recorder._events = [{"timestamp": 1, "host_time": 1001 if clock == "event" else None, "event_type": "task_phase",
                             "data": {"phase": phase, "action": "start"}}]
    session = Session("P", "Test", samples=recorder._samples, recorder=recorder)
    if clock == "metadata":
        session.metadata = {"pretest_phases": [{"phase": phase, "start_host_time": 1001, "end_host_time": 1003}]}
    original = copy.deepcopy(session.samples)
    calibration = copy.deepcopy(recorder.calibration)
    baseline = participant_rows([session])[0]
    summary = head_pretest_summary(session, initial_rest_exclude_s=1)
    held = summary["head_range_of_movement"]["directions"][direction]
    assert held["sample_count"] == 2
    assert held["angle_mean_deg"] == pytest.approx(11 if direction in ("left", "right") else 5.5)
    row = participant_rows([session], initial_rest_exclude_s=1)[0]
    assert row["head_movement_samples"] == 4
    for key in ("targets_analysed", "planned_id_mean_bits", "head_samples", "head_duration_s"):
        assert row[key] == baseline[key]
    assert row["head_range_of_movement_task_yaw_range_deg_session_mean"] == pytest.approx(12)
    assert row["head_range_of_movement_task_yaw_std_deg_session_mean"] == pytest.approx(np.std([0, 10, 12]))
    check = head_range_diagnostics(session, initial_rest_exclude_s=1)
    assert check["yaw_range_deg"] == pytest.approx(12)
    mesh = tmp_path / "mesh.obj"
    mesh.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    payload = playback_payload(session, mesh_path=mesh, initial_rest_exclude_s=1)
    assert len(payload["frames"]) == len(original)
    assert [frame["excluded"] for frame in payload["frames"]] == [False, True, True, False, False, False]
    assert payload["session_summary"] == row
    assert [point[2] for point in payload["head_graphs"][0]["points"]] == [False, True, True, False, False, False]
    for before, after in zip(original, session.samples):
        np.testing.assert_array_equal(before.quaternion, after.quaternion)
    assert recorder.calibration == calibration
    assert head_pretest_summary(session, initial_rest_exclude_s=10)["head_range_of_movement"] is None
