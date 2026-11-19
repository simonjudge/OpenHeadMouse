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
| MTnodwell, adjusted throughput, validity counts | [selection_stats.py](selection_stats.py): `alternative_selection_rows` calculates the separate no-dwell table, using [dwell.py](dwell.py) `selection_settings` to validate metadata. The duration comes from **each participant/session's imported `dwell_time_s`**, supplied in its manifest row or manual import controls, alongside `selection_method`. For each dwell target, subtract that session's supplied duration from its recorded MT **before** filtering negative results and averaging. Recalculate IDe on valid adjusted targets for adjusted TP; do not subtract a duration from an aggregated mean. No fixed duration is taken from the code or inferred from inside-target residence. |
| First-entry selection | [selection_stats.py](selection_stats.py): `first_entry_trial` and `alternative_selection_rows` estimate contact and calculate first-entry movement time, nominal-width ID and throughput. |
| Dwell residence, settling, cursor/head movement and ranges | [dwell.py](dwell.py): `target_intervals` solves line-segment/circle intersections; `_window_points` interpolates visit endpoints; `_trial_dwell` calculates per-target estimates; `selection_summary` averages available estimates and reports denominators. |
| Excluded targets and their head intervals | [data.py](data.py): `retained_trials` removes the first N targets **per file**; `_target_sample_mask` removes their inclusive host-clock intervals from head estimates. |
| Recording-opening and hold-opening exclusions | [data.py](data.py): `_head_movement_mask`, `_holds_after_opening`, `_hold_sample_mask`, `head_pretest_summary`. Hold opening applies to rest and all four directional holds; neither control modifies calibration/reference data. |
| Head speed, acceleration, sampling rate and raw Euler ranges | [core/session_stats.py](../core/session_stats.py): `SessionStats.times`, `_usable_intervals`, `angular_speed_series`, `angular_acceleration_series`, `sampling_stats`, `cervical_rom` and summary helpers. Speed is geodesic quaternion angle/dt; acceleration is absolute change in scalar speed/dt. |
| Exclusion-safe kinematics and relative movement ranges | [data.py](data.py): `_movement_series` splits retained runs before differentiating; `head_range_diagnostics` unwraps reference-relative Euler angles and measures max-min. `participant_rows` pools usable dynamics values and takes the largest within-recording range per axis. |
| Initial-rest pose, directional held poses and offsets | [session_viewer/pretest_stats.py](../session_viewer/pretest_stats.py): `_mean`, `_std`, `_pose_stats`, `head_initial_rest_stats`, `head_range_of_movement_stats`, `summarise_pretest`. [data.py](data.py) `head_pretest_summary` adds exclusions/calibration-relative yaw and `_circular_mean_deg`; `participant_rows` aggregates sessions and separately calculates full movement-exercise max-min ranges. |
| Cursor initial-rest steadiness and exercise extremes | [session_viewer/pretest_stats.py](../session_viewer/pretest_stats.py): `cursor_initial_rest_stats`, `cursor_range_of_movement_stats`. These shared desktop pre-test measures are not included in the notebook's head-only pre-test panel. |
| Saved calibration and playback reference | [core/recorder.py](../core/recorder.py): `Recorder.set_calibration` stores supplied neutral and relative-pose spread. [data.py](data.py): `calibration_mean_yaw`. [mesh_visualizer.py](../session_viewer/mesh_visualizer.py): `calculate_neutral_quaternion` chooses recenter/calibration/rest/startup reference, in that order. |
| Quaternion angles, distances and held-pose mean | [core/quaternion_math.py](../core/quaternion_math.py): `relative_rotation`, `to_euler_zyx`, `angle_between`, `mean_quaternion`. Scalar-first quaternions `[w,x,y,z]`; internal angles radians, displayed angles degrees. |
| Target outlier review | [outliers.py](outliers.py): `review_targets`. Per-participant sample-SD checks on retained movement time, distance and task-axis endpoint error; review checkboxes apply to all Fitts tables, never playback/head statistics. |
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
  notebook target-review/dwell controls.

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

Below the Fitts tables, **Cumulative cursor paths** shows one image per
participant, overlaying saved paths, target circles and click dots across all
their Fitts sessions. Targets are numbered **1–N continuously across all of the
participant's Fitts files**: files are ordered by recording time (import order
if unavailable) and targets keep their original order, so two 12-target tests
give targets 1–24, with the second test as 13–24. Type the targets to show in
the participant's **Targets** box as ranges and/or lists, e.g. `1-12`,
`1,2,24` or `3-5, 9`; the default is every target. **▲ Next** and **▼ Previous**
step through one target path at a time (skipping excluded targets) and **All**
restores the full set. The existing first-target exclusion applies to each
file without renumbering; changing the selection affects the image only. An
invalid entry shows an inline message for that participant only. Paths and
targets cycle through ten colours; expand the legend for target number, session,
target number within its file and source filename. The click is
appended to the saved trajectory as in the path statistics; a trial without
saved path samples shows its click only, not a fabricated path.

Different screen sizes, and estimated versus recorded dimensions, are shown in
separate proportional screen rectangles. If screen dimensions are missing, the
same estimate as playback is used and marked as estimated. Click **Download image
(SVG)** in the compact footer below the plot, on the same line as the screen
dimensions, to save the participant's current plot as a scalable image; it contains
the selected paths and participant label. Treat downloaded images as participant
data. Plot range settings do not change statistics or playback.

The loading cell displays only import controls, ZIP review and manifest downloads.
The separate session-viewer cell displays the single head/cursor viewer and summary
tables. Both share the same `viewer` instance; loading more data updates the viewer
without rerunning its cell, and rerunning either display cell keeps the collection.
The viewer cell disables nested output scrolling where the notebook frontend
honours that setting. For full-screen output in Colab, run and select the viewer
cell, then choose **View output fullscreen** in the **Command palette**. In
JupyterLab, right-click the output and choose **Create New View for Output** to
open it in a separate tab. The notebook cannot reliably enter browser full screen
automatically.
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

**No-Dwell stats** is always shown below the main Fitts table, with its own
CSV and a single collapsed **Table Notes** section containing its field key,
individual-session table (including filenames) and a short analysis-scope note. It
has exactly the same columns and order as **Fitts stats**, recalculated on
adjusted movement times: its MT column is MTnodwell and its throughput column is
no-dwell throughput. The main Fitts table retains recorded movement times.
All three Fitts tables include error rate: a one-decimal percentage on screen,
followed by **(misses/contributing targets)**, e.g. **8.3% (2/24)**, and an
unrounded fraction in CSV exports. Contributing target and miss totals are
integer columns in these tables, including the individual-session breakdowns.

In **Fitts calculation review**, enable **Show dwell behaviour estimates** to add
the counts, residence, settling and movement estimates in a separate **Dwell
behaviour stats** table with individual sessions under **Table Notes** and its own
CSV. It uses the same first-target and review exclusions as the main Fitts table.
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
  not infinity. The **Contributing targets and omissions** list under **Table Notes** shows
  the available, contributing, unknown-method and too-short counts.
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
First-target exclusions apply first, followed by individual review exclusions.
The existing recording-opening
and initial-rest exclusions keep their existing scopes and do not redefine
these target-based dwell windows.

## Exclusions and aggregation

### First-entry selection stats

