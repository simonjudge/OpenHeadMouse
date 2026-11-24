"""ipywidgets shell: a single browser viewer, repeated/bulk imports and summaries."""

from __future__ import annotations

import base64
import csv
import html
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import ipywidgets as widgets
from IPython.display import display

from colab_session_viewer.data import (
    Session, format_number, format_field, initial_pose_reference_diagram, initial_pose_wireframes, participant_rows, participant_tables,
    payload_json, playback_payload, summary_csv, column_label, head_range_diagnostics,
    field_description, fitts_condition_rows,
)
from colab_session_viewer.archive import ArchiveDataset, Assignment, load_archive, manifest_csv
from colab_session_viewer.cursor_paths import (
    cursor_path_plot, format_target_selection, numbered_targets, parse_target_selection,
)
from colab_session_viewer.selection_stats import alternative_selection_rows
from colab_session_viewer.outliers import review_targets
from colab_session_viewer.dwell import (
    DWELL_BEHAVIOUR_FIELDS, NODWELL_EQUIVALENTS, NODWELL_FITTS_FIELDS, SELECTION_METHODS,
)
from fitts_law.clutch import DEFAULT_CLUTCH_SETTINGS, ClutchSettings

_FOLDER = Path(__file__).parent
_TABLE_NUMBERS = {
    "Session summary": 1,
    "Initial head positions": 2,
    "Range of movement stats": 3,
    "Head movement stats": 4,
    "Fitts targets and hits": 5,
    "Fitts stats": 6,
    "Dwell behaviour stats": 7,
    "No-Dwell stats": 8,
    "First-entry selection stats": 9,
    "Clutch stats": 10,
}


def _table_heading(title: str) -> str:
    return f"<h3>Table {_TABLE_NUMBERS[title]}: {html.escape(title)}</h3>"


