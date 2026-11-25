# Colab session viewer

The notebook [session_viewer.ipynb](session_viewer.ipynb) runs locally or in
Google Colab. This folder reuses the repository's pure-Python recording,
quaternion, mesh and statistics modules; it is not a standalone single-file
notebook, so a complete project checkout must be available in either case.

For the head and Fitts file structures, see the
[recording format reference](../docs/recording-formats.md); the
[ZIP assignment manifest](#zip-assignment-manifests) is documented below.

## Where to inspect the calculations

See the [Colab analysis guide](../docs/colab-analysis.md#where-to-inspect-the-calculations)
for calculation sources, formulas and how to interpret the results.

### Reading the results correctly

See [reading the results correctly](../docs/colab-analysis.md#reading-the-results-correctly).

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

Selected paths, including single-target stepping, have translucent **O (teal)**
and **E (brown)** highlights over their estimated on-screen/off-screen clutch
segments. These reuse playback/Table 10 intervals and the current clutch
settings, including off-screen precedence; changing settings refreshes the
highlights without changing the path selection. Hover for trial-relative times.
Highlights do not bridge excessive cursor gaps or duplicate timestamps.
Missing evidence is not treated as proof of no clutch. See the
[labelled synthetic trajectory examples](../docs/colab-analysis.md#annotated-synthetic-cursor-trajectories).

Different screen sizes, and estimated versus recorded dimensions, are shown in
separate proportional screen rectangles. If screen dimensions are missing, the
same estimate as playback is used and marked as estimated. Click **Download image
(SVG)** in the compact footer below the plot, on the same line as the screen
dimensions, to save the participant's current plot as a scalable image; it contains
the selected paths, clutch highlights/key and participant label. Treat downloaded images as participant
data. Plot range settings do not change statistics or playback.

The loading cell displays only import controls, ZIP review and manifest downloads.
The separate session-viewer cell displays the single head/cursor viewer and summary
tables. Both share the same `viewer` instance; loading more data updates the viewer
without rerunning its cell, and rerunning either display cell keeps the collection.
Table numbers stay fixed when settings change. Table 7 is reserved for optional
dwell behaviour results; tables inside **Table Notes** use suffixes such as **Table 6a**.
Expand **Clutch estimate settings** above the Fitts tables to adjust three
off-screen settings (edge zone, plateau/cursor return, head rotation/reversal),
three on-screen settings (retreat/reversal, speed ratio, direction opposition),
and two sample-gap limits. The head-gap limit also permits a shortfall at the
recording end, holding the last angle constant. Participant and
individual-session Clutch stats, CSVs and playback update immediately; playback
restarts when settings change. Current settings
are included in the combined summary CSV, not Table 10 or its dedicated CSV.
Table 10 follows Table 9 and starts with **Total Clutches / target**: the proportion
of eligible targets with either type, counting each target once, plus raw clutched-
target and eligible-target counts. A detected clutch establishes a positive;
a zero requires both detectors to be available. Reset restores the defaults. The edge zone
also affects trajectory-based boundary estimates in older recordings, but not
saved boundary-event counts.
O/E marks share one row below the distance graph to identify on-screen/edge intervals independently of
miss backgrounds. Trial controls beside the session selector seek to a trial's
start; **Zoom to trial** restricts every timeline (including head acceleration)
and the slider to that trial. Navigation rebuilds playback at the trial start
without changing statistics. Playback includes excluded trials.
The on-screen estimate now requires a backward reset and faster recovery along
the initial approach axis, excluding ordinary throw/overshoot corrections.
See [the calculation and limitations](../docs/colab-analysis.md#estimated-headmouse-clutches).
The viewer cell disables nested output scrolling where the notebook frontend
honours that setting. For full-screen output in Colab, run and select the viewer
cell, then choose **View output fullscreen** in the **Command palette**. In
JupyterLab, right-click the output and choose **Create New View for Output** to
open it in a separate tab. The notebook cannot reliably enter browser full screen
automatically.
Restart the kernel to pick up code changes to an existing viewer instance.
If an older run left duplicate playback frames, clear the notebook outputs,
then restart the kernel and rerun the setup, loading and session-viewer cells.
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
The neutral/reference pose diagrams include an angled X/Y/Z wireframe so the
forward X arrow is visible. The rotation is for display only, and the reference
SVG download includes all four views.

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
After the first successful import, **Download manifest.csv for individual recordings**
appears below these controls and updates with every individually added session
(including `viewer.add_session(...)`). It preserves participant/session labels,
original filenames, selection methods and dwell durations. To reuse it, put it at
the root of a ZIP alongside the original recordings, with unique filenames.
ZIP imports keep their own separate manifest downloads.
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

### ZIP assignment manifests

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
names in different folders when their full relative paths are specified.
Filename-only entries are also searched recursively through subfolders: an exact
root-level match takes priority, otherwise there must be exactly one matching
filename. Ambiguous matches are rejected with the matching paths listed, rather
than guessing which recording to use. Downloaded ZIP manifests contain the resolved
relative paths. Any root-level CSV name (for example `A manifest.csv`)
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

See [dwell timing and movement estimates](../docs/colab-analysis.md#dwell-timing-and-movement-estimates).

## Exclusions and aggregation

The [analysis guide](../docs/colab-analysis.md#exclusions-and-aggregation)
explains exclusions, aggregation and summary tables.

### First-entry selection stats

See [first-entry selection stats](../docs/colab-analysis.md#first-entry-selection-stats).

### Planned-ID comparison and calculation review

See [planned ID and calculation review](../docs/colab-analysis.md#planned-id-comparison-and-calculation-review).

### Target review

See [target review](../docs/colab-analysis.md#target-review).

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
