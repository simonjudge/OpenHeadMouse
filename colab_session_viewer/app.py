"""ipywidgets shell: a single browser viewer, repeated/bulk imports and summaries."""

from __future__ import annotations

import base64
import csv
import html
import io
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import ipywidgets as widgets
from IPython.display import HTML, clear_output, display

from colab_session_viewer.data import (
    Session, participant_rows, payload_json, playback_payload, summary_csv,
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


def load_archive(content: bytes) -> list[Session]:
    """Read an explicit manifest; never extract archive paths onto disk."""
    sessions = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive, tempfile.TemporaryDirectory() as temporary:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("The archive has duplicate member names")
        if "manifest.csv" not in names:
            raise ValueError("The ZIP must contain manifest.csv at its root")
        manifest = csv.DictReader(io.StringIO(archive.read("manifest.csv").decode("utf-8-sig")))
        required = {"participant", "session", "head_file", "fitts_file"}
        if not required.issubset(manifest.fieldnames or []):
            raise ValueError("Manifest columns must include participant, session, head_file, fitts_file")
        for number, row in enumerate(manifest, 2):
            paths = {}
            original_names = {}
            for column, argument in (("head_file", "head_path"), ("fitts_file", "fitts_path")):
                name = (row.get(column) or "").strip()
                if not name:
                    continue
                if "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                    raise ValueError(f"Manifest row {number}: use relative ZIP paths with / separators")
                if name not in names:
                    raise ValueError(f"Manifest row {number}: missing file {name}")
                paths[argument] = _write_upload(Path(temporary), name, archive.read(name), argument)
                original_names[column] = PurePosixPath(name).name
            session = Session.load(row["participant"], row["session"], **paths)
            session.head_filename = original_names.get("head_file", "")
            session.fitts_filename = original_names.get("fitts_file", "")
            sessions.append(session)
    if not sessions:
        raise ValueError("The archive manifest has no sessions")
    return sessions


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
        add = widgets.Button(description="Add session", button_style="primary")
        add.on_click(self._import_session)
        bulk = widgets.Button(description="Import dataset ZIP")
        bulk.on_click(self._import_archive)
        self.participant.observe(self._participant_changed, names="value")
        self.session.observe(self._session_changed, names="value")
        self.widget = widgets.VBox([
            widgets.HTML("<h2>Session viewer</h2>"),
            widgets.HBox([self.participant, self.session]), self.viewer,
            self.message,
            widgets.HTML("<h3>Import recordings</h3><p>Add a head file, a Fitts file, or a matching pair. "
                         "Repeat for additional tests/participants. Names must be unique within each participant.</p>"),
            widgets.HBox([self.import_participant, self.import_session]),
            widgets.HBox([self.head_upload, self.fitts_upload, add]),
            widgets.HTML("<p>For several data sets at once, upload a ZIP with a root "
                         "<code>manifest.csv</code> (participant, session, head_file, fitts_file).</p>"),
            widgets.HBox([self.archive_upload, bulk]),
            widgets.HTML("<h3>Participant exclusions</h3><p>Exclude the first N completed targets "
                         "<b>from each Fitts file</b> for this participant, in chronological order. "
                         "No repetitions/conditions, source files or playback frames are removed. "
                         "Head and pre-test measures remain unchanged.</p>"),
            self.exclusion_controls,
            widgets.HTML("<h3>Collated participant summary</h3><p>One row per participant, pooling retained "
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
            display(HTML("<p>Import recordings below, then choose a participant and session here.</p>"))

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
            uploaded = _uploads(self.archive_upload)
            if not uploaded:
                raise ValueError("Choose a dataset ZIP first")
            self._add_sessions(load_archive(uploaded[0][1]))
        except (ValueError, TypeError, KeyError, OSError, csv.Error, zipfile.BadZipFile) as error:
            self._notify(f"Dataset import failed: {error}", error=True)

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
        columns = list(dict.fromkeys(key for row in rows for key in row))
        header = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
        body = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column)
                text = "" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
                cells.append(f"<td>{html.escape(text)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        encoded = base64.b64encode(summary_csv(rows).encode("utf-8-sig")).decode("ascii")
        self.table.value = (
            f'<a download="participant_summary.csv" href="data:text/csv;base64,{encoded}">Download summary CSV</a>'
            '<div style="overflow-x:auto"><table border="1" cellpadding="5" style="border-collapse:collapse">'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )
