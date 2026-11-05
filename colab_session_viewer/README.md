# Colab session viewer

The notebook [session_viewer.ipynb](session_viewer.ipynb) runs locally or in
Google Colab. This folder reuses the repository's pure-Python recording,
quaternion, mesh and statistics modules; it is not a standalone single-file
notebook, so a complete project checkout must be available in either case.

## Where to inspect the calculations

The notebook is a launcher, not a second implementation of the statistics.
Calculation functions have docstrings describing inputs, units, formulas,
selection rules, aggregation and unavailable/degenerate results. Use the
function names below with your editor's **Go to Symbol** or text search.
The table **Field keys** describe the displayed/exported columns in column order.

| Measures or step | Source and calculation functions |
|---|---|
| Main participant results and session aggregation | [data.py](data.py): `participant_rows`. This is the main entry point; the UI's [app.py](app.py) `summary_rows` passes global settings. Session tables call the same function with one session. |
| Planned ID | [data.py](data.py): `planned_id` calculates `log2(D/W + 1)` from actual saved start/target distance and width; `participant_rows` averages per-target IDs. |
| MT, Ae, We, IDe, errors and throughput | [fitts_law/results.py](../fitts_law/results.py): `FittsStats.summary`, `effective_amplitude`, `_axis_signed_error`, `effective_width`, `index_of_difficulty`, `error_rate`, `throughput_bits_per_s`. Ae is mean Euclidean start-to-click distance; We is `4.133 * sample SD` of projected click error; IDe is `log2(Ae/We+1)`; TP is IDe/mean MT. |
| Cursor path TRE, TAC, MDC, ODC, MV, ME, MO | [fitts_law/results.py](../fitts_law/results.py): `path_points`, `task_axis_coords`, `_sign_changes`, `path_measures` and its seven metric helpers; `accuracy_summary` averages usable targets. |
| MTnodwell, adjusted throughput, validity counts | [dwell.py](dwell.py): `selection_settings`, `selection_summary`. Subtract each target's session-specific dwell duration **before** filtering negative results and averaging. Recalculate IDe on valid adjusted targets for adjusted TP; do not subtract a duration from an aggregated mean. |
| Dwell residence, settling, cursor/head movement and ranges | [dwell.py](dwell.py): `target_intervals` solves line-segment/circle intersections; `_window_points` interpolates visit endpoints; `_trial_dwell` calculates per-target estimates; `selection_summary` averages available estimates and reports denominators. |
| Excluded targets and their head intervals | [data.py](data.py): `retained_trials` removes the first N targets **per file**; `_target_sample_mask` removes their inclusive host-clock intervals from head estimates. |
| Recording-opening and initial-rest exclusions | [data.py](data.py): `_head_movement_mask`, `_initial_rest_after_opening`, `head_pretest_summary`. These have distinct scopes; neither modifies calibration/reference data. |
| Head speed, acceleration, sampling rate and raw Euler ranges | [core/session_stats.py](../core/session_stats.py): `SessionStats.times`, `_usable_intervals`, `angular_speed_series`, `angular_acceleration_series`, `sampling_stats`, `cervical_rom` and summary helpers. Speed is geodesic quaternion angle/dt; acceleration is absolute change in scalar speed/dt. |
| Exclusion-safe kinematics and relative movement ranges | [data.py](data.py): `_movement_series` splits retained runs before differentiating; `head_range_diagnostics` unwraps reference-relative Euler angles and measures max-min. `participant_rows` pools usable dynamics values and takes the largest within-recording range per axis. |
| Initial-rest pose, directional held poses and offsets | [session_viewer/pretest_stats.py](../session_viewer/pretest_stats.py): `_mean`, `_std`, `_pose_stats`, `head_initial_rest_stats`, `head_range_of_movement_stats`, `summarise_pretest`. [data.py](data.py) `head_pretest_summary` adds exclusions/calibration-relative yaw and `_circular_mean_deg`; `participant_rows` aggregates sessions and separately calculates full movement-exercise max-min ranges. |
| Cursor initial-rest steadiness and exercise extremes | [session_viewer/pretest_stats.py](../session_viewer/pretest_stats.py): `cursor_initial_rest_stats`, `cursor_range_of_movement_stats`. These shared desktop pre-test measures are not included in the notebook's head-only pre-test panel. |
| Saved calibration and playback reference | [core/recorder.py](../core/recorder.py): `Recorder.set_calibration` stores supplied neutral and relative-pose spread. [data.py](data.py): `calibration_mean_yaw`. [mesh_visualizer.py](../session_viewer/mesh_visualizer.py): `calculate_neutral_quaternion` chooses recenter/calibration/rest/startup reference, in that order. |
| Quaternion angles, distances and held-pose mean | [core/quaternion_math.py](../core/quaternion_math.py): `relative_rotation`, `to_euler_zyx`, `angle_between`, `mean_quaternion`. Scalar-first quaternions `[w,x,y,z]`; internal angles radians, displayed angles degrees. |
| Strict-ID and closest-condition matched comparisons | [data.py](data.py): `matched_fitts_rows`, `_condition_matched_fitts_rows`, `_condition_test_quotas`, `_fitts_comparison_row`. Only these comparisons use matched subsets; original main results are never subsetted by matching. |
| Observed test conditions/counts | [data.py](data.py): `fitts_condition_rows`. Reads saved Fitts trials, not inferred settings or head measurements. |
| Playback plot cursor speed/distance and head series | [data.py](data.py): `playback_payload`; [viewer.js](viewer.js): `stateAt`, `pathMeasures`. The JavaScript path formulas mirror Python for seek-time feedback; main tables/CSVs are calculated in Python. |
| Table grouping, display rounding and exports | [data.py](data.py): `participant_tables`, `column_label`, `field_description`, `format_number`, `summary_csv`; [app.py](app.py): `_refresh_table`, `_table_html`. Table grouping does not recompute measures. Rounding is display/export-only; calculations retain precision. |