**First-entry selection stats** follows the No-Dwell stats table and uses the
same layout and column order as Fitts stats. It recalculates performance as if
each target had been selected the moment the cursor first touched it, as in
Ching, MacKenzie and Allison (2025), *Heads Will Roll: Alternative Head Pointing
for a Head-Mounted Display*, SUI '25,
[doi:10.1145/3694907.3765916](https://dl.acm.org/doi/10.1145/3694907.3765916).

- First contact is found by linear interpolation between saved cursor samples
  (with the saved start at time 0 and the click at selection), treating the
  cursor as a point against the circular target of radius width/2. The saved
  start is planned (usually the preceding target centre, but sometimes the
  screen centre), not necessarily the actual cursor position, so it can affect
  the first interpolated segment and
  start-to-contact distance. Sparse samples can miss brief visits. Contact
  includes tangency and starting inside the target.
- **MT** is the time from the trial's original start to first contact. The
  contact point replaces the click for amplitude, and path measures use the path
  up to that point.
- As in the paper, throughput uses nominal target width **W**, not click-spread
  **We**, because first-entry endpoints generally lie on the target edge. Each trial's
  **ID = log2(Aentry/W + 1)**, where Aentry is the start-to-contact distance;
  throughput is mean ID divided by mean first-entry MT. The column positions of
  effective width and IDe hold nominal width and first-entry ID.
- Trials that never touch the target, have no recorded path or use typing
  geometry are omitted and counted in **Contributing targets and omissions**.
  A trial that touched the target counts as a first-entry hit even if its click
  later missed.
- Each trial keeps its original start; this is a pooled reconstruction, not
  the paper's live sequence in which the next trial starts at the first-entry
  point.

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

Under **Fitts stats → Table Notes**, **Fitts test conditions by file**
provides a table with a CSV and ordered field key. Each row describes a saved
participant/Fitts file, using only its Fitts trials: lists of amplitude settings,
widths and block types, observed distinct target positions per condition/repetition,
observed repetitions per condition and recorded/analysed trial totals.
Targets per repetition and repetitions per condition are different settings.
The inventory uses all recorded trials; only the analysed-count column applies
first-target and review exclusions.

These recordings do not save the complete Fitts settings panel. Counts are
therefore labelled **observed**: incomplete runs, repeated positions or missing
repetitions cannot establish the intended target/repetition setting. For the
circle generator, saved amplitude is a configured radius, capped by screen fit;
it must not be confused with the start-to-target movement distance used for ID.

**Path TRE** is the average count of re-entries per usable path, not the number
of initial target entries or a percentage. The first entry is not counted;
paths with no re-entry contribute zero, so an average below 1 is expected.
The path-trial-count column is its denominator.

### Target review

The matched-target section has been removed. At the top of **Fitts results**,
**Target review** lists trials more than **3 sample standard deviations**
from their participant's average on any of:

- Recorded movement time (seconds).
- Euclidean start-to-click movement distance (pixels).
- Signed task-axis endpoint error (pixels), an additional accuracy check that
  helps identify gross misclicks when target distances vary.

The baseline pools that participant's Fitts files after first-N exclusions.
It is not condition-specific or iterative: review removals never change the
baseline. Zero-SD measures and pools with fewer than two trials cannot flag
targets. Each candidate shows participant-wide target number (matching the cursor
plot), file/session, within-file target number, measurement, mean, SD and z-score.

All **Include** checkboxes start checked. Untick individual candidates to remove
them from main Fitts, no-dwell and first-entry results, including per-session
tables, counts, optional dwell behaviour, condition analysed counts and CSVs.
Decisions persist within this viewer instance across settings changes; **Include
all reviewed targets** restores them. First-N-excluded targets cannot be
reintroduced by a review checkbox. Raw trials, playback, cursor-path plots and
head statistics are unchanged by review decisions.

[Soukoreff & MacKenzie (2004), footnote 8](https://www.yorku.ca/mack/ijhcs2004.html)
recommends investigating unusually large deviations in movement time/distance,
not automatically deleting misses. Endpoint error is an additional review
criterion, not a threshold attributed to that paper. Inspect candidates before
removal: a statistical flag alone does not establish an accidental selection.

The main Fitts table uses all remaining targets across each participant's files.
ID uses actual target geometry; IDe instead uses effective width derived from
click spread. Removing a target does not reconstruct any subsequent movement
or change its saved start-to-target distance.

Error rates are misses divided by contributing targets. First-entry error rate
is contact-based and therefore zero for its contributing subset: no-contact or
unestimable trials are reported separately under **Contributing targets and
omissions**, not treated as successful selections.

The **Fitts targets and hits** table shows distinct **Target widths (px)** and
**Target amplitudes (px)** immediately before **Planned ID min / max (bits)**.
These values come from the analysed Fitts trials after first-target and review
exclusions; multiple distinct settings are comma-separated. Width is the target
diameter for circular targets. Amplitude is the value saved in the trial file;
for circle tasks this is the configured radius (subject to screen fitting), not
necessarily the actual movement distance. The ID column gives the minimum and
maximum geometry-based difficulty, not effective ID; no remaining targets means
the measures are unavailable.

In playback, **missed target intervals have pink backgrounds** on both cursor
speed and distance-to-target graphs. The target circle is **red for a recorded
miss**, blue for a recorded hit (grey for excluded hits). Misses remain pink/red
even when excluded; raw outcomes are visible throughout their intervals, not
only after clicking. Head-graph exclusion shading is unchanged. Current planned
target ID in bits appears in the live cursor readout and **Selected session
statistics**, updating when seeking or playing. This is target difficulty
`log2(D/W + 1)`, not a target sequence number or an effective ID.

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
The tables retain the existing formulas so their values remain comparable;
this feature does not silently revise historical throughput calculations.

For Python callers, `review_targets(sessions, exclude)` returns candidates;
`participant_rows` and `alternative_selection_rows` accept `excluded_trial_ids`
to omit reviewed trials without modifying sessions.

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
**Table Notes** section with a readable two-column **Field key** table: the field
label and wrapping CSV key on the left, its meaning, units and calculation on the
right. Rows are top-aligned, padded and alternately shaded. Definitions follow
the result column order and apply to both participant and individual-session
tables; they are not repeated below the session table.
Hover over any data-table heading to see its CSV field name and a concise
description of what it measures.

Calculation details, including no-dwell adjustments and first-entry interpolation
limitations, sit with the relevant field. Only shared analysis scope and method
references remain separate. **Individual sessions**, contributing-target omissions
and initial-position angle references remain within **Table Notes**. Coverage is
an aligned participant-by-count table, hiding omission categories that are zero
for every participant. There is no separate
**Source files** section; filenames are listed with individual sessions.

**Each hold opening (s)** defaults to **1.0 seconds** in the
notebook's session section. Set it to 0 to use each whole hold. Initial-position
measurements use **only samples labelled `initial_rest.hold`**, never the
preceding `initial_rest.move` phase. The skip is timed from the saved hold-start
time, not the recording start; without a saved hold-start time, it starts at the
first labelled hold sample. Each separate initial-rest or left/right/up/down hold has its own opening
excluded. Changing this control immediately updates initial-rest averages,
spread (SD), usable sample count/duration, summary meshes and offsets measured
from rest, directional means/SDs, exercise ranges/SDs and recording movement
statistics/diagnostics. Move phases remain subject to their normal scopes and
are not trimmed by this control. The applied duration keeps the legacy key `initial_rest_exclude_s` in the
initial-position and combined summary CSVs.

This is an analysis exclusion, **not recalibration**: saved calibration heading,
playback poses, the original reference pose and pointing results stay unchanged.
Raw excluded samples remain visible with grey backgrounds on head charts.
Speed/acceleration calculations split at exclusion gaps rather than bridging them.
Fully excluded directional holds have blank means/SDs/offsets; their preceding
move samples are never substituted for held-pose measurements.
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

Every main summary table has **Individual sessions** within its collapsed
**Table Notes**. Expand the notes to inspect all sessions in a single table with
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
   Each axis also shows **exercise SD**, the population SD of unwrapped recorded
   angles across move and hold samples. Each directional average is followed by
   its **hold SD**, the population SD during that hold only. SD uses divisor n;
   participant values average available session SDs equally, rather than pooling
   samples or calculating the SD of session ranges. Exercise SD describes the
   exercise's spread, whereas hold SD describes steadiness; neither is an
   uncertainty estimate for the movement range. Skipped target intervals and
   each directional hold's opening are removed; the recording-opening control
   does not trim this exercise.
   No Fitts, initial-rest or unlabelled samples contribute to ROM ranges.
   Without recorded ROM tasks, cells stay blank; whole-session ranges are never
   substituted.
   Only head measures are included, not cursor positions or pixel ranges.
4. **Head movement stats:** average/largest angular speed and acceleration
   across recordings after recording-opening, hold-opening and target-interval
   exclusions, plus relative yaw/pitch/roll ranges. These differ
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
   files. Apart from excluded intervals, recordings include preparation,
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
measurements. Individual review exclusions change Fitts/dwell results only;
first-target exclusions also remove associated time intervals from head statistics.
Displayed measurements and numeric CSV fields use **one decimal place**.
Target totals, hits, misses, excluded-target counts and target-coverage counts
are **integers**, not one-decimal numbers. CSV `error_rate` fractions retain precision to avoid
rounding small nonzero error rates to zero. On screen, error rates are one-decimal
percentages. Calculations and `viewer.summary_rows()` retain full precision; rounding
is for presentation/export only. Playback readouts also use one decimal place.

Tables use compact, wrapping headings and fixed-width layout rather than long
machine-readable field names. Hover over a heading for its full CSV field name.
Related columns have a fixed order independent of file import order: yaw,
pitch, roll, with **all measures for each axis together**. Initial-pose averages
sit beside their SDs. Movement-exercise columns group yaw range/SD and left/right
hold average/SD/offset first, then pitch range/SD and up/down holds, then roll
range/SD. Main tables, individual-session tables, diagnostics, field keys and
their CSV columns use the same axis order.
The compact movement-exercise table shows task angle ranges followed by directional averages
and offsets; the combined CSV retains additional detailed hold-phase measures.
Source filenames are listed alongside participant/session labels under
**Table Notes → Individual sessions**, rather than in a duplicate source-file list.
If source filenames are unavailable this is stated explicitly.

Each available initial head-pose plot has a **download svg** link immediately
beside its participant heading, e.g. **A - (download svg)**. It saves the displayed
mesh and axes, respecting the current display-only yaw/pitch/roll zero settings.
Unavailable poses have no download link. Saved images contain participant labels;
treat them as participant data.

Seconds, degrees, pixels and bits/s are indicated in column names. Error rate
is a percentage on screen and a fraction in CSVs. Blank cells/CSV fields indicate unavailable
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
