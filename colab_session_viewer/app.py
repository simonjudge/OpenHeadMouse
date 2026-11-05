"""ipywidgets shell: a single browser viewer, repeated/bulk imports and summaries."""

from __future__ import annotations

import base64
import csv
import html
import tempfile
import zipfile
from pathlib import Path

import ipywidgets as widgets
from IPython.display import HTML, IFrame, clear_output, display

from colab_session_viewer.data import (
    Session, format_number, initial_pose_reference_diagram, initial_pose_wireframes, participant_rows, participant_tables,
    payload_json, playback_payload, summary_csv, column_label, table_sources, head_range_diagnostics,
    field_description, matched_fitts_rows, fitts_condition_rows,
)
from colab_session_viewer.archive import ArchiveDataset, Assignment, load_archive, manifest_csv
from colab_session_viewer.dwell import (
    DWELL_BEHAVIOUR_FIELDS, NODWELL_EQUIVALENTS, NODWELL_FITTS_FIELDS, SELECTION_METHODS,
)

_FOLDER = Path(__file__).parent


def viewer_document(payload: dict) -> str:
    template = (_FOLDER / "viewer.html").read_text(encoding="utf-8")
    script = (_FOLDER / "viewer.js").read_text(encoding="utf-8")
    return template.replace("__VIEWER_JS__", script).replace("__PAYLOAD__", payload_json(payload))


def _uploads(upload: widgets.FileUpload) -> list[tuple[str, bytes]]:
    # Colab and local Jupyter may provide ipywidgets 7 or 8.
    value = upload.value
    if isinstance(value, dict):
        return [(name, bytes(item["content"])) for name, item in value.items()]
    return [(item["name"], bytes(item["content"])) for item in value]


def _write_upload(directory: Path, name: str, content: bytes, prefix: str) -> Path:
    suffix = Path(name).suffix.lower()
    if suffix not in (".json", ".csv"):
        raise ValueError(f"Unsupported recording format: {name}")
    path = directory / f"{prefix}{suffix}"
    path.write_bytes(content)
    return path


def _selection_controls(method="unknown", duration=None):
    selection = widgets.Dropdown(
        options=SELECTION_METHODS, value=method, description="Selection method:",
        style={"description_width": "initial"},
    )
    dwell = widgets.FloatText(
        value=duration or 0, description="Dwell time (s):", disabled=method != "dwell",
        style={"description_width": "initial"},
    )
    selection.observe(lambda change: setattr(dwell, "disabled", change["new"] != "dwell"), names="value")
    return selection, dwell