### Reading the results correctly

- **Main Fitts results pool targets**, including misses, after global first-N
  exclusions. Overall IDe/TP are recalculated from that pool, not averaged from
  conditions. The current Euclidean Ae and cross-condition pooling are legacy
  descriptive formulas, not sequence-level task-axis ISO throughput.
- **Path/dwell means average per-target estimates**, equally, over the available
  contributing targets. They are not time-weighted; coverage counts identify
  the denominator. Effective Fitts width uses **sample SD** (`n-1`), whereas
  held-pose/pre-test spread uses **population SD** (`n`). Zero effective width
  has a documented legacy zero IDe/TP result, not a reliable performance estimate.
- **Head aggregation differs by measure**: speeds/accelerations pool usable
  interval values; participant movement range takes the largest recording's
  range; pre-test values average available session summaries equally (yaw means
  circularly). These are not interchangeable with pooling every head sample.
- **Raw playback and analysed statistics differ deliberately**: playback retains
  excluded data and marks grey periods; summary exclusions operate in Python.
  Graphs forward-fill dynamics for display, while statistics use interval values.
- **Desktop scope is explicit**: [session_viewer.py](../session_viewer/session_viewer.py)
  `_refresh_stats_widget` uses the full loaded head stream, `_calculate_dynamics`
  uses `SessionStats`; [fitts_playback.py](../session_viewer/fitts_playback.py)
  `_refresh_summary` uses `FittsStats`, `_prepare_series` prepares cursor graphs
  and `_on_clock_time` calculates live path feedback. Desktop Fitts exclusions
  use `repetition_index`, not the notebook's first-N targets, and do not implement
  notebook matching/dwell controls.

For executable examples and edge cases, see
[Fitts formulas](../tests/test_fitts_stats.py),
[head dynamics](../tests/test_session_stats.py),
[pre-test measures](../tests/test_fitts_pretest_stats.py),
[dwell estimates](../tests/test_colab_dwell.py),
[matching](../tests/test_colab_fitts_matching.py) and
[exclusions](../tests/test_colab_rest_exclusion.py).

## Running locally

1. Clone the repository (or use your existing checkout).
2. From the repository root, install the viewer dependencies and a Jupyter
   front-end into your Python environment:

   ```powershell
   python -m pip install -r colab_session_viewer\requirements.txt
   python -m pip install jupyterlab
   ```

3. Start JupyterLab from the repository root, passing it the notebook path:

   ```powershell
   python -m jupyter lab colab_session_viewer\session_viewer.ipynb
   ```

   JupyterLab serves the notebook locally and opens it in your browser. Run the
   setup, loading and session-viewer cells in order. Alternatively, open
   [session_viewer.ipynb](session_viewer.ipynb) in VS Code with its built-in
   notebook support and run the cells there. `npx serve` is not a substitute:
   it serves static files but cannot execute notebook cells.

Leave `REVISION` and `PROJECT_DIR` in the setup cell unchanged: setup detects
the project containing the notebook and prefers it over an older nested
checkout, so nothing is cloned or downloaded except the MediaPipe canonical
face mesh if it is not already present. The viewer cell reloads its modules and
prints the loaded source path. If a different checkout was already imported,
restart the kernel and rerun setup. This keeps the six separate summary tables
in sync with your local code rather than showing a previous version's combined
table.

## Running on Google Colab

Open [session_viewer.ipynb](session_viewer.ipynb) in Google Colab and run the
setup, loading and session-viewer cells. Use the notebook's **File > Open notebook > GitHub**
option after publishing this folder to a branch, or upload the notebook.
Set `REVISION` in the setup cell to that published branch/tag. For unpublished
work, upload the complete changed project to the runtime and set `PROJECT_DIR`
to its folder. Use a CPU runtime; no camera, mouse driver, desktop GUI or GPU
is needed.

Only the dependencies in [requirements.txt](requirements.txt) are needed.
Do not install the desktop requirements: no Qt, tracker, camera, mouse control,
OpenGL, server, GPU or Colab tunnel is used. Setup downloads the public MediaPipe
canonical face mesh over verified HTTPS when it is not already present.
Recordings stay in the notebook/runtime/browser and are not sent to a playback
service. Colab itself receives files you upload; follow your study's data policy.

## Import and playback

