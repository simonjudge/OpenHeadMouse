# Colab session viewer

Open [session_viewer.ipynb](session_viewer.ipynb) in Google Colab and run the
setup and viewer cells. Use the notebook's **File > Open notebook > GitHub**
option after publishing this folder to a branch, or upload the notebook.
Set `REVISION` in the setup cell to that published branch/tag. For unpublished
work, upload the complete changed project to the runtime and set `PROJECT_DIR`
to its folder. This folder reuses the repository's pure-Python recording,
quaternion, mesh and statistics modules; it is not a standalone single-file
notebook.

Only the dependencies in [requirements.txt](requirements.txt) are needed.
Do not install the desktop requirements: no Qt, tracker, camera, mouse control,
OpenGL, server, GPU or Colab tunnel is used. Setup downloads the public MediaPipe
canonical face mesh over verified HTTPS when it is not already present.
Recordings stay in the notebook/runtime/browser and are not sent to a playback
service. Colab itself receives files you upload; follow your study's data policy.

## Import and playback

The output contains one head/cursor viewer at the top. Select its participant
and session with the two dropdowns. Play/pause, rewind, seek, playback speed,
graph zoom, head-camera rotation/zoom, cursor trails, target/click markers,
angular/cursor plots and live values use the desktop viewer's recording
semantics. Browser canvas rendering replaces the desktop's Qt/OpenGL widgets;
it is not a pixel-identical native UI. No data is resampled or time-stretched.

For each session, enter participant/session names, upload a head JSON/CSV, a
Fitts JSON or a matching pair, then click **Add session**. Repeat for any number
of participants/tests. Head and Fitts files from the same test form one session.
Pairs require shared host timestamps and at least two head samples inside the
test's time range, just like desktop synchronized playback. Legacy files can
be viewed separately, not artificially synchronized. Head-only exercises
recorded in Fitts files and their pre-test cursor trajectories are supported.

You can also import multiple ZIP data sets. Each ZIP must have a root
`manifest.csv` specifying the files and participant assignments explicitly:

```csv
participant,session,head_file,fitts_file
P01,Test 1,P01/head1.json,P01/fitts1.json
P01,Test 2,P01/head2.csv,P01/fitts2.json
P02,Test 1,,P02/fitts1.json
```

Paths use `/` inside the ZIP; either file column may be blank. Files can share
names in different folders. There is no filename-based participant inference
or guessing which recordings belong together. A duplicate participant/session
name or invalid file rejects the import without partially adding that batch.
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

The summary table always has one row per participant, across all their sessions:

- Counts of sessions/files, recorded/excluded/analyzed targets, and hits.
- Pooled effective amplitude/width, index of difficulty, movement time,
  throughput and error rate, computed by the same `FittsStats.summary` as the
  desktop viewer, **not** an unweighted average of file summaries.
- The same seven path-accuracy measures, averaged over usable retained paths.
- Head sample count, summed recording duration, angular speed/acceleration
  means/maxima and neutral-relative angular ranges. Derivatives are computed
  within each head file, never across gaps between recordings. Head-only
  recordings also contribute.
- Initial-rest and range-of-movement metrics when available. Columns ending in
  `_session_mean` are means of available session-level pre-test measurements.

Seconds, degrees, pixels and bits/s are indicated in column names. Error rate
is a fraction, not a percentage. Blank cells/CSV fields indicate unavailable
measurements, including pointing measures when no targets remain. A zero
effective width/throughput with insufficient endpoint variance follows the
existing desktop formula; it is not a claim of measurable throughput.
The expandable **selected session statistics** panel in playback is explicitly
unfiltered; the participant table/CSV is the exclusion-adjusted analysis.

Download the summary CSV before the runtime ends. `viewer.summary_rows()` also
returns the table as Python dictionaries. Uploaded data/import assignments
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
