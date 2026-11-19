# Recording file formats

This reference describes the files currently written by OpenHeadMouse. For the
Colab viewer's ZIP assignment manifest, including its columns and validation
rules, see [ZIP assignment manifests](../colab_session_viewer/README.md#zip-assignment-manifests).
The manifest identifies and pairs recordings; it does not contain them.

The chosen recordings folder normally contains `sessions/` for head recordings
and `fitts_sessions/` for Fitts results. Head recordings are JSON or CSV,
depending on the recorder configuration; Fitts results are JSON. Filenames
normally use `session_<epoch-seconds>` and `fitts_session_<epoch-seconds>`.
See [Recordings folder](../README.md#recordings-folder) for choosing the folder.
Files can contain participant data and notes; take care when sharing them.

## Clocks and coordinates

- A head sample's `timestamp` comes from its provider. Its origin and clock
  depend on the provider and cannot generally be compared with another file.
- `host_time`, `start_host_time` and `end_host_time` use the shared host wall
  clock in Unix-epoch seconds, identified as `unix_epoch_s`. Use these to align
  recordings from the same run, not the provider timestamp or Fitts monotonic
  time. Wall-clock adjustments can affect the timestamps.
- Quaternions are scalar-first `[w, x, y, z]`, in the canonical head frame:
  X forward, Y left, Z up. Saved yaw, pitch and roll are in degrees, derived
  directly from the saved quaternion; they are not automatically relative to
  the calibration pose. See [coordinate frames](coordinate-frames.md).
- Fitts positions are `[x, y]` in task-canvas pixels, with x increasing right
  and y increasing down. Durations are seconds unless otherwise indicated.

## Head movement: JSON

The writer is [core/recorder.py](../core/recorder.py). A simplified example
without calibration:

```json
{
  "format_version": "1.0",
  "sample_count": 1,
  "clock": {"host_time": "unix_epoch_s"},
  "events": [],
  "samples": [
    {
      "timestamp": 12.5,
      "quaternion_w": 1.0,
      "quaternion_x": 0.0,
      "quaternion_y": 0.0,
      "quaternion_z": 0.0,
      "yaw_deg": 0.0,
      "pitch_deg": 0.0,
      "roll_deg": 0.0,
      "metadata": {"task_phase": "fitts_test"},
      "host_time": 1700000000.5
    }
  ]
}
```

| Top-level field | Meaning |
|---|---|
| `format_version` | `"1.0"` without calibration; `"1.1"` when the optional `calibration` object is present. This version alone does not establish whether host times are available. |
| `sample_count` | Number of movement `samples`, excluding separately saved calibration samples. |
| `clock` | Host-clock identifier; older files may not have it. |
| `events` | Recorded events, possibly empty. |
| `samples` | Movement sample objects in recording order. |
| `calibration` | Optional initial/rest calibration summary and samples, described below. |

Each sample has provider `timestamp`, quaternion components, and `yaw_deg`,
`pitch_deg`, `roll_deg`. `metadata` is an object containing provider-specific
data when available; older samples may lack it. When a head recording accompanies
the tasks, `metadata.task_phase` can label samples, for example
`initial_rest.hold`, `range_of_movement.left.move`, or `fitts_test`.
`host_time` is a host-clock timestamp or `null` when unavailable.

Each event has `timestamp`, `host_time`, `event_type` and a `data` object.
For example, `recenter` data contains the scalar-first reference `quaternion`
and sometimes `source`; `task_phase` data contains `phase` and `action`
(`start`, `end` or `cancel`). Event timestamps normally use the latest provider
timestamp, with a monotonic fallback if no sample is available. Event host time
is captured when the event is recorded. Older events may lack host time.

### Calibration object

When calibration is captured, the JSON `calibration` object contains:

| Field | Meaning |
|---|---|
| `start_timestamp`, `end_timestamp` | First and last calibration sample provider timestamps; `null` if no samples. |
| `duration_s` | Duration supplied by the capture workflow, or `null`. |
| `sample_count` | Number of calibration samples. |
| `neutral_quaternion` | Scalar-first neutral pose `[w, x, y, z]`. |
| `neutral_yaw_deg`, `neutral_pitch_deg`, `neutral_roll_deg` | Euler angles derived from the neutral quaternion. |
| `yaw_std_deg`, `pitch_std_deg`, `roll_std_deg` | Population standard deviations of sample Euler angles relative to the neutral quaternion. |
| `samples` | Calibration samples in the same object shape as movement samples. Their `host_time` can be `null`. |

Calibration samples are separate from `samples`; the latter is not implicitly
recentered. The initial-rest hold in a Fitts run can also provide the saved
calibration. See [Initial calibration](../README.md#initial-calibration).

## Head movement: CSV

Each row is one sample. The base columns, in order, are:

```text
timestamp,quaternion_w,quaternion_x,quaternion_y,quaternion_z,yaw_deg,pitch_deg,roll_deg
```

Additional columns are appended only when needed:

| Column | Meaning |
|---|---|
| `metadata` | A JSON object encoded in the CSV cell; included if any row has metadata. |
| `host_time` | Shared host time in seconds; included if any row has it, with blank cells where unavailable. |
| `phase` | Included when calibration exists: `calibration` rows precede `movement` rows. This is separate from `metadata.task_phase`. |

CSV does not save the JSON `clock`, event list or calibration summary. It saves
calibration samples as rows; the loader reconstructs a calibration summary from
them. Older CSVs may have only the base columns. An empty recording is written
as an empty file.

## Fitts results: JSON

The writer and trial definition are in [fitts_law/results.py](../fitts_law/results.py).
The top-level object contains:

| Field | Meaning |
|---|---|
| `format_version` | Current writer emits `"1.1"`; earlier files can lack host timestamps. |
| `trial_count` | Number of completed entries in `trials`. |
| `metadata` | Session settings, clock and optional task information. |
| `trials` | Completed trial objects in recording order; may be empty for head-only tasks. |

A shortened example of a trial:

```json
{
  "condition_label": "example",
  "block_type": "circle",
  "amplitude_px": 300.0,
  "width_px": 50.0,
  "start_pos": [100.0, 100.0],
  "target_pos": [400.0, 100.0],
  "click_pos": [398.0, 102.0],
  "start_time": 12.0,
  "end_time": 12.8,
  "movement_time_s": 0.8,
  "hit": true,
  "trial_index": 0,
  "repetition_index": 0,
  "trajectory": [[0.1, 150.0, 100.0], [0.7, 390.0, 101.0]],
  "start_host_time": 1700000000.0,
  "end_host_time": 1700000000.8
}
```

| Trial field | Meaning |
|---|---|
| `condition_label` | Identifier for the condition. |
| `block_type` | Task type: `circle`, `edge`, `random_path` or `typing`. |
| `amplitude_px`, `width_px` | Trial's nominal amplitude and target width in pixels; values may be derived from the task layout. |
| `start_pos`, `target_pos`, `click_pos` | Start, target and actual selection positions, each `[x, y]` in pixels. |
| `start_time`, `end_time` | Monotonic-clock readings in seconds, used for movement time; not Unix timestamps. |
| `movement_time_s` | Recorded movement duration in seconds. |
| `hit` | Whether the selection met the task's hit criterion. |
| `trial_index` | Zero-based trial position in the session. |
| `repetition_index` | Zero-based repetition index within the condition; typing tasks use the word index. |
| `trajectory` | Recorded mouse-move points `[seconds_since_trial_start, x_px, y_px]`; may be empty and does not necessarily include the click. |
| `start_host_time`, `end_host_time` | Shared host-clock timestamps, or `null`/absent in older recordings. |

### Fitts metadata

Common fields include `access_method`, `access_method_other`, `notes`,
`screen_width_px`, `screen_height_px` and `clock.host_time`. Fields may be
`null` or absent in older files. The app also records `record_head_movement`,
`task_selection` and `start_mode` for a run. `head_session_file`, when saved,
identifies the associated head recording; it is a path from the original machine
and may not remain valid if the files are moved.

For runs with pre-test tasks, metadata can additionally contain:

- `pretest_configuration`: `initial_rest_enabled`,
  `range_of_movement_enabled`, `move_duration_s` and `hold_duration_s`.
- `pretest_phases`: ordered phase objects with `phase`, `task`, `segment`,
  `direction` (or `null`), configured `duration_s`, `start_host_time`,
  `end_host_time` and `cursor_trajectory`. Each cursor point is an object
  `{ "host_time": ..., "x_px": ..., "y_px": ... }`. A cancelled phase can
  include `cancelled: true`.
- `fitts_task`: `phase` (`fitts_test`), `start_host_time`, `end_host_time`
  and, once ended, `completed`. Times may be `null` if the task did not start
  or finish.

See [Recording head movement during a Fitts test](../README.md#recording-head-movement-during-a-fitts-test)
and [Fitts playback and synchronised playback](../README.md#fitts-playback-and-synchronised-playback)
for task and playback behavior. Legacy files without the necessary shared host
timestamps can still be viewed separately but cannot be reliably synchronised.