The loading cell displays only import controls, ZIP review and manifest downloads.
The separate session-viewer cell displays the single head/cursor viewer and summary
tables. Both share the same `viewer` instance; loading more data updates the viewer
without rerunning its cell, and rerunning either display cell keeps the collection.
Restart the kernel to pick up code changes to an existing viewer instance.
For Python use, `viewer.show_loading()` and `viewer.show_session()` display those
blocks independently; `viewer.show()` still displays the combined interface.
Import
controls do not interrupt the playback/statistics area. Select the viewer's
participant and session with the two dropdowns. Play/pause, rewind, seek, playback speed,
graph zoom, head-camera rotation/zoom, cursor trails, target/click markers,
angular/cursor plots and live values use the desktop viewer's recording
semantics. Browser canvas rendering replaces the desktop's Qt/OpenGL widgets;
it is not a pixel-identical native UI. No data is resampled or time-stretched.

**Show relative to reference pose** is checked initially, matching desktop
playback. Uncheck it to show the recorded pose before the reference pose is
removed. Only the mesh and its angle-tip labels change; angle graphs always
show relative pose, and statistics and exports are unchanged. The source of
the reference pose is shown above the mesh. Saved recentering takes priority
over calibration, followed by labelled initial rest, then up to the first 30
samples within one second. Pitch is red, yaw green and roll blue throughout
the viewer's meshes, reference diagrams and angle graphs.

Terms used in headings and field descriptions:

- **Recorded pose:** orientation reported by the tracker before removing any
  reference pose; not necessarily a compass heading or a level head.
- **Reference pose:** the pose used as playback zero, with its source shown.
- **Relative pose:** movement measured from the reference pose, using a 3D
  rotation rather than separately subtracting yaw, pitch and roll.
- **Calibration pose:** the saved calibration orientation.
- **Calibration heading:** only the yaw of the calibration pose.
- **Initial rest:** the labelled still period before the movement exercise,
  distinct from calibration even if both were recorded at the same time.
- **Movement range:** largest angle minus smallest angle for the stated data
  and reference, with jumps at +/-180 degrees removed.
- **Average / spread (SD):** the typical value and how much values vary.
  Field descriptions state how recordings are combined and how SD is calculated.

CSV field keys are unchanged, including legacy names containing `absolute`,
`relative`, `neutral`, `mean` or `std`; their field descriptions explain the
meaning in the terms above. In particular, initial-rest yaw from calibration
heading removes only calibration yaw, unlike playback's full reference pose.

### Bundled demo data

For a demo-only presentation, use `NotebookViewer(demo_mode=True)` (optionally
with `demo_directory=...`), or set `DEMO_MODE = True` in the notebook's loading
cell before creating the viewer. This automatically imports the bundled demos.
`show_loading()` then displays only **Loaded Demo Data**, without upload controls,
folder settings or manifest links. `show_session()` is unchanged; the combined
`show()` also hides loading controls in this mode. Import errors or warnings are
still displayed rather than claiming success. The default `demo_mode=False`
preserves the full loading interface and optional demo checkbox.
Restart the kernel before changing modes for an already-created notebook viewer;
rerunning display cells intentionally preserves the existing instance and data.

Check **Load Demo Data** at the top of the loading block to import ZIPs from
`colab_session_viewer/demodata` in the current project checkout. This default
is resolved from the project/module location, not the notebook working directory,
so it works locally and in Colab. Put demo ZIPs
directly in that folder, each with a valid root CSV manifest (any CSV filename).
No Google Drive authorization, mounting or public sharing links are needed.
In Colab, the demo folder must be included in the published checkout or uploaded
project. Use lowercase `demodata` (Colab paths are case-sensitive).
To use another local folder, set `DEMO_DATA_DIR` in the loading cell or edit
**Demo folder** in the loading block.

All ZIPs directly in the folder are validated before any demo sessions are added.
A missing/invalid manifest, recording or duplicate participant/session rejects
the entire new batch with an import error. Manifestless demos are not guessed or
sent to review; use manual ZIP import to label them first. Loaded ZIP paths are
remembered for this viewer: unchecking does not delete data, and rechecking skips
already loaded ZIPs but loads newly added ones. Fix an error and check again to
retry. Start a new viewer/kernel to reload a modified ZIP at the same path.
Bundled demos are distributed with the project; include only data appropriate
for public sharing and follow the notebook runtime's privacy policy.

For each session, enter participant/session names, upload a head JSON/CSV, a
Fitts JSON or a matching pair, then click **Add session**. Repeat for any number
of participants/tests. Head and Fitts files from the same test form one session.
Pairs require shared host timestamps and at least two head samples inside the
test's time range, just like desktop synchronized playback. Legacy files can
be viewed separately, not artificially synchronized. Head-only exercises
recorded in Fitts files and their pre-test cursor trajectories are supported.

You can also import multiple ZIP data sets, **with or without a manifest**.
Without one, upload the ZIP and click **Import dataset ZIP**. Head JSON/CSV and
Fitts JSON files are identified by their contents, not their filenames.
The review step shows file paths, host timestamp ranges, sample/target counts,
and Fitts access-method/notes metadata alongside editable participant/session
labels and head/Fitts file selections. Invalid/unrecognized JSON/CSV files are
listed in the import notes rather than preventing other recordings from loading.

