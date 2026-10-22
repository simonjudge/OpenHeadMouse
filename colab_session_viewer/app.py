"""ipywidgets shell: a single browser viewer, repeated/bulk imports and summaries."""

from __future__ import annotations

import base64
import csv
import html
import tempfile
import zipfile
from pathlib import Path

import ipywidgets as widgets
from IPython.display import HTML, clear_output, display

from colab_session_viewer.data import (
    Session, format_number, initial_pose_reference_diagram, initial_pose_wireframes, participant_rows, participant_tables,
    payload_json, playback_payload, summary_csv, column_label, table_sources, head_range_diagnostics,
)
from colab_session_viewer.archive import ArchiveDataset, Assignment, load_archive, manifest_csv

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


class NotebookViewer:
    def __init__(self):
        self.sessions: list[Session] = []
        self.exclusions: dict[str, int] = {}
        self._changing = False
        self.participant = widgets.Dropdown(description="Participant:")
        self.session = widgets.Dropdown(description="Session:")
        self.viewer = widgets.Output(layout=widgets.Layout(width="100%"))
        self.message = widgets.HTML()
        self.table = widgets.HTML()
        self.exclusion_controls = widgets.VBox()
        self.import_participant = widgets.Text(description="Participant:", placeholder="P01")
        self.import_session = widgets.Text(description="Session:", placeholder="Test 1")
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
        self.widget = widgets.VBox([
            widgets.HTML("<h3>Import recordings</h3><p>Add a head file, a Fitts file, or a matching pair. "
                         "Repeat for additional tests/participants. Names must be unique within each participant.</p>"),
            widgets.HBox([self.import_participant, self.import_session]),
            widgets.HBox([self.head_upload, self.fitts_upload, add]),
            widgets.HTML("<p>Upload a ZIP with or without a <code>manifest.csv</code>. Without a manifest, "
                         "recordings are detected and paired by shared timestamps. Review the suggestions, "
                         "label participants/sessions, then import and download your manifest.</p>"),
            widgets.HBox([self.archive_upload, bulk]),
            self.archive_review, self.manifest_downloads, self.message,
            widgets.HTML("<h2>Session viewer</h2>"),
            widgets.HBox([self.participant, self.session]), self.viewer,
            widgets.HTML("<h3>Participant exclusions</h3><p>Exclude the first N completed targets "
                         "<b>from each Fitts file</b> for this participant, in chronological order. "
                         "No repetitions/conditions, source files or playback frames are removed. "
                         "Head and pre-test measures remain unchanged.</p>"),
            self.exclusion_controls,
            widgets.HTML("<h3>Participant summaries</h3><p>One row per participant in each table, pooling retained "
                         "trials across all imported sessions using the desktop FittsStats formulas. "
                         "Times are seconds, angles degrees, distances pixels; error_rate is a fraction. "
                         "Blank values mean unavailable. Pre-test columns are means across available sessions.</p>"),
            self.table,
        ], layout=widgets.Layout(width="100%"))
        self._show_empty()

    def show(self):
        display(self.widget)
        return self

    def _show_empty(self):
        with self.viewer:
            clear_output(wait=True)
            display(HTML("<p>Import recordings above, then choose a participant and session here.</p>"))

    def _notify(self, text, error=False):
        color = "#b00020" if error else "#256329"
        self.message.value = f'<p role="status" style="color:{color}">{html.escape(text)}</p>'

    def add_session(self, participant, name, *, head_path=None, fitts_path=None):
        session = Session.load(participant, name, head_path=head_path, fitts_path=fitts_path)
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
            payload_json(playback_payload(session))
        self.sessions.extend(sessions)
        self._changing = True
        try:
            self.participant.options = sorted({s.participant for s in self.sessions})
            self.participant.value = sessions[-1].participant
            self.session.options = [s.name for s in self.sessions if s.participant == self.participant.value]
            self.session.value = sessions[-1].name
        finally:
            self._changing = False
        self._rebuild_exclusions()
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
                session = Session.load(self.import_participant.value, self.import_session.value, **paths)
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
        def update_details(_=None):
            names = [controls[key].value for key in ("head_file", "fitts_file") if controls[key].value]
            controls["details"].value = "<br>".join(html.escape(dataset.describe(name)) for name in names)
        for key in ("head_file", "fitts_file"):
            controls[key].observe(update_details, names="value")
        update_details()
        self._review_rows.append(controls)
        return widgets.VBox([
            widgets.HBox([controls["include"], controls["participant"], controls["session"]]),
            controls["head_file"], controls["fitts_file"], controls["details"],
        ], layout=widgets.Layout(border="1px solid #ccc", padding="8px", margin="4px 0"))

    def _confirm_archive(self, _):
        try:
            if self._pending_dataset is None:
                raise ValueError("No ZIP is waiting for review")
            assignments = [
                Assignment(**{key: controls[key].value.strip()
                              for key in ("participant", "session", "head_file", "fitts_file")})
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
        document = viewer_document(playback_payload(selected))
        iframe = ('<iframe title="Head and cursor session playback" '
                  'sandbox="allow-scripts" style="width:100%;height:1150px;border:0" '
                  f'srcdoc="{html.escape(document, quote=True)}"></iframe>')
        with self.viewer:
            clear_output(wait=True)
            display(HTML(iframe))

    def _rebuild_exclusions(self):
        controls = []
        for participant in sorted({s.participant for s in self.sessions}):
            control = widgets.BoundedIntText(
                value=self.exclusions.get(participant, 0), min=0, max=2**31 - 1,
                description=f"{participant}:",
                style={"description_width": "initial"},
            )
            def changed(change, participant=participant):
                self.exclusions[participant] = change["new"]
                self._refresh_table()
            control.observe(changed, names="value")
            controls.append(control)
        self.exclusion_controls.children = tuple(controls)

    def summary_rows(self):
        return participant_rows(self.sessions, self.exclusions)

    def _refresh_table(self):
        rows = self.summary_rows()
        if not rows:
            self.table.value = "No recordings imported."
            return
        sections = []
        descriptions = {
            "Session summary": "Recording counts and durations.",
            "Initial head positions": (
                "Initial-rest hold yaw is shown explicitly as absolute (provider frame) and relative "
                "to saved calibration heading. Absolute does not mean a universal compass heading: "
                "gravity does not determine yaw. Calibration yaw is the heading of the mean saved "
                "calibration quaternion. Wireframes use calibration mean yaw with initial-rest pitch/roll. "
                "Pitch/roll are gravity-referenced tilt estimates for xIMU3/AirPods, subject to sensor fusion "
                "and mounting/fit. Webcam pitch/roll are camera-referenced, not gravity-referenced. "
                "xIMU3 yaw may be magnetically referenced when enabled and reliable; AirPods heading is arbitrary. Missing "
                "calibration leaves relative yaw and plots unavailable, but absolute yaw remains available."
            ),
            "Range of movement stats": (
                "Only range_of_movement task data. Task angle ranges include move and hold samples; "
                "directional pose/extremes use hold phases. No other session data contributes. "
                "Offsets use initial rest as a reference; _session_mean columns average available sessions."
            ),
            "Fitts stats": "Pooled pointing and path measures after per-participant target exclusions.",
            "Fitts targets and hits": "Target counts, hits and error rate after target exclusions.",
            "Head movement stats": (
                "Full recordings, including preparation and ROM tasks, not just mouse use. "
                "Speed/acceleration use quaternion geodesic rotation. Ranges use Euler angles extracted "
                "from q_neutral^-1 * q_sample, unwrapped within each file; participant ranges are the "
                "largest within-file span per axis, never pooled extrema across different reference frames. "
                "Euler ranges are coupled and can be singular near +/-90 deg pitch. Diagnostics below "
                "flag samples at |relative pitch| >= 80 deg and distinguish labelled Fitts-only ranges."
            ),
        }
        for label, table_rows in participant_tables(rows).items():
            filename = label.lower().replace(" ", "_") + ".csv"
            sections.append(
                f"<h3>{label}</h3><p>{descriptions[label]}</p>" +
                self._table_html(table_rows, filename, f"Download {label.lower()} CSV")
            )
            sources = table_sources(self.sessions, label)
            sections.append(
                '<div style="font-size:11px;margin:6px 0"><b>Source files:</b><ul>' +
                "".join(f"<li>{html.escape(participant)}: " +
                        (", ".join(html.escape(name) for name in names) if names else "No contributing files") +
                        "</li>" for participant, names in sorted(sources.items())) +
                "</ul></div>"
            )
            if label == "Initial head positions":
                sections.append(initial_pose_reference_diagram())
                sections.append(initial_pose_wireframes(table_rows))
            if label == "Head movement stats":
                diagnostics = [row for session in self.sessions
                               if (row := head_range_diagnostics(session)) is not None]
                if diagnostics:
                    sections.append(
                        "<details><summary>Range diagnostics by recording (full versus Fitts-only)</summary>" +
                        self._table_html(diagnostics, "head_range_diagnostics.csv", "Download range diagnostics CSV") +
                        "<p>Fitts-only means task_phase == fitts_test; unlabelled head-only recordings "
                        "have no Fitts-only estimate. Large heading changes can couple into pitch/roll "
                        "when neutral is tilted; no automatic drift removal or outlier deletion is applied.</p></details>"
                    )
        encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
        self.table.value = (
            f'<a download="participant_summary.csv" href="data:text/csv;base64,{encoded}">Download summary CSV (all metrics)</a>'
            + "".join(sections)
        )

    @staticmethod
    def _table_html(rows, filename, download_label):
        columns = list(dict.fromkeys(key for row in rows for key in row))
        header = "".join(
            f'<th title="{html.escape(c, quote=True)}" style="padding:4px;overflow-wrap:anywhere;'
            f'font-weight:600">{html.escape(column_label(c))}</th>' for c in columns
        )
        body = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                text = format_number(value)
                cells.append(f"<td>{html.escape(text)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
        return (
            f'<a download="{filename}" href="data:text/csv;base64,{encoded}">{download_label}</a>'
            '<div style="width:100%;max-width:100%;overflow-x:auto">'
            '<table border="1" cellpadding="3" style="border-collapse:collapse;table-layout:fixed;'
            'width:100%;font-size:11px;line-height:1.25;overflow-wrap:anywhere">'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )
