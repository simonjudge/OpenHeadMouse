# Colab session viewer

Open [session_viewer.ipynb](session_viewer.ipynb) in Google Colab and run the
setup, loading and session-viewer cells. Use the notebook's **File > Open notebook > GitHub**
option after publishing this folder to a branch, or upload the notebook.
Set `REVISION` in the setup cell to that published branch/tag. For unpublished
work, upload the complete changed project to the runtime and set `PROJECT_DIR`
to its folder. This folder reuses the repository's pure-Python recording,
quaternion, mesh and statistics modules; it is not a standalone single-file
notebook.

When running locally, setup prefers the project containing the notebook over
an older nested checkout. The viewer cell reloads its modules and prints the
loaded source path. If a different checkout was already imported, restart the
kernel and rerun setup. This keeps the six separate summary tables in sync
with your local code rather than showing a previous version's combined table.

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

### Bundled demo data

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
participant exclusion settings; keep the source ZIP and set exclusions separately.
Example:

```csv
participant,session,head_file,fitts_file
P01,Test 1,P01/head1.json,P01/fitts1.json
P01,Test 2,P01/head2.csv,P01/fitts2.json
P02,Test 1,,P02/fitts1.json
```

Paths use `/` inside the ZIP; either file column may be blank. Files can share
names in different folders. Any root-level CSV name (for example `A manifest.csv`)
is checked for the manifest columns `participant,session,head_file,fitts_file`;
the name need not be `manifest.csv`. Only a CSV with those columns and valid
nonempty assignments is treated as a manifest. A recognized but invalid manifest
fails explicitly, rather than silently discovering/importing unlisted files.
If multiple root manifests are valid, keep only the one you intend to import.
Unrelated CSVs are not treated as manifests.
An existing root manifest is authoritative and is
imported directly: only its listed files are loaded, and its labels and pairings
are not replaced with suggestions.
A duplicate participant/session name or invalid manifest-referenced file
rejects that batch without partially adding it.
Imports append to the existing collection; earlier uploads are not replaced.
For files on mounted Drive, use `viewer.add_session(participant, session,
head_path=..., fitts_path=...)` instead.

## Exclusions and aggregation

Each participant has an integer **first N targets** setting (default zero).
It drops the first N completed targets **of each Fitts file**, ordered by
`(start_time, trial_index)`. This deliberately differs from desktop's
per-condition `repetition_index` filter: N=2 removes two targets per test, not
two whole repetitions/conditions. Misses are targets too. It never discards
an entire test as a unit or changes source data, playback, head statistics
or pre-test measures. If a file has N or fewer targets it contributes none.

The summaries are split into six tables, each with one row per participant
across all their sessions and its own CSV download:

Table columns size to their content rather than using equal fixed widths, with
bold headers. Every table (including range diagnostics) has a collapsed
**Field key** explaining every field, its units, aggregation and, for Fitts/path
measures, its formula or count definition. **Source files**, calculation notes
and initial-position angle-reference diagrams are collapsed by default; expand
them when needed.

1. **Session summary:** session/file counts, head sample count, summed head
   recording duration. Movement measures are in their own table.