Pair suggestions use overlapping **shared host timestamps** stored in the files,
including pre-test phases. Each file must be the other's unique best match
(greatest overlap relative to the combined span, then closest start time).
At least two head samples must fall inside the Fitts time range. Tied/conflicting
matches remain separate with a review note; participant identity is never
inferred. Files without shared host timestamps remain standalone. The viewer
aligns matched streams on their original host clock, using the same median
provider-to-host offset as desktop playback, not by shifting their starts to zero.

The **Participant** input above the upload controls supplies the initial participant
label for every manifestless ZIP review row, including added rows. You can edit
each row independently; existing manifest labels are not replaced.
Enter participant and session labels for the included rows. You may change either
file selection, untick rows, or use **Add assignment row** to split a suggestion.
A file can occur only once within an import, to avoid accidental double counting.
Click **Import labelled sessions** to add the collection; failed validation leaves
it in review without partially importing. **Cancel ZIP review** discards only the
pending ZIP. Existing sessions remain unchanged.

After importing, **Download manifest.csv for ZIP import N** saves those assignments
with the full original relative paths. Put the downloaded file at the root of the
original ZIP to replay the same import without re-entering labels. Each imported
ZIP has its own manifest link so paths from different ZIPs cannot collide.
The manifest contains identity/pairing assignments, not source recordings or
analysis exclusion settings; keep the source ZIP and set exclusions separately.
Example:

```csv
participant,session,head_file,fitts_file,selection_method,dwell_time_s
P01,Test 1,P01/head1.json,P01/fitts1.json,external_switch,
P01,Test 2,P01/head2.csv,P01/fitts2.json,external_switch,
P02,Test 1,,P02/fitts1.json,dwell,0.6
```

Paths use `/` inside the ZIP; either file column may be blank. Files can share
names in different folders. Any root-level CSV name (for example `A manifest.csv`)
is checked for the manifest columns `participant,session,head_file,fitts_file`;
the name need not be `manifest.csv`. Only a CSV with those columns and valid
nonempty assignments is treated as a manifest. A recognized but invalid manifest
fails explicitly, rather than silently discovering/importing unlisted files.
If multiple root manifests are valid, keep only the one you intend to import.
Unrelated CSVs are not treated as manifests.
The optional `selection_method` values are `external_switch`, `dwell`, `mouse`,
`other` (non-dwell) and `unknown`. A `dwell` row requires a positive finite
`dwell_time_s` in **seconds**; leave that column blank for non-dwell methods.
Older four-column manifests still load as `unknown`; no method or duration is
guessed from participant identity or head input device. Both the individual-file
import controls and each manifestless ZIP review row offer these same fields.
Downloaded manifests preserve them. The collapsed Fitts conditions inventory
shows each file's selection labels for checking.
An existing root manifest is authoritative and is
imported directly: only its listed files are loaded, and its labels and pairings
are not replaced with suggestions.
A duplicate participant/session name or invalid manifest-referenced file
rejects that batch without partially adding it.
Imports append to the existing collection; earlier uploads are not replaced.
For files on mounted Drive, use `viewer.add_session(participant, session,
head_path=..., fitts_path=..., selection_method="dwell", dwell_time_s=0.6)` instead
(use your actual dwell setting; 0.6 is illustrative).

## Dwell timing and movement estimates

In **Fitts calculation review**, enable **Add MTnodwell, no-dwell throughput and
dwell behaviour**. Only **MTnodwell** and **no-dwell throughput** are added to the
Fitts tables. The counts, residence, settling and movement estimates appear in a
separate **Dwell behaviour stats** table with a collapsed individual-session
table and its own CSV. The optional matched comparison likewise separates its
Fitts and dwell behaviour tables/CSVs, using only matched targets for both.
The original MT, IDe, throughput and other measures are unchanged.
MTnodwell sits immediately to the right of MT, and no-dwell throughput sits
immediately to the right of throughput, including session and matched tables.
Both adjusted measures show **-** when the participant/session has no dwell
selection (CSV values are blank), rather than duplicating the original measures.
For non-dwell/unknown selection, dwell-only fields show **-**, not zero.
Genuine zero counts or movements for dwell selection remain zero. Numeric CSVs
keep unavailable/inapplicable values blank, rather than inserting text dashes.

- **MTnodwell = recorded MT - configured dwell time** for dwell selection.
  Subtraction is performed **for each target using its session's duration**,
  before invalid times are excluded and the remaining adjusted times averaged;
  no duration is subtracted from an aggregated participant mean.
  Known non-dwell methods retain their original MT. Unknown methods are excluded
  from adjusted timing, not silently treated as non-dwell. Negative adjusted
  times are flagged and excluded only from adjusted timing; zero is retained.
- **No-dwell throughput** uses the pooled IDe of the valid adjusted targets
  divided by their average MTnodwell. A zero mean yields a blank throughput,
  not infinity. Valid/invalid/unknown counts show the denominator.
- Dwell attempts are estimated as **all cursor visits inside the target**,
  summing time across visits. Cursor path length, horizontal/vertical ranges,
  total 3D head rotation and yaw/pitch/roll ranges are averaged per target.
  Path lengths never bridge exits/re-entries. Axis ranges cover all visits.
- **Settling time** is first target entry to the beginning of the final
  uninterrupted inside-target period, including exits and returns. **Final
  inside duration** runs from that final entry to selection; a selection outside
  the target has zero final residence and no settling estimate.