class NotebookViewer:
    def __init__(self, *, demo_directory: str | None = None, demo_mode: bool = False):
        self.demo_mode = demo_mode
        self.sessions: list[Session] = []
        self._changing = False
        self.participant = widgets.Dropdown(description="Participant:")
        self.session = widgets.Dropdown(description="Session:")
        self.viewer = widgets.Output(layout=widgets.Layout(width="100%"))
        self.message = widgets.HTML()
        self.load_demo = widgets.Checkbox(value=False, description="Load Demo Data", indent=False)
        self.demo_directory = widgets.Text(
            value=str(_FOLDER / "demodata") if demo_directory is None else demo_directory,
            description="Demo folder:", layout=widgets.Layout(width="95%"),
        )
        self._loaded_demo_archives: set[Path] = set()
        self.load_demo.observe(self._load_demo_changed, names="value")
        self.table = widgets.HTML()
        self.match_planned_id = widgets.Checkbox(
            value=False, description="Show matched-target comparison", indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.match_planned_id.observe(self._pose_zero_changed, names="value")
        self.fitts_matching_method = widgets.Dropdown(
            options=[
                ("Strict planned ID (within 0.1 bits)", "strict_id"),
                ("Closest target conditions (retain every test)", "target_conditions"),
            ],
            value="strict_id", description="Matching method:",
            style={"description_width": "initial"}, layout=widgets.Layout(width="auto"),
        )
        self.fitts_matching_method.observe(self._pose_zero_changed, names="value")
        self.matched_fitts_table = widgets.HTML()
        self.include_dwell = widgets.Checkbox(
            value=False, description="Add MTnodwell, no-dwell throughput and dwell behaviour",
            indent=False, layout=widgets.Layout(width="auto"),
        )
        self.include_dwell.observe(self._pose_zero_changed, names="value")
        self.pose_zero_controls = {
            axis: widgets.Checkbox(value=False, description=f"Zero {axis}", indent=False,
                                   layout=widgets.Layout(width="120px"))
            for axis in ("yaw", "pitch", "roll")
        }
        for control in self.pose_zero_controls.values():
            control.observe(self._pose_zero_changed, names="value")
        self.initial_rest_exclude = widgets.BoundedFloatText(
            value=1.0, min=0.0, max=1e6, step=0.1,
            description="Skip opening of initial-rest hold (s):",
            style={"description_width": "initial"}, layout=widgets.Layout(width="400px"),
        )
        self.initial_rest_exclude.observe(self._initial_rest_exclusion_changed, names="value")
        self.head_opening_exclude = widgets.BoundedFloatText(
            value=1.0, min=0.0, max=1e6, step=0.1,
            description="Skip opening of head recording (s):",
            style={"description_width": "initial"}, layout=widgets.Layout(width="400px"),
        )
        self.head_opening_exclude.observe(self._initial_rest_exclusion_changed, names="value")
        self.rest_exclusion_note = widgets.HTML()
        self.head_exclusion_note = widgets.HTML()
        self.exclude_targets = widgets.BoundedIntText(
            value=0, min=0, max=2**31 - 1,
            description="First targets per Fitts file:",
        )
        self.exclude_targets.observe(self._initial_rest_exclusion_changed, names="value")
        for control in (self.exclude_targets, self.initial_rest_exclude, self.head_opening_exclude):
            control.description = control.description.rstrip(":")
            control.style.description_width = "0px"
            control.layout = widgets.Layout(width="150px")

        def labelled(control, label):
            return widgets.VBox(
                [widgets.Label(label), control], layout=widgets.Layout(margin="0 20px 0 0"),
            )

        self.exclusion_controls = labelled(self.exclude_targets, "First targets per Fitts file")
        self.time_exclusion_controls = widgets.HBox(
            [
                labelled(self.initial_rest_exclude, "Initial-rest hold opening (s)"),
                labelled(self.head_opening_exclude, "Head recording opening (s)"),
            ],
            layout=widgets.Layout(flex_flow="row wrap"),
        )
        self.analysis_exclusions = widgets.HBox(
            [self.exclusion_controls, self.time_exclusion_controls],
            layout=widgets.Layout(flex_flow="row wrap", align_items="flex-start"),
        )
        self.import_participant = widgets.Text(description="Participant:", placeholder="P01")
        self.import_session = widgets.Text(description="Session:", placeholder="Test 1")
        self.import_selection, self.import_dwell_time = _selection_controls()
        self.head_upload = widgets.FileUpload(accept=".json,.csv", multiple=False, description="Head JSON/CSV")
        self.fitts_upload = widgets.FileUpload(accept=".json", multiple=False, description="Fitts JSON")
        self.archive_upload = widgets.FileUpload(accept=".zip", multiple=False, description="Dataset ZIP")
        self.archive_review = widgets.VBox()
        self.manifest_downloads = widgets.VBox()
        self._pending_dataset: ArchiveDataset | None = None
        self._review_rows: list[dict] = []
        add = widgets.Button(description="Add session", button_style="primary")
        add.on_click(self._import_session)
        bulk = widgets.Button(description="Import dataset ZIP")
        bulk.on_click(self._import_archive)
        self.participant.observe(self._participant_changed, names="value")
        self.session.observe(self._session_changed, names="value")
        self.loading_widget = widgets.VBox([
            self.load_demo, self.demo_directory,
            widgets.HTML("<p>Load bundled ZIPs from <code>colab_session_viewer/demodata</code>, each with "
                         "a valid root CSV manifest. No Google Drive authorization is needed. "
                         "You can also enter another local folder. Unchecking does not remove imported data.</p>"),
            widgets.HTML("<h3>Import recordings</h3><p>Add a head file, a Fitts file, or a matching pair. "
                         "Repeat for additional tests/participants. Names must be unique within each participant.</p>"),
            widgets.HBox([self.import_participant, self.import_session]),
            widgets.HBox([self.import_selection, self.import_dwell_time]),
            widgets.HBox([self.head_upload, self.fitts_upload, add]),
            widgets.HTML("<p>Upload a ZIP with or without a <code>manifest.csv</code>. Without a manifest, "
                         "recordings are detected and paired by shared timestamps. Review the suggestions, "
                         "label participants/sessions, then import and download your manifest.</p>"),
            widgets.HBox([self.archive_upload, bulk]),
            self.archive_review, self.manifest_downloads, self.message,
        ], layout=widgets.Layout(width="100%"))
        if self.demo_mode:
            self.loading_widget.children = (self.message,)
        self.fitts_calculation_review = widgets.Accordion(children=[widgets.VBox([
            widgets.HTML(
                "<p>The existing table uses the standard effective-width factor, sample SD, Shannon equation "
                "and TP = IDe / average movement time. However, it pools trials across conditions/sessions "
                "and uses straight-line start-to-click distance for Ae. "
                "The published ISO-style reference calculates throughput per sequence of one A/W condition "
                "and measures Ae along the task axis, with a previous-endpoint adjustment for serial tasks. "
                "Thus these existing pooled results should be treated as descriptive, not certified "
                "ISO 9241-411 sequence throughput. Zero effective width currently yields zero IDe/TP "
                "(an unavailable/degenerate estimate, not evidence of zero performance). "
                "These legacy formulas are kept unchanged in both tables for comparability. "
                '<a href="https://www.yorku.ca/mack/FittsLawSoftware/doc/Throughput.html" target="_blank">'
                "Reference: MacKenzie, Throughput calculation and geometry</a>.</p>"
                "<p>Choose strict ID matching, or balance shared block type/amplitude/width conditions "
                "without an ID cutoff, keeping every test. Enable the comparison below to see results. "
                "Discarding targets never changes the original movement distances or their ID.</p>"
            ),
            self.fitts_matching_method,
            self.include_dwell,
            widgets.HTML(
                "<p>Selection method and dwell duration come from each imported session's labels. "
                "Unknown methods are not assumed non-dwell. Original MT/throughput stay unchanged. "
                "MT shorter than configured dwell is flagged and omitted only from adjusted timing. "
                "Inside-target visits estimate dwell attempts, not the software timer: all visits contribute "
                "to cursor/head movement, while final uninterrupted residence and first-entry-to-final-residence "
                "settling are reported separately. Target residence need not exceed configured dwell time. "
                "Head estimates require shared-clock coverage; missing paths/coverage remain blank.</p>"
            ),
        ])], selected_index=None)
        self.fitts_calculation_review.set_title(0, "Fitts calculation review")
        self.session_widget = widgets.VBox([
            widgets.HTML("<h2>Session viewer</h2>"),
            widgets.HTML("<h3>Exclusions</h3><p>Exclude the first N completed targets "
                         "<b>from every Fitts file, for all participants</b>, in chronological order. "
                         "The same targets and their time intervals are excluded from pointing and head statistics. "
                         "Plots retain coloured traces, with excluded time periods shaded grey behind them. "
                         "Source recordings and saved calibration are unchanged.</p>"),
            self.analysis_exclusions,
            widgets.HTML(
                "<p><b>Skip opening of head recording</b> excludes the chosen duration from each recording's "
                "first sample for whole-recording ranges, speed and acceleration only. Both time controls "
                "default to 1 second; 0 disables that exclusion. Calibration, reference pose, "
                "movement-exercise results and pointing results are preserved. Recording counts and durations "
                "still describe the original files. No retained samples means blank movement estimates.</p>"
            ),
            self.head_exclusion_note,
            widgets.HBox([self.participant, self.session]), self.viewer,
            widgets.HTML("<h3>Participant summaries</h3><p>One row per participant in each table, pooling retained "
                         "trials across all imported sessions using the desktop FittsStats formulas. "
                         "Times are seconds, angles degrees, distances pixels; error_rate is a fraction. "
                         "Blank values mean unavailable. Pre-test columns are averages across available sessions. "
                         "Expand Individual sessions below each main table to inspect each session's values.</p>"),
            widgets.HTML(
                "<p><b>Initial head positions use hold samples only</b> "
                "(<code>initial_rest.hold</code>), never the preceding move-to-rest phase "
                "(<code>initial_rest.move</code>). Skip the chosen opening duration from each hold's start; "
                "0 includes the full hold. Without a saved hold-start time, use the first labelled hold sample. "
                "This changes initial-rest summaries, their meshes and offsets from rest only. "
                "Saved calibration, directional holds, whole-recording head-movement statistics "
                "and pointing results are unchanged. A hold with no samples left has no rest-pose estimate.</p>"
            ),
            self.rest_exclusion_note,
            widgets.HTML("<b>Initial head position plots only</b>: set selected angles to 0.0 deg for all "
                         "participant plots. Tables, CSVs and playback are unchanged. Uncheck to restore recorded angles."),
            widgets.HBox(list(self.pose_zero_controls.values()),
                         layout=widgets.Layout(flex_flow="row wrap")),
            self.table,
            widgets.HTML("<h3>Fitts stats: matched targets (optional)</h3>"),
            widgets.HTML(
                "<p><b>ID versus IDe:</b> planned ID = log2(D/W + 1), where D is the saved distance "
                "between target centres. Effective ID (IDe) = log2(Ae/We + 1), where We = 4.133 times "
                "the sample SD of task-axis endpoint errors. IDe therefore varies with click accuracy "
                "even for the same planned test. Target count alone does not determine ID.</p>"
            ),
            self.fitts_calculation_review,
            self.match_planned_id, self.matched_fitts_table,
        ], layout=widgets.Layout(width="100%"))
        self.widget = widgets.VBox(
            [*self.loading_widget.children, *self.session_widget.children],
            layout=widgets.Layout(width="100%"),
        )
        self._show_empty()
        if self.demo_mode:
            self.load_demo.value = True

    def show(self):
        display(self.widget)
        return self

    def show_loading(self):
        display(self.loading_widget)
        return self

    def show_session(self):
        display(self.session_widget)
        return self

    def _load_demo_changed(self, change):
        if not change["new"]:
            return
        self.load_demo.disabled = self.demo_directory.disabled = True
        try:
            if self._pending_dataset is not None:
                raise ValueError("Import or cancel the current ZIP review before loading demo data")
            text = self.demo_directory.value.strip()
            if not text:
                raise ValueError("Enter the demo ZIP folder first")
            folder = Path(text).expanduser()
            if not folder.is_dir():
                raise ValueError(f"Demo folder does not exist: {folder}")
            archives = sorted(
                (p.resolve() for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".zip"),
                key=lambda p: p.name.lower(),
            )
            if not archives:
                raise ValueError(f"No demo ZIPs found in {folder}")
            pending = [path for path in archives if path not in self._loaded_demo_archives]
            sessions, datasets, notes = [], [], []
            for path in pending:
                try:
                    dataset = ArchiveDataset.read(path.read_bytes())
                    if not dataset.has_manifest:
                        raise ValueError("Demo ZIP needs a valid root CSV manifest; use manual ZIP import for labelling")
                    sessions.extend(dataset.build_sessions(dataset.assignments))
                except (ValueError, TypeError, KeyError, OSError, csv.Error, zipfile.BadZipFile) as error:
                    raise ValueError(f"{path.name}: {error}") from error
                datasets.append(dataset)
                notes.extend(f"{path.name}: {warning}" for warning in dataset.warnings)
            if sessions:
                self._add_sessions(sessions)
                for dataset in datasets:
                    self._save_manifest_link(dataset.assignments)
                self._loaded_demo_archives.update(pending)
            self._notify(
                ("Loaded Demo Data" if self.demo_mode else
                 f"Loaded {len(sessions)} demo session(s) from {len(pending)} ZIP(s). "
                 f"{len(archives) - len(pending)} previously loaded ZIP(s) skipped. ")
                + (" Import notes: " + "; ".join(notes) if notes else "")
            )
        except (ValueError, TypeError, KeyError, OSError, csv.Error, zipfile.BadZipFile) as error:
            self.load_demo.value = False
            self._notify(f"Demo import failed: {error}", error=True)
        finally:
            self.load_demo.disabled = self.demo_directory.disabled = False

    def _show_empty(self):
        with self.viewer:
            clear_output(wait=True)
            display(HTML("<p>Import recordings above, then choose a participant and session here.</p>"))

    def _notify(self, text, error=False):
        color = "#b00020" if error else "#256329"
        self.message.value = f'<p role="status" style="color:{color}">{html.escape(text)}</p>'

    def add_session(self, participant, name, *, head_path=None, fitts_path=None,
                    selection_method="unknown", dwell_time_s=None):
        session = Session.load(
            participant, name, head_path=head_path, fitts_path=fitts_path,
            selection_method=selection_method, dwell_time_s=dwell_time_s,
        )
        self._add_sessions([session])
        return session

    def _add_sessions(self, sessions: list[Session]):
        keys = {(s.participant, s.name) for s in self.sessions}
        for session in sessions:
            key = (session.participant, session.name)
            if key in keys:
                raise ValueError(f"Duplicate participant/session: {key[0]} / {key[1]}")
            keys.add(key)
            # Validate rendering before changing the collection; failed imports are atomic.
            payload_json(playback_payload(
                session, exclude_first_targets=self.exclude_targets.value,
                initial_rest_exclude_s=self.initial_rest_exclude.value,
                head_opening_exclude_s=self.head_opening_exclude.value,
            ))
        self.sessions.extend(sessions)
        self._changing = True
        try:
            self.participant.options = sorted({s.participant for s in self.sessions})
            self.participant.value = sessions[-1].participant
            self.session.options = [s.name for s in self.sessions if s.participant == self.participant.value]
            self.session.value = sessions[-1].name
        finally:
            self._changing = False
        self._refresh_table()
        self._render()
        self._notify(f"Added {len(sessions)} session(s). {len(self.sessions)} loaded in total.")

    def _import_session(self, _):
        try:
            head, fitts = _uploads(self.head_upload), _uploads(self.fitts_upload)
            with tempfile.TemporaryDirectory() as temporary:
                paths = {}
                for uploaded, key in ((head, "head_path"), (fitts, "fitts_path")):
                    if uploaded:
                        name, content = uploaded[0]
                        paths[key] = _write_upload(Path(temporary), name, content, key)
                session = Session.load(
                    self.import_participant.value, self.import_session.value, **paths,
                    selection_method=self.import_selection.value,
                    dwell_time_s=self.import_dwell_time.value if self.import_selection.value == "dwell" else None,
                )
                session.head_filename = head[0][0] if head else ""
                session.fitts_filename = fitts[0][0] if fitts else ""
                self._add_sessions([session])
            for upload in (self.head_upload, self.fitts_upload):
                upload.value = {} if isinstance(upload.value, dict) else ()
        except (ValueError, TypeError, KeyError, OSError, csv.Error) as error:
            self._notify(f"Import failed: {error}", error=True)

    def _import_archive(self, _):
        try:
            if self._pending_dataset is not None:
                raise ValueError("Import or cancel the current ZIP review before opening another ZIP")
            uploaded = _uploads(self.archive_upload)
            if not uploaded:
                raise ValueError("Choose a dataset ZIP first")
            dataset = ArchiveDataset.read(uploaded[0][1])
            if dataset.has_manifest:
                self._add_sessions(dataset.build_sessions(dataset.assignments))
                self._save_manifest_link(dataset.assignments)
            else:
                self._show_archive_review(dataset)
        except (ValueError, TypeError, KeyError, OSError, csv.Error, zipfile.BadZipFile) as error:
            self._notify(f"Dataset import failed: {error}", error=True)

    def _show_archive_review(self, dataset: ArchiveDataset):
        self._pending_dataset = dataset
        self._review_rows = []
        children = [widgets.HTML(
            "<h4>Review ZIP recordings</h4><p>Participant labels are required; none are inferred from timestamps. "
            "Matched streams use their shared host clock (no artificial start-time shift). "
            "Choose different files or leave a stream blank to split a suggestion; untick redundant rows. "
            "The Participant input supplies the default label; each row remains editable. "
            "Each file can be included only once. No data is added until you confirm.</p>"
        )]
        if dataset.warnings:
            children.append(widgets.HTML("<p><b>Import notes:</b><br>" +
                                         "<br>".join(html.escape(w) for w in dataset.warnings) + "</p>"))
        for assignment in dataset.assignments:
            children.append(self._archive_row(dataset, assignment))
        confirm = widgets.Button(description="Import labelled sessions", button_style="primary",
                                 layout=widgets.Layout(width="200px"))
        confirm.on_click(self._confirm_archive)
        cancel = widgets.Button(description="Cancel ZIP review")
        cancel.on_click(self._cancel_archive)
        add_row = widgets.Button(description="Add assignment row")
        def append_row(_):
            row = self._archive_row(dataset, Assignment())
            existing = self.archive_review.children
            self.archive_review.children = (*existing[:-1], row, existing[-1])
        add_row.on_click(append_row)
        children.append(widgets.HBox([confirm, add_row, cancel]))
        self.archive_review.children = tuple(children)
        self._notify(f"Found {len(dataset.recordings)} recording(s). Review timestamp matches and enter participant/session labels.")

    def _archive_row(self, dataset: ArchiveDataset, assignment: Assignment):
        heads = [name for name, s in dataset.recordings.items() if s.head_filename]
        fitts = [name for name, s in dataset.recordings.items() if s.fitts_filename]
        controls = {
            "include": widgets.Checkbox(value=True, description="Include", indent=False),
            "participant": widgets.Text(value=assignment.participant or self.import_participant.value.strip(),
                                        description="Participant:", placeholder="P01"),
            "session": widgets.Text(value=assignment.session, description="Session:"),
            "head_file": widgets.Dropdown(options=[""] + heads, value=assignment.head_file,
                                         description="Head file:", layout=widgets.Layout(width="95%")),
            "fitts_file": widgets.Dropdown(options=[""] + fitts, value=assignment.fitts_file,
                                          description="Fitts file:", layout=widgets.Layout(width="95%")),
            "details": widgets.HTML(),
        }
        controls["selection_method"], controls["dwell_time_s"] = _selection_controls(
            assignment.selection_method, assignment.dwell_time_s,
        )
        def update_details(_=None):
            names = [controls[key].value for key in ("head_file", "fitts_file") if controls[key].value]
            controls["details"].value = "<br>".join(html.escape(dataset.describe(name)) for name in names)
        for key in ("head_file", "fitts_file"):
            controls[key].observe(update_details, names="value")
        update_details()
        self._review_rows.append(controls)
        return widgets.VBox([
            widgets.HBox([controls["include"], controls["participant"], controls["session"]]),
            widgets.HBox([controls["selection_method"], controls["dwell_time_s"]]),
            controls["head_file"], controls["fitts_file"], controls["details"],
        ], layout=widgets.Layout(border="1px solid #ccc", padding="8px", margin="4px 0"))

    def _confirm_archive(self, _):
        try:
            if self._pending_dataset is None:
                raise ValueError("No ZIP is waiting for review")
            assignments = [
                Assignment(
                    **{key: controls[key].value.strip()
                       for key in ("participant", "session", "head_file", "fitts_file")},
                    selection_method=controls["selection_method"].value,
                    dwell_time_s=(controls["dwell_time_s"].value
                                  if controls["selection_method"].value == "dwell" else None),
                )
                for controls in self._review_rows if controls["include"].value
            ]
            self._add_sessions(self._pending_dataset.build_sessions(assignments))
            self._save_manifest_link(assignments)
            self._cancel_archive(None, notify=False)
        except (ValueError, TypeError, KeyError, OSError, csv.Error) as error:
            self._notify(f"ZIP review: {error}", error=True)

    def _cancel_archive(self, _, notify=True):
        self._pending_dataset = None
        self._review_rows = []
        self.archive_review.children = ()
        if notify:
            self._notify("ZIP review cancelled. Previously imported sessions are unchanged.")

    def _save_manifest_link(self, assignments: list[Assignment]):
        encoded = base64.b64encode(manifest_csv(assignments).encode("utf-8-sig")).decode("ascii")
        number = len(self.manifest_downloads.children) + 1
        link = widgets.HTML(
            f'<p><a download="manifest.csv" href="data:text/csv;base64,{encoded}">'
            f'Download manifest.csv for ZIP import {number}</a> '
            '(put this at the root of the original ZIP to reproduce these assignments).</p>'
        )
        self.manifest_downloads.children = (*self.manifest_downloads.children, link)

    def _participant_changed(self, _):
        if self._changing:
            return
        self._changing = True
        try:
            self.session.options = [s.name for s in self.sessions if s.participant == self.participant.value]
        finally:
            self._changing = False
        self._render()

    def _session_changed(self, _):
        if not self._changing:
            self._render()

    def _render(self):
        selected = next((s for s in self.sessions
                         if s.participant == self.participant.value and s.name == self.session.value), None)
        if selected is None:
            self._show_empty()
            return
        try:
            document = viewer_document(playback_payload(
                selected, exclude_first_targets=self.exclude_targets.value,
                initial_rest_exclude_s=self.initial_rest_exclude.value,
                head_opening_exclude_s=self.head_opening_exclude.value,
            ))
        except ValueError as error:
            self._show_analysis_error(error)
            return
        iframe = IFrame(
            src="about:blank", width="100%", height=1150,
            extras=[
                'title="Head and cursor session playback"',
                'sandbox="allow-scripts"',
                'style="width:100%;height:1150px;border:0"',
                f'srcdoc="{html.escape(document, quote=True)}"',
            ],
        )
        with self.viewer:
            clear_output(wait=True)
            display(iframe)

    @property
    def exclusions(self):
        return {s.participant: self.exclude_targets.value for s in self.sessions}

    def summary_rows(self):
        """Calculate main participant rows using global exclusions, never matching.

        Delegate formulas/aggregation to data.participant_rows; include_dwell
        adds measures without changing the original Fitts/head results.
        """
        return participant_rows(
            self.sessions, self.exclusions, initial_rest_exclude_s=self.initial_rest_exclude.value,
            head_opening_exclude_s=self.head_opening_exclude.value,
            include_dwell=self.include_dwell.value,
        )

    def _pose_zero_changed(self, change):
        try:
            self._refresh_table()
        except ValueError as error:
            self._show_analysis_error(error)

    def _initial_rest_exclusion_changed(self, change):
        try:
            self._refresh_table()
            if self.participant.value is not None and self.session.value is not None:
                self._render()
        except ValueError as error:
            self._show_analysis_error(error)

    def _show_analysis_error(self, error):
        self.table.value = f"<p style='color:#b00020'>Cannot calculate summaries: {html.escape(str(error))}</p>"
        with self.viewer:
            clear_output(wait=True)
            display(widgets.HTML(self.table.value))

    def _refresh_table(self):
        rows = self.summary_rows()
        if not rows:
            self.table.value = "No recordings imported."
            self.matched_fitts_table.value = ""
            self.rest_exclusion_note.value = ""
            self.head_exclusion_note.value = ""
            return
        per_session = []
        exhausted = []
        head_exhausted = []
        for session in self.sessions:
            row = participant_rows(
                [session], self.exclusions, initial_rest_exclude_s=self.initial_rest_exclude.value,
                head_opening_exclude_s=self.head_opening_exclude.value,
                include_dwell=self.include_dwell.value,
            )[0]
            row["session"] = session.name
            per_session.append(row)
            if row.get("head_initial_rest_sample_count_session_mean") == 0:
                exhausted.append(f"{session.participant} / {session.name}")
            if session.samples and row["head_movement_samples"] == 0:
                head_exhausted.append(f"{session.participant} / {session.name}")
        self.head_exclusion_note.value = (
            "<p><b>No head samples remain after movement exclusions:</b> " +
            html.escape(", ".join(head_exhausted)) + ". Movement estimates are blank.</p>"
            if head_exhausted else ""
        )
        self.rest_exclusion_note.value = (
            "<p><b>No initial-rest samples remain after exclusion:</b> " +
            html.escape(", ".join(exhausted)) +
            ". Rest-pose estimates and offsets from rest are blank for these sessions; "
            "saved calibration heading is still shown.</p>"
            if exhausted else ""
        )
        sections = []
        descriptions = {
            "Session summary": "Recording counts and durations.",
            "Initial head positions": (
                "Initial rest is the labelled still period before the movement exercise. "
                "Only initial_rest.hold samples are used, never the preceding initial_rest.move phase. "
                f"The opening {self.initial_rest_exclude.value:g} seconds of each initial-rest hold are excluded "
                "from rest measurements, their meshes and offsets from rest. Set the opening exclusion to 0 "
                "for the full hold. A hold with no samples left contributes no rest-pose estimate. "
                "Recorded yaw is the heading reported by the tracker during that period; it is not "
                "necessarily a compass heading, because gravity does not determine yaw. "
                "Yaw from calibration heading measures that same initial-rest heading from the saved calibration heading, "
                "removing only yaw, not pitch or roll. Calibration heading is the yaw of the average saved calibration pose. "
                "The meshes combine calibration heading with average initial-rest pitch/roll; they are not a pure initial-rest pose. "
                "xIMU3/AirPods estimate pitch/roll relative to gravity, depending on processing and mounting/fit. "
                "Webcam pitch/roll are relative to the camera, not gravity. "
                "xIMU3 heading may use magnetic north when enabled and reliable; AirPods heading has an arbitrary zero. "
                "Without saved calibration, recorded yaw remains available but yaw from calibration heading and the meshes are unavailable."
            ),
            "Range of movement stats": (
                "Uses only the labelled movement exercise. Movement ranges use both moving and holding samples. "
                "Directional positions use only the samples while holding left, right, up or down. "
                "An offset is the change from the average initial-rest angle. Results average the available "
                "session values; _session_mean in a CSV key means average across sessions."
            ),
            "Fitts stats": (
                "All measures in this main table, including ID and IDe, use all targets from all of the "
                "participant's files after global first-target exclusions. Set First targets per Fitts file "
                "to 0 to include every recorded target. Matching never changes this table; only the separate "
                "matched-target comparison uses a matched subset. Path measures require usable recorded paths. "
                "ID describes target geometry; IDe uses effective width calculated from click spread, so a "
                "large spread can make IDe much lower than ID. A trial is one reach and click."
            ),
            "Fitts targets and hits": "Counts of completed targets and successful clicks, after leaving out any excluded targets. A miss is a click outside the target.",
            "Dwell behaviour stats": (
                "Uses all analysed dwell targets after global first-target exclusions, never the matched subset. "
                "Inside-target visits estimate dwell attempts, not software timer events. Movement averages "
                "use available per-target estimates; contributing counts show coverage. Settling runs from "
                "first entry to the start of final uninterrupted residence. Missing or inapplicable values "
                "are shown as '-'; non-dwell sessions do not contribute zeros to dwell estimates. "
                "Timing validity counts also explain the MTnodwell/throughput denominator in the Fitts table."
            ),
            "Head movement stats": (
                "Uses head recordings, including preparation and the movement exercise, not just pointing, "
                f"after excluding the opening {self.head_opening_exclude.value:g} seconds of each recording. "
                "Skipped targets' start-to-click intervals are also excluded. "
                "Set the recording-opening exclusion to 0 for the full recording. "
                "This is not a rest-hold-only table and is unaffected by the initial-rest opening exclusion. "
                "Speed measures how fast the head turns in 3D; acceleration measures how quickly that speed changes. "
                "Movement ranges use relative pose: movement measured from the reference pose, normally calibration "
                "but with saved recentering taking priority. Without either, initial rest or the first samples are used. "
                "Each angle's range is its largest minus smallest value after removing jumps at +/-180 degrees. "
                "The participant result is the largest single-recording range, not a range spanning different files. "
                "A tilted reference can mix changes between yaw, pitch and roll. Near +/-90 degrees pitch, "
                "yaw and roll become difficult to separate. The checks below flag relative pitch of 80 degrees or more "
                "away from zero, and also show ranges during the pointing task only."
            ),
        }
        for label, table_rows in participant_tables(rows).items():
            filename = label.lower().replace(" ", "_") + ".csv"
            columns = list(table_rows[0])
            session_rows = [
                {
                    "participant": row["participant"], "session": row["session"],
                    **{key: row.get(key) for key in columns if key != "participant"},
                }
                for row in per_session
            ]
            conditions = ""
            if label == "Fitts stats":
                condition_rows = fitts_condition_rows(self.sessions, self.exclude_targets.value)
                conditions = (
                    '<details class="fitts-conditions"><summary><b>Fitts test conditions by file</b></summary>'
                    "<p>One row per participant/Fitts file, using only saved Fitts trials, not head data. "
                    "Lists amplitude settings, widths, block types and observed counts. "
                    "The files do not save the complete settings panel: observed repetitions and distinct "
                    "target positions cannot prove the original settings or that a run was complete. "
                    "For circle tasks, saved amplitude is the configured radius; screen fitting and target "
                    "order determine actual movement distances. Targets per condition/repetition "
                    "are not the same as the number of repetitions per condition. "
                    "Analysed counts apply global target exclusions, not optional ID matching.</p>" +
                    (self._table_html(condition_rows, "fitts_test_conditions.csv", "Download Fitts conditions CSV")
                     if condition_rows else "<p>No recorded Fitts trials.</p>") +
                    "</details>"
                )
            sections.append(
                f"<h3>{label}</h3>" + conditions +
                self._table_html(table_rows, filename, f"Download {label.lower()} CSV") +
                '<details class="session-details"><summary><b>Individual sessions</b></summary>'
                '<p>Same settings as the participant table. Each row uses only that session; '
                'Fitts results are recalculated from its analysed targets. Session rows are inspection-only '
                'and are not extra observations in the participant CSV. Blank means unavailable.</p>' +
                self._table_html(session_rows, None, None) +
                "</details>"
            )
            sources = table_sources(self.sessions, label)
            sections.append(
                '<details style="font-size:11px;margin:6px 0;overflow-wrap:anywhere"><summary><b>Source files</b></summary><ul>' +
                "".join(f"<li>{html.escape(participant)}: " +
                        (", ".join(html.escape(name) for name in names) if names else "No contributing files") +
                        "</li>" for participant, names in sorted(sources.items())) +
                "</ul></details>"
            )
            if label == "Initial head positions":
                sections.append(
                    "<details><summary><b>Angle references and zero-position diagrams</b></summary>"
                    f"<p>{descriptions[label]}</p>" + initial_pose_reference_diagram() + "</details>"
                )
                zero_axes = tuple(axis for axis, control in self.pose_zero_controls.items() if control.value)
                sections.append(initial_pose_wireframes(table_rows, zero_axes=zero_axes))
            else:
                sections.append(
                    f"<details><summary><b>Calculation notes</b></summary><p>{descriptions[label]}</p></details>"
                )
            if label == "Head movement stats":
                diagnostics = [row for session in self.sessions
                               if (row := head_range_diagnostics(
                                   session, head_opening_exclude_s=self.head_opening_exclude.value,
                                   exclude_first_targets=self.exclude_targets.value,
                               )) is not None]
                if diagnostics:
                    sections.append(
                        "<details><summary>Movement range checks by recording (analysed recording versus pointing task)</summary>" +
                        self._table_html(diagnostics, "head_range_diagnostics.csv", "Download range diagnostics CSV") +
                        "<p>Both checks respect head-recording opening and target-interval exclusions and retain the original reference pose. "
                        "Pointing-task results use samples labelled task_phase = fitts_test; recordings "
                        "without those labels have no pointing-only estimate. A large heading change may also change "
                        "relative pitch/roll when the reference pose is tilted. Drift and unusually large values "
                        "are not automatically removed.</p></details>"
                    )
        encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
        self.table.value = (
            f'<a download="participant_summary.csv" href="data:text/csv;base64,{encoded}">Download summary CSV (all metrics)</a>'
            + "".join(sections)
        )
        if self.match_planned_id.value:
            try:
                matched = matched_fitts_rows(
                    self.sessions, self.exclude_targets.value, method=self.fitts_matching_method.value,
                    include_dwell=self.include_dwell.value,
                )
            except ValueError as error:
                self.matched_fitts_table.value = (
                    "<p style='color:#b00020'>Cannot calculate matched-target comparison: " +
                    html.escape(str(error)) + "</p>"
                )
            else:
                matched_fitts = [
                    {
                        column: row[column]
                        for key in row if key not in DWELL_BEHAVIOUR_FIELDS and key not in NODWELL_FITTS_FIELDS
                        for column in (
                            (key, NODWELL_EQUIVALENTS[key])
                            if key in NODWELL_EQUIVALENTS and NODWELL_EQUIVALENTS[key] in row else (key,)
                        )
                    }
                    for row in matched
                ]
                matched_dwell = ""
                if self.include_dwell.value:
                    matched_dwell = (
                        "<h4>Dwell behaviour stats: matched targets</h4>"
                        "<p>Uses only the matched targets, unlike the main dwell behaviour table. "
                        "'-' means unavailable or not applicable, including dwell-only fields for "
                        "non-dwell selection.</p>" +
                        self._table_html(
                            participant_tables(matched)["Dwell behaviour stats"],
                            "matched_dwell_behaviour_stats.csv", "Download matched dwell behaviour CSV",
                        )
                    )
                method_note = (
                    "Balances the maximum equal target count within each shared block type, saved amplitude "
                    "and width, with no planned-ID cutoff. Every test must contribute; test quotas within a "
                    "condition are shared as evenly as availability allows. The smallest participant pool "
                    "per condition supplies reference IDs; nearest unused IDs are chosen within test quotas. "
                    "This deterministic greedy rule prioritises target counts and test retention, not a global "
                    "minimum ID difference. Tests with no remaining/shared targets produce an explicit notice. "
                    "The table reports per-test contributions and the largest ID difference from reference "
                    "targets. Original target distances and IDs are never changed. "
                    if self.fitts_matching_method.value == "target_conditions" else
                    "Same task type and target width; each matched set differs by at most 0.1 bits. "
                    "Matching starts with the participant having the fewest available targets (label breaks ties), "
                    "then takes nearest unused IDs in stable session/trial order. It is not an optimal matching "
                    "or a guarantee of matching movement directions/session composition. "
                )
                self.matched_fitts_table.value = (
                    "<p>Compares targets using planned ID = log2(D/W + 1), from saved start-to-target distance D. " +
                    method_note +
                    "Equal numbers of targets are selected per participant, after global target exclusions. "
                    "Selection uses geometry only, not clicks, success or movement time. "
                    "IDe may still differ because endpoint accuracy differs. "
                    "Results use the same pooled descriptive formulas as the original Fitts table; "
                    "this is not a sequence-level ISO throughput estimate. "
                    "Only this table is subsetted; other tables and playback remain unchanged.</p>" +
                    self._table_html(matched_fitts, "matched_planned_id_fitts_stats.csv", "Download matched-target CSV") +
                    matched_dwell
                )
        else:
            self.matched_fitts_table.value = ""

    @staticmethod
    def _table_html(rows, filename, download_label):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        header = "".join(
            f'<th title="{html.escape(c, quote=True)}" style="padding:4px;overflow-wrap:anywhere;'
            f'font-weight:bold">{html.escape(column_label(c))}</th>' for c in columns
        )
        body = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                text = (
                    "-" if value is None and column in (*DWELL_BEHAVIOUR_FIELDS, *NODWELL_FITTS_FIELDS)
                    else format_number(value)
                )
                cells.append(f"<td>{html.escape(text)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        sizing = (
            "width:max-content;max-width:none;overflow-wrap:normal"
            if filename is None else "width:auto;max-width:100%;overflow-wrap:anywhere"
        )
        table = (
            '<div style="width:100%;max-width:100%;overflow-x:auto">'
            '<table border="1" cellpadding="3" style="border-collapse:collapse;table-layout:auto;'
            f'{sizing};font-size:11px;line-height:1.25">'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )
        download = ""
        if filename is not None:
            encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
            download = f'<a download="{filename}" href="data:text/csv;base64,{encoded}">{download_label}</a>'
        return (
            download + table +
            '<details style="overflow-wrap:anywhere"><summary><b>Field key</b></summary>'
            '<p>Blank means unavailable. Values are displayed/exported to 1 decimal place; calculations retain precision. '
            "'-' in dwell behaviour or no-dwell fields means unavailable or not applicable, not zero. "
            'Fitts measures pool retained trials across files, including misses. Path measures average only usable '
            'trials, using sampled paths with the click point appended when needed.</p><dl>' +
            "".join(
                f'<dt><b>{html.escape(column_label(c))}</b> <code>{html.escape(c)}</code></dt>'
                f'<dd>{html.escape(field_description(c))}</dd>' for c in columns
            ) + '</dl></details>'
        )