@dataclass
class CursorPathControl:
    text: widgets.Text
    up: widgets.Button
    down: widgets.Button
    show_all: widgets.Button
    hint: widgets.HTML
    output: widgets.HTML
    maximum: int
    suppress: bool = False


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
        self.trial = widgets.BoundedIntText(
            value=1, min=1, max=1, description="Trial:", disabled=True,
            layout=widgets.Layout(width="135px"),
        )
        self.previous_trial = widgets.Button(
            description="<", tooltip="Previous trial", disabled=True, layout=widgets.Layout(width="35px"),
        )
        self.next_trial = widgets.Button(
            description=">", tooltip="Next trial", disabled=True, layout=widgets.Layout(width="35px"),
        )
        self.zoom_to_trial = widgets.Checkbox(
            value=False, description="Zoom to trial", indent=False, disabled=True,
            layout=widgets.Layout(width="140px"),
        )
        self._playback_session = None
        self._seek_trial = False
        self.trial.observe(self._trial_changed, names="value")
        self.zoom_to_trial.observe(self._trial_changed, names="value")
        self.previous_trial.on_click(lambda _: self._step_trial(-1))
        self.next_trial.on_click(lambda _: self._step_trial(1))
        self.viewer = widgets.HTML(layout=widgets.Layout(width="100%"))
        self.message = widgets.HTML()
        self.load_demo = widgets.Checkbox(value=False, description="Load Demo Data", indent=False)
        self.demo_directory = widgets.Text(
            value=str(_FOLDER / "demodata") if demo_directory is None else demo_directory,
            description="Demo folder:", layout=widgets.Layout(width="95%"),
        )
        self._loaded_demo_archives: set[Path] = set()
        self.load_demo.observe(self._load_demo_changed, names="value")
        self.table = widgets.HTML()
        self.fitts_table = widgets.HTML()
        self.target_review = widgets.VBox()
        self._target_review_controls: dict[int, widgets.Checkbox] = {}
        self._excluded_fitts_trials: set[int] = set()
        self.cursor_paths = widgets.VBox()
        self._cursor_path_controls: dict[str, CursorPathControl] = {}
        self.nodwell_table = widgets.HTML()
        self.first_entry_table = widgets.HTML()
        self.include_dwell = widgets.Checkbox(
            value=False, description="Show dwell behaviour estimates",
            indent=False, layout=widgets.Layout(width="auto"),
        )
        self.include_dwell.observe(self._refresh_after_analysis_change, names="value")
        self.initial_rest_exclude = widgets.BoundedFloatText(
            value=1.0, min=0.0, max=1e6, step=0.1,
            description="Skip opening of each hold (s):",
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

        def labelled(control, label, tooltip):
            label_widget = widgets.HTML(
                f'<span title="{html.escape(tooltip, quote=True)}" '
                f'style="cursor:help;border-bottom:1px dotted #777">{html.escape(label)} (?)</span>'
            )
            return widgets.VBox(
                [label_widget, control], layout=widgets.Layout(margin="0 20px 0 0"),
            )

        self.exclusion_controls = labelled(
            self.exclude_targets, "First targets per Fitts file",
            "Skips the first N completed targets in every Fitts file, across all participants. "
            "These targets are omitted from pointing results; their time intervals are also omitted "
            "from head statistics when paired recordings have a shared clock. Target numbering and "
            "source recordings stay unchanged.",
        )
        self.time_exclusion_controls = widgets.HBox(
            [
                labelled(
                    self.initial_rest_exclude, "Each hold opening (s)",
                    "Skips this many seconds from the start of every labelled still hold. "
                    "Affects initial-pose, exercise and movement estimates, but not move phases, "
                    "calibration, playback or pointing results. Use 0 to include each whole hold.",
                ),
                labelled(
                    self.head_opening_exclude, "Head recording opening (s)",
                    "Skips this many seconds from the first sample of each head recording for "
                    "whole-recording movement range, speed and acceleration only. Initial pose, "
                    "exercise results, pointing results, calibration and source recordings are unchanged. "
                    "Use 0 to include the full recording.",
                ),
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
        self.individual_manifest_download = widgets.HTML()
        self._individual_assignments: list[Assignment] = []
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
            self.individual_manifest_download,
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
                "These legacy formulas are kept unchanged for comparability. "
                '<a href="https://www.yorku.ca/mack/FittsLawSoftware/doc/Throughput.html" target="_blank">'
                "Reference: MacKenzie, Throughput calculation and geometry</a>.</p>"
                "<p>Review exclusions never change original target distances or IDs.</p>"
            ),
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
        clutch_fields = (
            ("edge_zone_px", "Near-edge zone (px)", "Distance from the screen edge; also affects legacy boundary estimates."),
            ("still_axis_px", "Plateau tolerance (px)", "Maximum edge-normal cursor displacement from the plateau's first sample."),
            ("min_still_s", "Minimum plateau (s)", "Minimum duration of the near-edge cursor plateau."),
            ("max_cursor_gap_s", "Maximum cursor gap (s)", "Largest gap within an off-screen plateau or on-screen episode."),
            ("max_head_gap_s", "Maximum head gap (s)", "Largest head-sample gap allowed within an eligible trial."),
            ("outward_deg", "Outward head rotation (deg)", "Minimum head rotation toward the edge during the plateau."),
            ("return_deg", "Head return (deg)", "Minimum head reversal from the outward peak."),
            ("return_px", "Cursor return (px)", "Minimum inward cursor movement from the plateau."),
            ("return_window_s", "Return window (s)", "Maximum time after the plateau for the head and cursor return."),
            ("onscreen_hysteresis_px", "On-screen reversal tolerance (px)", "Target-axis displacement required to establish a reversal."),
            ("onscreen_retreat_px", "On-screen retreat (px)", "Minimum backward target-axis displacement and increase in distance from the target."),
            ("onscreen_return_px", "On-screen return (px)", "Minimum forward displacement; the return must also recover the entire retreat."),
            ("onscreen_speed_ratio", "On-screen return speed ratio", "Mean forward target-axis speed must exceed this multiple of backward speed."),
            ("onscreen_max_phase_s", "On-screen phase limit (s)", "Maximum duration of each retreat and return phase."),
            ("onscreen_opposition", "On-screen direction opposition", "Minimum cosine of opposition between retreat and return; 1 means exactly opposite."),
        )
        self.clutch_controls = {}
        self._resetting_clutch = False
        controls = []
        for key, label, tooltip in clutch_fields:
            control = widgets.BoundedFloatText(
                value=getattr(DEFAULT_CLUTCH_SETTINGS, key),
                min=(1 if key == "onscreen_speed_ratio" else
                     0.001 if key in ("max_cursor_gap_s", "max_head_gap_s",
                                      "onscreen_hysteresis_px", "onscreen_max_phase_s") else 0),
                max=1 if key == "onscreen_opposition" else 1e6, step=0.01, continuous_update=False,
                layout=widgets.Layout(width="150px"),
            )
            self.clutch_controls[key] = control
            controls.append(labelled(control, label, tooltip))
            control.observe(self._clutch_settings_changed, names="value")
        reset_clutch = widgets.Button(description="Reset clutch defaults")
        reset_clutch.on_click(self._reset_clutch_settings)
        self.clutch_settings_panel = widgets.Accordion(children=[widgets.VBox([
            widgets.HTML(
                "<p>Change the thresholds to compare the estimate with observed behaviour. "
                "Results, CSVs and notebook playback update automatically (playback restarts); "
                "recorded data and desktop playback are unchanged. "
                "Near-edge also affects estimated boundary contacts in older files, but not saved event counts. "
                "If the zone is at least half a screen dimension, off-screen and legacy boundary "
                "estimates are unavailable; on-screen estimates do not require screen geometry.</p>"
            ),
            widgets.HBox(controls, layout=widgets.Layout(flex_flow="row wrap")),
            reset_clutch,
        ])], selected_index=0)
        self.clutch_settings_panel.set_title(0, "Clutch estimate settings")
        self.session_widget = widgets.VBox([
            widgets.HTML("<h2>Session viewer</h2>"),
            widgets.HTML("<h3>Exclusions</h3>"),
            self.analysis_exclusions,
            self.head_exclusion_note,
            widgets.HBox([
                self.participant, self.session, self.previous_trial, self.trial,
                self.next_trial, self.zoom_to_trial,
            ], layout=widgets.Layout(flex_flow="row wrap")), self.viewer,
            widgets.HTML("<h3>Participant summaries</h3><p>One row per participant. "
                         "Expand <b>Table Notes</b> for field definitions, session details and angle references.</p>"),
            self.rest_exclusion_note,
            self.table,
            widgets.HTML("<h2>Fitts results</h2>"),
            self.target_review,
            self.fitts_calculation_review,
            self.clutch_settings_panel,
            self.fitts_table,
            widgets.HTML(_table_heading("No-Dwell stats")),
            self.nodwell_table,
            widgets.HTML(_table_heading("First-entry selection stats")),
            self.first_entry_table,
            widgets.HTML(
                "<h3>Cumulative cursor paths</h3><p>One plot per participant across all their Fitts files. "
                "Targets are numbered 1&ndash;N continuously across the participant's files (files in recording "
                "order, targets in original order), so a second 12-target test is targets 13&ndash;24. "
                "Type ranges and/or lists such as <code>1-12</code> or <code>1,2,24</code>; "
                "&#9650; Next and &#9660; Previous step through one target path at a time and All restores "
                "every target. First-target exclusions apply to each file and keep the numbering unchanged. "
                "Different screen sizes are shown separately; missing dimensions are estimated as in playback. "
                "Lines show saved samples through the click; if no path was recorded, only the click appears. "
                "Colours repeat after 10 targets. Circles are targets; dots are clicks. "
                "Download an SVG image using the link below each plot.</p>"
            ),
            self.cursor_paths,
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
        self.viewer.value = "<p>Import recordings above, then choose a participant and session here.</p>"

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
        self._save_individual_manifest_link(session)
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
                clutch_settings=self.clutch_settings,
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
                self._save_individual_manifest_link(session)
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

    def _save_individual_manifest_link(self, session: Session):
        self._individual_assignments.append(Assignment(
            participant=session.participant, session=session.name,
            head_file=session.head_filename, fitts_file=session.fitts_filename,
            selection_method=session.selection_method, dwell_time_s=session.dwell_time_s,
        ))
        self._save_manifest_link(self._individual_assignments, individual=True)

    def _save_manifest_link(self, assignments: list[Assignment], *, individual=False):
        encoded = base64.b64encode(manifest_csv(assignments).encode("utf-8-sig")).decode("ascii")
        if individual:
            self.individual_manifest_download.value = (
                f'<p><a download="manifest.csv" href="data:text/csv;base64,{encoded}">'
                'Download manifest.csv for individual recordings</a> '
                '(includes all individually added sessions; put this at the root of a ZIP '
                'alongside the original recordings, using unique filenames).</p>'
            )
            return
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

    def _step_trial(self, step):
        if not self.trial.disabled:
            self.trial.value = min(self.trial.max, max(1, self.trial.value + step))

    def _trial_changed(self, _):
        if not self._changing:
            self._seek_trial = True
            self._render()

    def _render(self):
        selected = next((s for s in self.sessions
                         if s.participant == self.participant.value and s.name == self.session.value), None)
        if selected is None:
            self.trial.disabled = self.previous_trial.disabled = self.next_trial.disabled = True
            self.zoom_to_trial.disabled = True
            self._show_empty()
            return
        if selected is not self._playback_session:
            self._changing = True
            try:
                self.trial.value = 1
                self.trial.max = max(1, len(selected.trials))
                self._seek_trial = False
                self._playback_session = selected
            finally:
                self._changing = False
        self.trial.disabled = self.zoom_to_trial.disabled = not selected.trials
        self.previous_trial.disabled = not selected.trials or self.trial.value <= 1
        self.next_trial.disabled = not selected.trials or self.trial.value >= len(selected.trials)
        try:
            payload = playback_payload(
                selected, exclude_first_targets=self.exclude_targets.value,
                initial_rest_exclude_s=self.initial_rest_exclude.value,
                head_opening_exclude_s=self.head_opening_exclude.value,
                clutch_settings=self.clutch_settings,
            )
            payload["selected_trial"] = self.trial.value - 1
            payload["zoom_to_trial"] = bool(selected.trials and self.zoom_to_trial.value)
            payload["initial_time"] = (
                payload["trials"][self.trial.value - 1]["start"]
                if selected.trials and (self._seek_trial or self.zoom_to_trial.value) else 0
            )
            document = viewer_document(payload)
        except ValueError as error:
            self._show_analysis_error(error)
            return
        self.viewer.value = (
            '<iframe src="about:blank" width="100%" height="900" '
            'title="Head and cursor session playback" sandbox="allow-scripts" '
            'style="display:block;width:100%;height:900px;border:0" '
            f'srcdoc="{html.escape(document, quote=True)}"></iframe>'
        )

    @property
    def exclusions(self):
        return {s.participant: self.exclude_targets.value for s in self.sessions}

    @property
    def clutch_settings(self) -> ClutchSettings:
        return ClutchSettings(**{key: control.value for key, control in self.clutch_controls.items()})

    def _clutch_settings_changed(self, change):
        if not self._resetting_clutch:
            self._refresh_after_analysis_change(change)
            if self.participant.value is not None and self.session.value is not None:
                self._render()

    def _reset_clutch_settings(self, _button):
        self._resetting_clutch = True
        try:
            for key, control in self.clutch_controls.items():
                control.value = getattr(DEFAULT_CLUTCH_SETTINGS, key)
        finally:
            self._resetting_clutch = False
        self._refresh_after_analysis_change(None)
        if self.participant.value is not None and self.session.value is not None:
            self._render()

    def summary_rows(self):
        """Calculate main participant rows using global exclusions, never matching.

        Delegate formulas/aggregation to data.participant_rows; include_dwell
        adds measures without changing the original Fitts/head results.
        """
        return participant_rows(
            self.sessions, self.exclusions, initial_rest_exclude_s=self.initial_rest_exclude.value,
            head_opening_exclude_s=self.head_opening_exclude.value,
            include_dwell=self.include_dwell.value,
            excluded_trial_ids=self._excluded_fitts_trials,
            clutch_settings=self.clutch_settings,
        )

    def _refresh_after_analysis_change(self, _change):
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
        self.fitts_table.value = ""
        self.target_review.children = ()
        self.nodwell_table.value = ""
        self.first_entry_table.value = ""
        self.cursor_paths.children = ()
        self.viewer.value = self.table.value

    def _refresh_table(self):
        self._refresh_target_review()
        rows = self.summary_rows()
        if not rows:
            self.table.value = "No recordings imported."
            self.fitts_table.value = ""
            self.nodwell_table.value = ""
            self.first_entry_table.value = ""
            self.rest_exclusion_note.value = ""
            self.head_exclusion_note.value = ""
            self.cursor_paths.children = ()
            return
        per_session = []
        exhausted = []
        exhausted_directions = []
        head_exhausted = []
        for session in self.sessions:
            row = participant_rows(
                [session], self.exclusions, initial_rest_exclude_s=self.initial_rest_exclude.value,
                head_opening_exclude_s=self.head_opening_exclude.value,
                include_dwell=self.include_dwell.value,
                excluded_trial_ids=self._excluded_fitts_trials,
                clutch_settings=self.clutch_settings,
            )[0]
            row["session"] = session.name
            per_session.append(row)
            if row.get("head_initial_rest_sample_count_session_mean") == 0:
                exhausted.append(f"{session.participant} / {session.name}")
            for direction in ("left", "right", "up", "down"):
                phase = f"range_of_movement.{direction}.hold"
                if (any(s.metadata.get("task_phase") == phase for s in session.samples)
                        and row.get(f"head_range_of_movement_directions_{direction}_sample_count_session_mean") is None):
                    exhausted_directions.append(f"{session.participant} / {session.name}: {direction}")
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
        if exhausted_directions:
            self.rest_exclusion_note.value += (
                "<p><b>No directional hold samples remain after exclusion:</b> " +
                html.escape(", ".join(exhausted_directions)) +
                ". Held-pose means, SDs and offsets are unavailable.</p>"
            )
        head_sections = []
        fitts_sections = []
        descriptions = {
            "Session summary": "Recording counts and durations.",
            "Initial head positions": (
                "Initial head positions use only samples labelled initial_rest.hold, never the preceding "
                "initial_rest.move phase. The opening "
                f"{self.initial_rest_exclude.value:g} seconds of each hold are excluded; without a saved "
                "hold-start time, the first labelled hold sample is the start. The same rule applies to "
                "directional holds. A fully excluded hold contributes no held-pose estimate; move phases "
                "are not trimmed. Saved calibration, playback and pointing results are unchanged. "
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
                "Uses only the labelled movement exercise, not the rest of the recording. "
                f"The opening {self.initial_rest_exclude.value:g} seconds of each hold are excluded from "
                "directional means/SDs, offsets and exercise-wide ranges/SDs. Move samples remain included "
                "in exercise-wide results. A fully excluded directional hold has no held-pose estimate. "
                "Participant results average available session values; individual sessions below are not "
                "additional observations."
            ),
            "Fitts stats": (
                "All measures in this main table, including ID and IDe, use all targets from all of the "
                "participant's files after global first-target exclusions. Set First targets per Fitts file "
                "to 0 and keep every review checkbox ticked to include every recorded target. "
                "Unticked review targets are omitted from all Fitts tables, not head statistics or playback. "
                "Results pool trials rather than averaging session statistics."
            ),
            "Fitts targets and hits": "Counts of completed targets and successful clicks, after leaving out any excluded targets. A miss is a click outside the target.",
            "Dwell behaviour stats": (
                "Uses analysed dwell targets after first-target and review exclusions. "
                "Inside-target visits estimate dwell attempts, not software timer events. "
                "Non-dwell sessions do not contribute zeros. Coverage counts identify each estimate's "
                "contributing targets."
            ),
            "Clutch stats": (
                "Raw boundary-contact and estimated clutch totals, with eligible-target counts and averages. "
                "Uses the same first-target and review exclusions as the main Fitts results. "
                "For older files without saved boundary counts, contacts are estimated from entries into a "
                "configured screen-edge zone in the recorded cursor path. Consecutive edge samples count once; "
                "the first recorded sample in the zone counts as an observed episode. This is approximate "
                "because older cursor coordinates are canvas-local. Off-screen clutches additionally require "
                "synchronised head movement; on-screen clutches require a backward reset followed by faster "
                "recovery along the original approach axis, not an ordinary throw/overshoot correction. "
                "Missing data is unavailable, not zero. Totals pool eligible targets, not session averages. "
                "These measures are not included in no-dwell or first-entry tables."
            ),
            "Head movement stats": (
                "Uses head recordings, including preparation and the movement exercise, not just pointing, "
                f"after excluding the opening {self.head_opening_exclude.value:g} seconds of each recording. "
                "Skipped targets' start-to-click intervals are also excluded. "
                "Set the recording-opening exclusion to 0 for the full recording. "
                "This is not a rest-hold-only table. The opening of every initial-rest/directional hold "
                "is also excluded using the Each hold opening setting; derivatives never cross these gaps. "
                "Movement is measured from the reference pose, normally calibration "
                "but with saved recentering taking priority. Without either, initial rest or the first samples are used. "
                "Near +/-90 degrees pitch, yaw and roll become difficult to separate. "
                "The checks below flag relative pitch of 80 degrees or more "
                "away from zero, and also show ranges during the pointing task only."
            ),
        }
        for label, table_rows in participant_tables(rows).items():
            number = str(_TABLE_NUMBERS[label])
            sections = fitts_sections if label in (
                "Fitts stats", "Fitts targets and hits", "Dwell behaviour stats", "Clutch stats",
            ) else head_sections
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
                condition_rows = fitts_condition_rows(
                    self.sessions, self.exclude_targets.value,
                    excluded_trial_ids=self._excluded_fitts_trials,
                )
                conditions = (
                    f'<details class="fitts-conditions"><summary><b>Table {number}c: Fitts test conditions by file</b></summary>'
                    "<p>One row per participant/Fitts file, using only saved Fitts trials, not head data. "
                    "Lists amplitude settings, widths, block types and observed counts. "
                    "The files do not save the complete settings panel: observed repetitions and distinct "
                    "target positions cannot prove the original settings or that a run was complete. "
                    "For circle tasks, saved amplitude is the configured radius; screen fitting and target "
                    "order determine actual movement distances. Targets per condition/repetition "
                    "are not the same as the number of repetitions per condition. "
                    "Analysed counts apply first-target and review exclusions.</p>" +
                    (self._table_html(condition_rows, "fitts_test_conditions.csv", "Download Fitts conditions CSV",
                                      field_key_title=f"Table {number}c.1: Field key")
                     if condition_rows else "<p>No recorded Fitts trials.</p>") +
                    "</details>"
                )
            notes = ""
            if label == "Initial head positions":
                notes += (
                    "<h4>Angle interpretation</h4>"
                    f"<p>{descriptions[label]}</p>"
                    "<p>Axis and sign key: X points forward, Y left, and Z up. "
                    "<span style=\"color:#2ca02c\">Yaw (about Z): positive left, negative right.</span> "
                    "<span style=\"color:#d62728\">Pitch (about Y): positive up, negative down.</span> "
                    "<span style=\"color:#1f77b4\">Roll (about X): positive left ear down, "
                    "negative right ear down.</span> Solid participant-plot axes are positive; dashed axes are negative. "
                    "The rotation-angle colors are green yaw, red pitch and blue roll.</p>"
                )
            else:
                notes += self._notes_details(descriptions[label])
            notes += self._session_details(session_rows, number) + conditions
            if label == "Head movement stats":
                diagnostics = [row for session in self.sessions
                               if (row := head_range_diagnostics(
                                   session, head_opening_exclude_s=self.head_opening_exclude.value,
                                   exclude_first_targets=self.exclude_targets.value,
                                   initial_rest_exclude_s=self.initial_rest_exclude.value,
                               )) is not None]
                if diagnostics:
                    notes += (
                        f"<details><summary>Table {number}c: Movement range checks by recording "
                        "(analysed recording versus pointing task)</summary>" +
                        self._table_html(diagnostics, "head_range_diagnostics.csv", "Download range diagnostics CSV",
                                         field_key_title=f"Table {number}c.1: Field key") +
                        "<p>Both checks respect head-recording opening, each-hold opening and target-interval exclusions and retain the original reference pose. "
                        "Pointing-task results use samples labelled task_phase = fitts_test; recordings "
                        "without those labels have no pointing-only estimate. A large heading change may also change "
                        "relative pitch/roll when the reference pose is tilted. Drift and unusually large values "
                        "are not automatically removed.</p></details>"
                    )
            section = _table_heading(label)
            if label == "Initial head positions":
                section += initial_pose_reference_diagram()
            sections.append(section + self._table_html(
                table_rows, filename, f"Download {label.lower()} CSV", extra_notes=notes,
                field_key_title=f"Table {number}a: Field key",
            ))
            if label == "Initial head positions":
                sections.append(initial_pose_wireframes(table_rows))
        encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
        self.table.value = (
            f'<a download="participant_summary.csv" href="data:text/csv;base64,{encoded}">'
            "Download all summary measures (CSV)</a>"
            + "".join(head_sections)
        )
        self.fitts_table.value = "".join(fitts_sections)
        self._refresh_cursor_paths()
        self._refresh_selection_tables()

    @staticmethod
    def _session_row(row, session):
        return {"participant": row["participant"], "session": session.name,
                **{key: value for key, value in row.items() if key != "participant"}}

    def _session_details(self, session_rows, number: str):
        sessions = {(row["participant"], row["session"]) for row in session_rows}
        files = "".join(
            f"<li>{html.escape(s.participant)} / {html.escape(s.name)}: " +
            html.escape(", ".join(name for name in (s.head_filename, s.fitts_filename) if name)
                        or "No source filenames") + "</li>"
            for s in self.sessions if (s.participant, s.name) in sessions
        )
        return (
            f'<div class="session-details"><h4>Table {number}b: Individual sessions</h4>'
            '<p>Same fields and settings as above, recalculated for each session. '
            'These inspection rows do not add observations to participant exports.</p>' +
            f"<ul>{files}</ul>" +
            self._table_html(session_rows, None, None, show_field_key=False) +
            "</div>"
        )

    @staticmethod
    def _notes_details(notes):
        return f"<p class=\"analysis-scope\"><b>Analysis scope.</b> {notes}</p>"

    def _fitts_sessions(self):
        return [s for s in self.sessions if s.fitts_filename or s.trials]

    def _refresh_target_review(self):
        candidates = review_targets(self.sessions, self.exclude_targets.value)
        current_ids = {id(t) for s in self.sessions for t in s.trials}
        self._excluded_fitts_trials.intersection_update(current_ids)
        self._target_review_controls = {
            key: control for key, control in self._target_review_controls.items() if key in current_ids
        }
        children = [widgets.HTML(
            "<h3>Target review: more than 3 SD from the average</h3>"
            "<p>Checked = include in every Fitts table; uncheck to remove. "
            "Candidates exceed 3 sample SD in movement time, start-to-click distance, or signed "
            "task-axis endpoint error, pooling each participant's files after first-target exclusions. "
            "Endpoint error additionally checks accuracy across different target distances. "
            "This is a review aid, not proof of error: ordinary misses should remain included. "
            "Means/SDs are fixed before review removals (no iterative rejection); "
            "fewer than two trials or zero SD cannot flag a measure. "
            "Target numbers match the cursor plots. Playback, plots and head statistics remain unchanged. "
            '<a href="https://www.yorku.ca/mack/ijhcs2004.html">Soukoreff &amp; MacKenzie '
            "(2004), footnote 8</a>, recommends investigating unusual movement times/distances; "
            "the endpoint-error check is an additional review criterion.</p>"
        )]
        for candidate in candidates:
            trial_id = id(candidate.trial)
            if trial_id not in self._target_review_controls:
                checkbox = widgets.Checkbox(
                    value=trial_id not in self._excluded_fitts_trials,
                    description="Include", indent=False, layout=widgets.Layout(width="90px"),
                )
                checkbox.observe(
                    lambda change, key=trial_id: self._target_review_changed(key, change["new"]),
                    names="value",
                )
                self._target_review_controls[trial_id] = checkbox
            checkbox = self._target_review_controls[trial_id]
            label = (f"{candidate.participant} / target {candidate.number} / {candidate.session.name} "
                     f"(file target {candidate.file_number}; {candidate.session.fitts_filename})")
            children.append(widgets.HBox([
                checkbox, widgets.HTML(
                    f"<b>{html.escape(label)}</b><br>" +
                    "<br>".join(html.escape(reason) for reason in candidate.reasons)
                ),
            ], layout=widgets.Layout(align_items="center")))
        if not candidates:
            children.append(widgets.HTML("<p>No targets exceed 3 SD.</p>"))
        removed = sum(id(t) in self._excluded_fitts_trials for s in self.sessions
                      for t in s.trials[self.exclude_targets.value:])
        children.append(widgets.HTML(f"<p><b>Review targets removed: {removed}</b>. "
                                     "Choices persist when first-target exclusions change.</p>"))
        restore = widgets.Button(description="Include all reviewed targets",
                                 layout=widgets.Layout(width="220px"))
        restore.on_click(self._restore_review_targets)
        children.append(restore)
        self.target_review.children = tuple(children)

    def _restore_review_targets(self, _button):
        self._excluded_fitts_trials.clear()
        self._target_review_controls.clear()
        self._refresh_after_analysis_change(None)

    def _target_review_changed(self, trial_id, included):
        if included:
            self._excluded_fitts_trials.discard(trial_id)
        else:
            self._excluded_fitts_trials.add(trial_id)
        self._refresh_after_analysis_change(None)

    def _refresh_selection_tables(self):
        labels = {"available": "available after exclusions", "used": "contributing",
                  "unknown": "unknown selection method", "invalid_time": "MT shorter than dwell",
                  "missing_path": "missing cursor path", "no_entry": "no target contact",
                  "unsupported": "unsupported typing geometry"}
        for first_entry, title, output, filename, download in (
            (False, "No-Dwell stats", self.nodwell_table, "no_dwell_stats.csv", "Download No-Dwell stats CSV"),
            (True, "First-entry selection stats", self.first_entry_table, "first_entry_stats.csv",
             "Download first-entry stats CSV"),
        ):
            if not self.sessions:
                output.value = ""
                continue
            try:
                number = str(_TABLE_NUMBERS[title])
                rows, coverage = alternative_selection_rows(
                    self.sessions, self.exclude_targets.value, first_entry=first_entry,
                    excluded_trial_ids=self._excluded_fitts_trials,
                )
                session_rows = []
                for session in self._fitts_sessions():
                    session_result, _ = alternative_selection_rows(
                        [session], self.exclude_targets.value, first_entry=first_entry,
                        excluded_trial_ids=self._excluded_fitts_trials,
                    )
                    session_rows.append(self._session_row(session_result[0], session))
                notes = {
                    "targets_analysed": (
                        "Targets remaining after first-target and review exclusions with estimable first "
                        "contact. Typing geometry, missing paths and no-contact trials are omitted. "
                        "See the coverage table for counts."
                        if first_entry else
                        "Targets remaining after first-target and review exclusions with a known selection "
                        "method and non-negative adjusted movement time. All measures use this same subset; "
                        "see the coverage table for counts."
                    ),
                    "mean_movement_time_s": (
                        "Mean interpolated time from original trial start to first target contact, in seconds. "
                        "Contact uses linear interpolation of cursor samples against the circular target, "
                        "treating the cursor as a point. Sparse samples can miss brief visits. The saved "
                        "start is planned, not necessarily the actual cursor position; using it at time "
                        "zero can affect the first segment. Each trial retains its original start, "
                        "not the preceding trial's estimated first-entry endpoint."
                        if first_entry else
                        "Mean MT after subtracting configured dwell duration for each dwell trial; known "
                        "non-dwell MT is unchanged. Unknown methods and negative adjusted times are omitted. "
                        "This removes configured dwell time, not settling or time spent revisiting a target."
                    ),
                    "throughput_bits_per_s": (
                        "Mean first-entry nominal-width ID divided by mean first-entry MT. Unavailable for zero mean MT. Not click-spread effective-width throughput."
                        if first_entry else "Pooled IDe recalculated on the same valid adjusted-time targets divided by their mean adjusted MT; unavailable for zero mean MT."
                    ),
                }
                if first_entry:
                    notes["nominal_width_px"] = (
                        field_description("nominal_width_px") +
                        " Nominal width replaces effective width because first-entry endpoints generally "
                        "lie on the target perimeter, making click-spread width misleading."
                    )
                    notes["error_rate"] = (
                        "Misses divided by contributing first-entry selections; displayed as a percentage "
                        "(CSV: unrounded fraction). Always zero: only "
                        "trials with an estimated target contact contribute; no-contact trials are listed "
                        "under omissions, not counted as successful selections."
                    )
                    notes["effective_amplitude_px"] = (
                        "Average straight-line distance from the saved planned start position to "
                        "the estimated first target contact, in pixels. The planned start is "
                        "not necessarily the actual cursor position."
                    )
                    for key in rows[0]:
                        if key.startswith("path_"):
                            notes[key] = field_description(key) + " Uses the path only up to first contact."
                scope = (
                    "All measures use the same contributing subset after first-target and review exclusions; "
                    "omissions are counted below. Results pool trials across files, not session averages. "
                ) + (
                    "First-entry reconstruction follows Ching, MacKenzie and Allison (2025), "
                    "doi:10.1145/3694907.3765916; it is not an exact reproduction of their live VR "
                    "sequence-level experiment."
                    if first_entry else "Geometry, endpoint accuracy and path measures use recorded clicks."
                )
                coverage_rows = [
                    {"participant": row["participant"], **{labels[key]: str(value) for key, value in row.items()
                                                          if key != "participant" and (key in ("available", "used")
                                                              or any(r[key] for r in coverage))}}
                    for row in coverage
                ]
                extra_notes = (
                    self._notes_details(html.escape(scope)) +
                    self._session_details(session_rows, number) +
                    f"<h4>Table {number}c: Contributing targets and omissions</h4>" +
                    "<p>Counts after first-target and review exclusions. Omission columns with zero "
                    "targets for every participant are hidden.</p>" +
                    self._table_html(coverage_rows, None, None, show_field_key=False)
                )
                output.value = self._table_html(
                    rows, filename, download, field_notes=notes, extra_notes=extra_notes,
                    field_key_title=f"Table {number}a: Field key",
                )
            except (ValueError, TypeError) as error:
                output.value = f'<p style="color:#b00020">Cannot calculate selection statistics: {html.escape(str(error))}</p>'

    def _refresh_cursor_paths(self):
        participants = sorted({s.participant for s in self.sessions if s.fitts_filename and s.trials})
        children = []
        for participant in participants:
            maximum = len(numbered_targets(self._participant_sessions(participant), 0))
            if participant not in self._cursor_path_controls:
                text = widgets.Text(
                    value=f"1-{maximum}", description="Targets:", continuous_update=False,
                    placeholder="e.g. 1-12 or 1,2,24",
                    layout=widgets.Layout(width="min(95%, 320px)"),
                )
                up = widgets.Button(description="▲ Next", tooltip="Show only the next target path",
                                    layout=widgets.Layout(width="90px"))
                down = widgets.Button(description="▼ Previous", tooltip="Show only the previous target path",
                                      layout=widgets.Layout(width="100px"))
                show_all = widgets.Button(description="All", tooltip="Show every target path",
                                          layout=widgets.Layout(width="60px"))
                control = CursorPathControl(text, up, down, show_all, widgets.HTML(), widgets.HTML(), maximum)
                self._cursor_path_controls[participant] = control
                text.observe(lambda change, p=participant: self._render_cursor_paths(p), names="value")
                up.on_click(lambda _button, p=participant: self._step_cursor_path(p, 1))
                down.on_click(lambda _button, p=participant: self._step_cursor_path(p, -1))
                show_all.on_click(lambda _button, p=participant: self._set_cursor_path_text(p, None))
            control = self._cursor_path_controls[participant]
            if maximum != control.maximum:
                old_full = f"1-{control.maximum}"
                control.maximum = maximum
                if control.text.value.replace(" ", "") in ("", old_full, "all"):
                    self._set_cursor_path_text(participant, None, render=False)
            control.hint.value = (f'<span style="color:#555">{maximum} targets in total; '
                                  "type ranges and/or lists, e.g. 1-12 or 1,2,24</span>")
            self._render_cursor_paths(participant)
            children.append(widgets.VBox([
                widgets.HTML(f"<h4>{html.escape(participant)}</h4>"),
                widgets.HBox([control.text, control.up, control.down, control.show_all],
                             layout=widgets.Layout(flex_flow="row wrap", align_items="center")),
                control.hint, control.output,
            ]))
        self.cursor_paths.children = tuple(children) if children else (
            widgets.HTML("<p>No Fitts targets imported.</p>"),
        )

    def _participant_sessions(self, participant):
        return [s for s in self.sessions if s.participant == participant]

    def _set_cursor_path_text(self, participant, numbers, *, render=True):
        control = self._cursor_path_controls[participant]
        value = format_target_selection(numbers) if numbers else f"1-{control.maximum}"
        if control.text.value == value:
            if render:
                self._render_cursor_paths(participant)
        else:
            # The text observer re-renders when render is wanted; otherwise suppress it.
            control.suppress = not render
            try:
                control.text.value = value
            finally:
                control.suppress = False

    def _step_cursor_path(self, participant, direction):
        """Show one retained target: the next/previous after a single selection,
        otherwise the first/last retained target in the current selection."""
        control = self._cursor_path_controls[participant]
        available = [number for number, *_rest, retained in numbered_targets(
            self._participant_sessions(participant), self.exclude_targets.value) if retained]
        if not available:
            return
        try:
            selected = parse_target_selection(control.text.value, control.maximum)
        except ValueError:
            selected = []
        if len(selected) == 1:
            later = [n for n in available if (n - selected[0]) * direction > 0]
            target = (min(later) if direction > 0 else max(later)) if later else selected[0]
            if target not in available:
                target = available[0] if direction > 0 else available[-1]
        else:
            within = [n for n in available if n in set(selected)] or available
            target = within[0] if direction > 0 else within[-1]
        self._set_cursor_path_text(participant, [target])

    def _render_cursor_paths(self, participant):
        control = self._cursor_path_controls[participant]
        if control.suppress:
            return
        sessions = self._participant_sessions(participant)
        try:
            selection = parse_target_selection(control.text.value, control.maximum)
            control.output.value = cursor_path_plot(sessions, selection, self.exclude_targets.value)
        except (ValueError, TypeError) as error:
            control.output.value = f'<p style="color:#b00020">Cannot draw cursor paths: {html.escape(str(error))}</p>'

    @staticmethod
    def _table_html(rows, filename, download_label, *, field_notes=None, extra_notes="", show_field_key=True,
                    field_key_title="Field key"):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        header_cells = []
        for column in columns:
            description = (field_notes.get(column) if field_notes else None) or field_description(column)
            compact_description = " ".join(description.split())
            if len(compact_description) > 180:
                compact_description = compact_description[:177].rsplit(" ", 1)[0] + "..."
            title = f"{column}: {compact_description}"
            header_cells.append(
                f'<th title="{html.escape(title, quote=True)}" style="padding:4px;overflow-wrap:anywhere;'
                f'font-weight:bold">{html.escape(column_label(column))}</th>'
            )
        header = "".join(header_cells)
        body = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                misses = row.get("misses", row.get("targets_analysed", 0) - row.get("hits", 0))
                text = (
                    "-" if value is None and column in (*DWELL_BEHAVIOUR_FIELDS, *NODWELL_FITTS_FIELDS)
                    else f"{100 * value:.1f}% ({misses}/{row['targets_analysed']})"
                    if column == "error_rate" and value is not None
                    else format_field(column, value)
                )
                cells.append(f"<td>{html.escape(text)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        sizing = (
            "width:max-content;max-width:none;overflow-wrap:normal"
            if filename is None else "width:auto;max-width:100%;overflow-wrap:anywhere"
        )
        table = (
            '<div style="width:100%;max-width:100%;overflow-x:auto">'
            '<table class="report-data" border="1" cellpadding="3" style="border-collapse:collapse;table-layout:auto;'
            f'{sizing};font-size:11px;line-height:1.25">'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )
        download = ""
        if filename is not None:
            encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
            download = f'<a download="{filename}" href="data:text/csv;base64,{encoded}">{download_label}</a>'
        if not show_field_key:
            return download + table
        note_cell = "padding:8px 10px;vertical-align:top;text-align:left;border:1px solid #ccc;"
        field_key = (
            f'<h4>{html.escape(field_key_title)}</h4>'
            '<p>Definitions apply to both participant and individual-session rows. '
            'Blank or &ldquo;-&rdquo; means unavailable, not zero. Measurements use 1 decimal place; '
            'target counts are integers and clutch settings retain their entered precision. Calculations retain precision. '
            'Error rates display as % (misses/targets); CSVs retain unrounded fractions.</p>'
            '<div style="max-width:100%;overflow-x:auto">'
            '<table class="field-key" style="width:100%;border-collapse:collapse;table-layout:auto;'
            'font-size:13px;line-height:1.5">'
            f'<thead><tr><th scope="col" style="{note_cell}font-weight:bold;background:#eee">Field / CSV key</th>'
            f'<th scope="col" style="{note_cell}font-weight:bold;background:#eee">Meaning and calculation</th></tr></thead><tbody>' +
            "".join(
                f'<tr style="background:{"#fafafa" if i % 2 else "#fff"}">'
                f'<th scope="row" style="{note_cell}font-weight:normal;width:30%">'
                f'<b>{html.escape(column_label(c))}</b><br>'
                f'<code style="font-size:11px;overflow-wrap:anywhere;white-space:normal">{html.escape(c)}</code></th>'
                f'<td style="{note_cell}overflow-wrap:break-word">'
                f'{html.escape(field_notes[c] if field_notes and c in field_notes else field_description(c))}</td></tr>'
                for i, c in enumerate(columns)
            ) + '</tbody></table></div>'
        )
        if filename is None:
            return table + field_key
        return (
            download + table +
            '<details class="table-notes" style="overflow-wrap:anywhere">'
            '<summary><b>Table Notes</b></summary>' +
            field_key + extra_notes + '</details>'
        )