These are trajectory-based proxies, **not recorded software dwell-timer
events**. Target residence is not guaranteed to exceed configured dwell time:
the software can use a separate stability region, and a selection may miss.
Cursor boundaries are linearly interpolated between recorded samples, adding
the saved start position at trial start and click position at selection.
Sparse/event-driven sampling can miss brief exits or movement. Circular target
regions use radius width/2, matching pointing-task hit testing; typing targets
have different rectangular regions and are explicitly counted as unsupported
for spatial estimates (their timing can still be adjusted).

Head estimates require host timestamps and full head coverage of the
inside-target intervals. Quaternion boundary rotations are interpolated; axis
ranges use unwrapped recorded Euler angles. Missing/partial coverage and missing
cursor paths give blank estimates with contributing-target counts, not zero
movement. Means pool available per-target estimates, not duration-weighted means.
Global target exclusions apply first; only the separate matched table further
restricts these estimates to selected targets. The existing recording-opening
and initial-rest exclusions keep their existing scopes and do not redefine
these target-based dwell windows.

## Exclusions and aggregation

### Planned-ID comparison and calculation review

The existing Fitts table's difficulty column is explicitly labelled
**Effective ID (IDe, bits)**. This is not planned ID:

- Planned **ID = log2(D/W + 1)** uses the saved start/target-centre distance D
  and target width W. Target count alone does not determine ID. Configured
  circle amplitude/radius can differ from actual presented movement distance,
  for example because of screen fitting or a different target sequence.
- **IDe = log2(Ae/We + 1)** uses measured endpoint spread, with
  **We = 4.133 × sample SD** of task-axis endpoint errors. Participants doing
  the same planned test can therefore have different IDe.

The original participant and session Fitts tables show **Planned ID average
(bits)** immediately before IDe, using the arithmetic mean across analysed
trials after target exclusions. A mixture of conditions has multiple IDs;
this column is their trial-weighted average, not a single configured setting.

The first item under **Fitts stats** is a collapsed **Fitts test conditions by
file** table, with a CSV and ordered field key. Each row describes a saved
participant/Fitts file, using only its Fitts trials: lists of amplitude settings,
widths and block types, observed distinct target positions per condition/repetition,
observed repetitions per condition and recorded/analysed trial totals.
Targets per repetition and repetitions per condition are different settings.
The inventory uses all recorded trials; only the analysed-count column applies
target exclusions. Optional ID matching does not change it.

These recordings do not save the complete Fitts settings panel. Counts are
therefore labelled **observed**: incomplete runs, repeated positions or missing
repetitions cannot establish the intended target/repetition setting. For the
circle generator, saved amplitude is a configured radius, capped by screen fit;
it must not be confused with the start-to-target movement distance used for ID.

**Path TRE** is the average count of re-entries per usable path, not the number
of initial target entries or a percentage. The first entry is not counted;
paths with no re-entry contribute zero, so an average below 1 is expected.
The path-trial-count column is its denominator.

Below the existing Fitts stats table, enable **Show matched-target comparison**
to populate a second table and its CSV. The original
table and all other analyses remain unchanged. Global first-target exclusions
apply before matching.

Every measure in the main Fitts table, including ID and IDe, uses all targets
across that participant's files after global first-target exclusions (path
measures additionally require usable recorded paths). Set **First targets per
Fitts file** to **0** to include all recorded targets. Only the separate
matched-target table uses the matched subset. ID uses the actual target width;
IDe instead uses effective width derived from click spread. A large click spread
can therefore make IDe substantially lower than ID, without any targets being
removed. For participant A in the supplied recordings, all 36 targets give
ID **3.340 bits** and IDe **1.273 bits**; the pooled effective width is about
**274 pixels**, compared with target widths of 30/60 pixels.

Expand the collapsed **Fitts calculation review** and choose a **Matching
method**. The default **Strict planned ID (within 0.1 bits)** uses only
geometry, never click positions, hits/misses or movement times.
It starts with the participant having the fewest available targets
(participant label breaks ties), orders their trials by task type, width and
planned ID, and selects the nearest unused planned ID from each other
participant. Matches require identical task type and target width; the
largest minus smallest planned ID in each matched set must be at most
**0.1 bits**. Session names and trial order break nearest-ID ties.
Every participant contributes the same selected target count. This is a
deterministic nearest-match comparison, not globally optimal matching or
matching of directions/session composition. IDe is allowed to differ.

The additional **Closest target conditions (retain every test)** mode:

- Groups targets by **saved block type, amplitude setting and width**, after
  global target exclusions. Conditions absent from any participant are omitted.
- Retains the **maximum equal target count per shared condition**: the smallest
  available count among participants. It does not duplicate or rescale trials.
- Reserves a contribution from **every Fitts test**, reassigning reservations
  across shared conditions if needed. Remaining per-condition quotas are
  balanced across tests as evenly as availability allows (stable name order
  breaks ties). If a test cannot contribute, including when exclusions exhaust
  it, an explicit error is shown instead of silently dropping it.