2. **Initial head positions:** head pose in degrees during `initial_rest.hold`,
   not later session data or cursor/pixel measurements. Under the table, each
   participant has a wireframe below the table. Three explicit yaw columns show
   **absolute** initial-rest yaw (provider frame), **relative** initial-rest yaw
   (calibration-heading-relative), and **calibration mean yaw** (heading of the
   mean saved calibration quaternion). Absolute/provider yaw is not necessarily
   a compass heading: gravity cannot determine yaw, and webcam pose is
   camera-referenced. Only calibration yaw is removed for relative yaw using
   quaternion composition; pitch and roll
   retain provider-frame tilt, not a full neutral subtraction.
   **xIMU3 and AirPods pitch/roll are gravity-referenced tilt estimates** from
   AHRS/CoreMotion sensor fusion. Pitch zero means the aligned forward axis is
   horizontal; roll zero means no lateral tilt. Sensor mounting/fit and
   acceleration/fusion errors affect correspondence to anatomical head tilt.
   **Camera-based pitch/roll are camera-referenced**, with zero at the model's
   canonical level pose in that frame, not necessarily gravity-level:
   camera tilt changes the reference.
   xIMU3 yaw can be magnetically referenced when its magnetometer is enabled
   and reliable; AirPods yaw uses an arbitrary heading reference and can drift.
   Absolute yaw and calibration mean yaw both use the provider's heading zero;
   relative yaw is zero when facing the saved mean calibration heading.
   A head-mesh reference diagram below this table shows side-view pitch zero,
   front-view roll zero, and top-view provider/relative yaw zero. The calibration
   heading in that diagram is illustrative, not a participant measurement.
   Calibration mean yaw comes from the mean of saved calibration sample
   quaternions, or the saved neutral quaternion when samples are unavailable,
   never an inferred first-second/rest reference. **Wireframes use calibration
   mean yaw with initial-rest mean pitch/roll**, not either initial yaw column.
   Without calibration, absolute yaw remains available, relative/calibration yaw
   are blank, and the wireframe reports the missing calibrated pose.
   Initial-rest yaw uses circular means to handle the +/-180 degree boundary.
   Participant means average available session measurements, with yaw again
   averaged circularly. Calibration recorded during initial rest can naturally
   yield near-zero initial yaw.
   Participant plots have signed head axes (X forward, Y left, Z up) and a sign
   key: positive yaw is left, positive pitch is up, positive roll is left ear
   down; negative values are the opposite. Solid axes are positive and dashed
   axes negative. A fixed oblique viewing angle makes the three axes visible;
   this viewing angle is not added to the displayed measurements.
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
4. **Head movement stats:** whole-session angular speed/acceleration means/maxima
   and neutral-relative yaw/pitch/roll ranges, separate from the task-only ROM
   table. Derivatives are computed within each head file, never across gaps.
   Speed/acceleration are quaternion-geodesic measures. Axis ranges are
   **quaternion-derived Euler spans**: convert `inverse(neutral) * sample` into
   ZYX Euler angles, unwrap within each recording, then take max-minus-min.
   These neutral-relative pitch/roll components are **not gravity-referenced
   tilt ranges**, even for IMUs: a tilted neutral can couple heading changes
   into relative pitch/roll. This differs from the provider-frame tilt retained
   in the initial-position table. Playback also uses neutral-relative angles.
   The participant range is now the **maximum within-recording range** per axis,
   not an envelope of extrema from separate calibration frames/Euler wrap sheets.
   Full recordings include preparation, ROM and unlabelled data: they are not
   exclusively "range in use".

   Expand **Range diagnostics by recording** for full-recording and strictly
   `fitts_test`-labelled ranges, sample counts, maximum absolute relative pitch
   and a count of samples at `|relative pitch| >= 80 deg`. This flags proximity
   to the Euler singularity at 90 degrees; using quaternions as input does not
   make the extracted Euler angles gimbal-lock-proof. Axis values are coupled:
   a heading change can create relative pitch/roll when neutral is tilted.
   No drift correction, clipping or outlier deletion is applied. Unlabelled
   recordings have no defensible Fitts-only estimate, so those cells are blank.
5. **Fitts targets and hits:** recorded/excluded/analyzed target counts, hits
   and error rate.
6. **Fitts stats:**
   pooled effective amplitude/width, index of difficulty, movement time,
   throughput, computed by the same `FittsStats.summary` as the
   desktop viewer, **not** an unweighted average of file summaries, and the seven
   path-accuracy measures averaged over usable retained paths.

Columns ending in `_session_mean` are means of available session-level task
measurements. Exclusions change only the two Fitts tables, not the head tables.
Displayed numbers and numeric CSV fields use **one decimal place**, including
counts. Calculations and `viewer.summary_rows()` retain full precision; rounding
is for presentation/export only. Playback readouts also use one decimal place.

Tables use compact, wrapping headings and fixed-width layout rather than long
machine-readable field names. Hover over a heading for its full CSV field name.
Related columns have a fixed order independent of file import order: yaw,
pitch, roll; mean before standard deviation/max; left, right, up, down.
The compact ROM table shows task angle ranges followed by directional means
and offsets; the combined CSV retains additional detailed hold-phase measures.
Every table has a per-participant source-file footnote. Head task tables cite
only head files containing the relevant phase, Fitts tables cite Fitts files,
and session summaries cite both. An empty source list is stated explicitly.

Seconds, degrees, pixels and bits/s are indicated in column names. Error rate
is a fraction, not a percentage. Blank cells/CSV fields indicate unavailable
measurements, including pointing measures when no targets remain. A zero
effective width/throughput with insufficient endpoint variance follows the
existing desktop formula; it is not a claim of measurable throughput.
The expandable **selected session statistics** panel in playback is explicitly
unfiltered; the participant table/CSV is the exclusion-adjusted analysis.

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