- Within those quotas, uses the smallest participant pool per condition as
  reference (label breaks ties), sorts its targets by planned ID, and takes
  nearest unused IDs from the other participants. **There is no ID cutoff**.
  Selection never depends on click accuracy, hit/miss status or movement time.
  This deterministic greedy target selection prioritises counts and test
  retention, not the global minimum possible ID difference.
- Reports the method, shared-condition count, retained tests, **target counts
  by test**, and **largest planned-ID difference from reference targets**,
  alongside actual planned-ID averages/ranges and the existing Fitts metrics.

Removing targets does not reconstruct their original trajectories or change
their start-to-target distances. In the supplied six recordings, with no global
target exclusions, this mode retains **24 targets each**, contributing **12 from
each test**: A's 24-target test is reduced to 12, while its 12-target test is
fully retained; B/C keep all their targets. With N=2 per-file exclusions,
it retains **20 targets each**, 10 per test. Planned ID remains approximately
**3.42 bits for A versus 3.67 for B/C** at N=0; matching counts and settings
does not make the three-/six-target layouts geometrically identical.

The table reports available, selected and omitted targets and the mean/min/max
planned ID, followed by the same Fitts/path statistics as the existing table.
Its ordered field key documents each column. No match or fewer than two
participants produces an explicit notice, not an unrestricted fallback.
Participants without any available pointing trials prevent an all-participant
match. Selection is recalculated when settings or imported sessions change.

**Standards review:** The effective-width factor, sample SD, Shannon equation
and TP = IDe / mean MT follow the usual ISO-style formulation. However, the
legacy overall calculation pools different conditions and sessions, and Ae
is Euclidean start-to-click distance. The
[MacKenzie throughput reference](https://www.yorku.ca/mack/FittsLawSoftware/doc/Throughput.html)
specifies calculations per sequence of one A/W condition, task-axis projected
Ae, and an additional previous-endpoint adjustment for serial tasks. These
pooled results are therefore descriptive, not a fully standards-conformant
sequence-level throughput estimate. The legacy zero-We behaviour returns
zero IDe/TP; treat this as degenerate/unavailable, not zero performance.
Both tables retain the existing formulas so their values remain comparable;
this feature does not silently revise historical throughput calculations.

For Python callers, `planned_id(trial)` computes geometry-based ID and
`matched_fitts_rows(sessions, exclude_first_targets=0)` provides the comparison
rows without modifying sessions. Add `method="target_conditions"` for
closest-condition/count matching; the default `method="strict_id"` preserves
the original strict comparison.

The **Exclusions** section is at the top of the viewer, before playback.
One integer **First targets per Fitts file** setting (default zero) applies to
all participants and sessions. Each input has its label directly above it.
It drops the first N completed targets **of each Fitts file**, ordered by
`(start_time, trial_index)`. This deliberately differs from desktop's
per-condition `repetition_index` filter: N=2 removes two targets per test, not
two whole repetitions/conditions. Misses are targets too. If a file has N or
fewer targets it contributes none. The excluded targets' start-to-click
intervals, including both endpoints, are also omitted from head statistics
(initial pose, directional/task measures, movement ranges, speed and acceleration).
Paired files must have shared host timestamps; if these are unavailable, an
explicit error is shown instead of silently retaining those samples.

Exclusions apply to both summary tables/CSVs and the selected session's embedded
initial-pose and session-statistics panels. Changing a control refreshes playback
with the current settings. All original playback frames, targets and graph points
remain available to seek through. Excluded chart periods have grey background
bands behind the original coloured traces. Excluded targets remain grey,
not removed; raw pose/cursor readouts remain inspectable and identify exclusions.
Grey head-chart backgrounds mark either time exclusion or an excluded target interval.
The statistical scopes of the two time controls remain separate as described below.

The summaries are split into six tables, each with one row per participant
across all their sessions and its own CSV download:

Table columns size to their content rather than using equal fixed widths, with
bold headers. Every table (including range diagnostics) has a collapsed
**Field key** listing fields in exactly the same left-to-right order as the
table columns, using the same heading/key/description format as Fitts stats.
This includes session-level tables and range diagnostics. It explains every
field, its units, aggregation and, for Fitts/path
measures, its formula or count definition. **Source files**, calculation notes
and initial-position angle-reference diagrams are collapsed by default; expand
them when needed.

**Skip opening of initial-rest hold (s)** defaults to **1.0 seconds** in the
notebook's session section. Set it to 0 to use the whole hold. Initial-position
measurements use **only samples labelled `initial_rest.hold`**, never the
preceding `initial_rest.move` phase. The skip is timed from the saved hold-start
time, not the recording start; without a saved hold-start time, it starts at the
first labelled hold sample. Each separate initial-rest hold has its own opening
excluded. Changing this control immediately updates initial-rest averages,
spread (SD), usable sample count/duration, summary meshes and offsets measured
from rest. The applied duration is included as `initial_rest_exclude_s` in the
initial-position and combined summary CSVs.

This is an analysis exclusion, **not recalibration**: saved calibration heading,
playback poses, directional movement-exercise holds,
whole-recording Head movement stats, and pointing results stay unchanged.
If no samples remain, the session's rest-pose estimates and offsets from rest
are blank and an explicit notice names the session; saved calibration heading
is still shown. The preceding phase is never substituted. Participant rest-pose
averages use only sessions with available estimates.

**Skip opening of head recording (s)** independently defaults to **1.0 seconds**.
Both time controls are next to each other, to the right of the single
target-exclusion box (wrapping on narrow screens). The recording exclusion
starts at each head recording's first sample, using the same clock selection
as angular speed: provider timestamps, or host timestamps when a provider clock
resets/jumps and a complete host clock is available. Samples exactly at the
cutoff are retained. Set 0 to include the full recording.

This second control changes Head movement stats and its range checks, including
the embedded session-statistics panel:
relative ranges, angular speed and angular acceleration are recalculated from
retained samples, without intervals crossing the cutoff, excluded target
intervals or file boundaries.
The original reference pose and angle unwrapping are preserved. Calibration,
playback poses, initial-rest results, movement-exercise results and pointing results
stay unchanged. Original sample counts/durations in Session summary are also
unchanged. Head movement stats reports the retained sample count and exports
the applied setting as `head_opening_exclude_s`. If no samples remain, estimates
are blank and an explicit notice names the session; a single retained sample
has zero range but no speed or acceleration estimate.

Every main summary table has a separate **Individual sessions** section below it,
collapsed by default. Expand it to inspect all sessions in a single table with
participant and session labels, without filename columns. The opening-duration and target-exclusion
settings apply identically to participant and session rows. Single-session
values are recalculated from that session's own data, not a division of the
participant result. Session inspection rows do not add observations to the
participant CSV or change participant weighting.

For Python callers, `participant_rows(..., initial_rest_exclude_s=1.0)` and
`head_pretest_summary(..., initial_rest_exclude_s=1.0)` apply the same analysis
rule. These lower-level helpers default to 0 to preserve existing callers and
unmodified standalone playback summaries; the notebook controls default to 1
and explicitly pass their values into playback.
`participant_rows(..., head_opening_exclude_s=1.0)` and
`head_range_diagnostics(..., head_opening_exclude_s=1.0)` apply the recording
exclusion; these also default to 0 for existing Python callers.
`playback_payload(..., exclude_first_targets=N, initial_rest_exclude_s=1.0,
head_opening_exclude_s=1.0)` applies all three settings to the selected session.
`head_pretest_summary(..., exclude_first_targets=N)` excludes target intervals
from head pre-test measurements too.

1. **Session summary:** session/file counts, head sample count, summed head
   recording duration. Movement measures are in their own table.
2. **Initial head positions:** head pose in degrees during `initial_rest.hold`,
   after the opening exclusion, not the preceding move-to-rest phase, later
   session data or cursor/pixel measurements. Under the table, each
   participant has a wireframe. Three yaw columns show **recorded yaw** during
   initial rest, **yaw from calibration heading** during initial rest, and
   **calibration heading** (yaw of the average saved calibration pose).
   Recorded yaw is not necessarily a compass heading: gravity cannot determine
   yaw, and webcam pose is relative to the camera. Yaw from calibration heading
   removes only calibration yaw using a 3D rotation; pitch and roll retain the
   recorded tilt, without removing the full reference pose.
   **xIMU3 and AirPods pitch/roll are gravity-referenced tilt estimates** from
   AHRS/CoreMotion sensor fusion. Pitch zero means the aligned forward axis is
   horizontal; roll zero means no lateral tilt. Sensor mounting/fit and
   acceleration/fusion errors affect correspondence to anatomical head tilt.
   **Camera-based pitch/roll are camera-referenced**, with zero at the model's
   canonical level pose in that frame, not necessarily gravity-level:
   camera tilt changes the reference.
   xIMU3 yaw can be magnetically referenced when its magnetometer is enabled
   and reliable; AirPods yaw uses an arbitrary heading reference and can drift.
   Recorded yaw and calibration heading both use the tracker's heading zero;
   yaw from calibration heading is zero when facing the saved calibration heading.
   A head-mesh reference diagram below this table shows side-view pitch zero,
   front-view roll zero, and top-view recorded/calibration-heading yaw zero. The calibration
   heading in that diagram is illustrative, not a participant measurement.
   Calibration heading comes from the average of saved calibration sample
   quaternions, or the saved neutral quaternion when samples are unavailable,
   never an inferred first-second/rest reference. **Wireframes use calibration
   heading with average initial-rest pitch/roll**, not either initial yaw column.
   Without calibration, recorded yaw remains available, yaw from calibration
   heading and calibration heading are blank, and the wireframe reports the missing calibration pose.
   Initial-rest yaw uses circular means to handle the +/-180 degree boundary.
   Participant means average available session measurements, with yaw again
   averaged circularly. Calibration recorded during initial rest can naturally
   yield near-zero initial yaw.
   Participant plots have signed head axes (X forward, Y left, Z up) and a sign
   key: positive yaw is left, positive pitch is up, positive roll is left ear
   down; negative values are the opposite. Solid axes are positive and dashed
   axes negative. Plots are front-on: zero yaw, pitch and roll faces straight
   out of the screen, with the forward axis pointing at the viewer.
   Pitch and yaw values appear at the positive rotation-axis tips (Y and Z).
   Colours identify rotation angles, not anatomical X/Y/Z: X forward is the
   roll axis (blue), Y left the pitch axis (red), and Z up the yaw axis (green).
   **Zero yaw / Zero pitch / Zero roll** controls above the summaries set chosen
   plotted Euler components to 0.0 degrees for every participant, immediately
   redrawing the plots. Uncheck to restore the original angles. This is a
   display-only override, not recalibration: tables, CSV exports, source data
   and playback stay unchanged. Explicitly zeroing yaw permits a tilt-only
   plot when calibration heading is missing; wholly absent initial poses are
   never manufactured.
3. **Range of movement stats:** only `range_of_movement.*` task data. Columns
   containing `task_yaw_range`, `task_pitch_range` and `task_roll_range` measure
   the angle span across move and hold samples within each session, averaged
   across available sessions. Directional pose/extremes and between-direction
   ranges use the existing hold-phase statistics. Offsets reference initial rest.
   No Fitts, initial-rest or unlabelled samples contribute to ROM ranges.
   Without recorded ROM tasks, cells stay blank; whole-session ranges are never
   substituted.
   Only head measures are included, not cursor positions or pixel ranges.
4. **Head movement stats:** average/largest angular speed and acceleration
   across recordings after the recording-opening exclusion, plus relative yaw/pitch/roll ranges. These differ
   from the movement-exercise-only table. Speed and acceleration use changes
   between consecutive samples in each file, never across gaps between files.
   Speed measures the shortest 3D turn between poses; acceleration measures how
   quickly that speed changes. Relative angles remove the full reference pose
   using `inverse(reference) * recorded pose`, then extract yaw, pitch and roll.
   Movement range is the largest minus smallest angle after removing jumps at
   +/-180 degrees. **Relative pitch/roll ranges do not measure tilt from
   gravity**, even for IMUs: a tilted reference pose can mix heading changes
   into relative pitch/roll. Initial-position pitch/roll instead use recorded
   tilt. Playback angle graphs also show relative pose; the mesh can be toggled.
   Each participant's range is the **largest single-recording range** per angle,
   not a range spanning different reference poses or angle wraps in different
   files. Apart from the excluded opening, recordings include preparation,
   the movement exercise and unlabelled data, not just pointing.

   Expand **Movement range checks by recording** for analysed-recording and
   pointing-task-only (`fitts_test`) ranges, sample counts, largest relative
   pitch away from zero and a count of samples at `|relative pitch| >= 80 deg`.
   Near +/-90 degrees pitch, yaw and roll become difficult to separate, even
   when calculated from 3D rotations. A heading change can also change relative
   pitch/roll when the reference pose is tilted.
   No drift correction, clipping or outlier deletion is applied. Unlabelled
   recordings have no pointing-task-only estimate, so those cells are blank.
5. **Fitts targets and hits:** recorded/excluded/analyzed target counts, hits
   and error rate.
6. **Fitts stats:**
   pooled effective amplitude/width, index of difficulty, movement time,
   throughput, computed by the same `FittsStats.summary` as the
   desktop viewer, **not** an unweighted average of file summaries, and the seven
   path-accuracy measures averaged over usable retained paths.

Columns ending in `_session_mean` are averages of available session-level task
measurements. Exclusions change only the two Fitts tables, not the head tables.
Displayed numbers and numeric CSV fields use **one decimal place**, including
counts. Calculations and `viewer.summary_rows()` retain full precision; rounding
is for presentation/export only. Playback readouts also use one decimal place.

Tables use compact, wrapping headings and fixed-width layout rather than long
machine-readable field names. Hover over a heading for its full CSV field name.
Related columns have a fixed order independent of file import order: yaw,
pitch, roll; average before spread (SD)/largest value; left, right, up, down.
The compact movement-exercise table shows task angle ranges followed by directional averages
and offsets; the combined CSV retains additional detailed hold-phase measures.
Every table has a per-participant source-file footnote. Head task tables cite
only head files containing the relevant phase, Fitts tables cite Fitts files,
and session summaries cite both. An empty source list is stated explicitly.

Seconds, degrees, pixels and bits/s are indicated in column names. Error rate
is a fraction, not a percentage. Blank cells/CSV fields indicate unavailable
measurements, including pointing measures when no targets remain. A zero
effective width/throughput with insufficient endpoint variance follows the
existing desktop formula; it is not a claim of measurable throughput.
The expandable **selected session statistics** and **Initial pose and range of
movement** panels apply the same exclusions as that session's rows in the
summary tables. Participant statistics retain their existing aggregation.

Download the CSVs before the runtime ends. The combined **Download summary CSV
(all metrics)** and `viewer.summary_rows()` remain available, including the
maximum within-recording head ranges (distinct from task-only ROM).
Uploaded data/import assignments
exist only in this runtime; keep source files and manifests to reproduce an
analysis. Rerunning the viewer cell starts an empty collection. Clear notebook
outputs before sharing participant-sensitive notebooks.

## Local validation

From the repository root, install this folder's requirements into your Python
environment, then run:

```powershell
python -m pytest tests\test_colab_session_viewer.py
node --test colab_session_viewer\viewer.test.cjs
```

The Python suite includes notebook structure, imports, aggregation and native
playback parity checks. JavaScript tests exercise the browser playhead, trials,
gaps, pre-test transitions and path-accuracy calculations without Colab.
