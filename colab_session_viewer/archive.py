"""ZIP discovery, timestamp-based pair suggestions and reusable assignments."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from colab_session_viewer.data import Session

MANIFEST_COLUMNS = ("participant", "session", "head_file", "fitts_file")


@dataclass
class Assignment:
    participant: str = ""
    session: str = ""
    head_file: str = ""
    fitts_file: str = ""


def _safe_path(name: str) -> None:
    if "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise ValueError(f"Use relative ZIP paths with / separators: {name}")


def manifest_csv(assignments: list[Assignment]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=MANIFEST_COLUMNS)
    writer.writeheader()
    for assignment in assignments:
        writer.writerow({key: getattr(assignment, key) for key in MANIFEST_COLUMNS})
    return output.getvalue()


class ArchiveDataset:
    def __init__(self):
        self.recordings: dict[str, Session] = {}
        self.assignments: list[Assignment] = []
        self.warnings: list[str] = []
        self.has_manifest = False
        self.manifest_filename = ""

    @classmethod
    def read(cls, content: bytes):
        dataset = cls()
        with zipfile.ZipFile(io.BytesIO(content)) as archive, tempfile.TemporaryDirectory() as temporary:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("The archive has duplicate member names")
            manifests = []
            for name in sorted(names):
                if "/" in name or "\\" in name or PurePosixPath(name).suffix.lower() != ".csv":
                    continue
                try:
                    reader = csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))
                    columns = reader.fieldnames or []
                    if not set(MANIFEST_COLUMNS).issubset(columns):
                        if name == "manifest.csv":
                            raise ValueError("Manifest columns must include participant, session, head_file, fitts_file")
                        continue
                    assignments = [
                        Assignment(**{key: (row.get(key) or "").strip() for key in MANIFEST_COLUMNS})
                        for row in reader
                    ]
                except (UnicodeDecodeError, csv.Error) as error:
                    if name == "manifest.csv":
                        raise ValueError(f"Cannot read root CSV {name}: {error}") from error
                    dataset.warnings.append(f"Cannot check root CSV {name} for a manifest: {error}")
                    continue
                if not assignments or any(
                    not row.participant or not row.session or not (row.head_file or row.fitts_file)
                    for row in assignments
                ):
                    raise ValueError(f"Invalid manifest {name}: each row needs participant, session and at least one file")
                manifests.append((name, assignments))
            if len(manifests) > 1:
                raise ValueError("Multiple valid root manifests found; keep only the manifest to import")
            dataset.has_manifest = bool(manifests)
            if dataset.has_manifest:
                dataset.manifest_filename, dataset.assignments = manifests[0]
                requested = {name for row in dataset.assignments for name in (row.head_file, row.fitts_file) if name}
            else:
                requested = {name for name in names
                             if PurePosixPath(name).suffix.lower() in (".json", ".csv")
                             and not name.startswith("__MACOSX/")}
            for index, name in enumerate(sorted(requested)):
                _safe_path(name)
                if name not in names:
                    raise ValueError(f"Manifest: missing file {name}")
                suffix = PurePosixPath(name).suffix.lower()
                if suffix not in (".json", ".csv"):
                    raise ValueError(f"Unsupported recording format: {name}")
                raw = archive.read(name)
                kind = None
                try:
                    if suffix == ".json":
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            kind = "head" if "samples" in parsed else "fitts" if "trials" in parsed else None
                    else:
                        columns = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))).fieldnames or []
                        if {"timestamp", "quaternion_w", "quaternion_x", "quaternion_y", "quaternion_z"}.issubset(columns):
                            kind = "head"
                    if kind is None:
                        raise ValueError("Not a head or Fitts recording")
                    path = Path(temporary) / f"recording_{index}{suffix}"
                    path.write_bytes(raw)
                    recording = Session.load(
                        "Unlabelled", PurePosixPath(name).stem,
                        **{f"{kind}_path": path},
                    )
                except (ValueError, TypeError, KeyError, OSError, csv.Error) as error:
                    if dataset.has_manifest:
                        raise ValueError(f"{name}: {error}") from error
                    dataset.warnings.append(f"{name}: {error}")
                    continue
                recording.head_filename = name if kind == "head" else ""
                recording.fitts_filename = name if kind == "fitts" else ""
                dataset.recordings[name] = recording
        if not dataset.recordings:
            detail = "; ".join(dataset.warnings)
            raise ValueError(f"The ZIP contains no usable recordings. {detail}")
        if not dataset.has_manifest:
            dataset.assignments = dataset.suggest_assignments()
        return dataset

    def build_sessions(self, assignments: list[Assignment]) -> list[Session]:
        if not assignments:
            raise ValueError("Include at least one recording")
        sessions = []
        used = set()
        labels = set()
        for row in assignments:
            participant, name = row.participant.strip(), row.session.strip()
            if not participant or not name:
                raise ValueError("Participant and session names are required for every included row")
            if (participant, name) in labels:
                raise ValueError(f"Duplicate participant/session: {participant} / {name}")
            labels.add((participant, name))
            if not row.head_file and not row.fitts_file:
                raise ValueError(f"{participant} / {name}: choose at least one file")
            for path, kind in ((row.head_file, "head"), (row.fitts_file, "fitts")):
                if not path:
                    continue
                if path in used:
                    raise ValueError(f"File assigned more than once: {path}. Clear it from the other row or untick that row.")
                used.add(path)
                if path not in self.recordings or not getattr(self.recordings[path], f"{kind}_filename"):
                    raise ValueError(f"Not a {kind} recording: {path}")
            head = self.recordings.get(row.head_file)
            fitts = self.recordings.get(row.fitts_file)
            session = replace(
                head if head is not None else fitts,
                participant=participant, name=name,
                trials=fitts.trials if fitts is not None else [],
                metadata=fitts.metadata if fitts is not None else {},
                head_filename=row.head_file, fitts_filename=row.fitts_file,
            )
            if head is not None and fitts is not None:
                session.synchronised_samples()
            sessions.append(session)
        return sessions

    def _pair_score(self, head: Session, fitts: Session):
        _, aligned = head.head_times()
        _, _, host, span = fitts.fitts_times()
        if aligned is None or not host:
            return None
        if len([s for s in head.samples if span[0] <= s.host_time <= span[1]]) < 2:
            return None
        start, end = min(s.host_time for s in head.samples), max(s.host_time for s in head.samples)
        overlap = max(0, min(end, span[1]) - max(start, span[0]))
        if overlap <= 0:
            return None
        return overlap / (max(end, span[1]) - min(start, span[0])), -abs(start - span[0])

    def suggest_assignments(self) -> list[Assignment]:
        heads = {name: s for name, s in self.recordings.items() if s.head_filename}
        fitts = {name: s for name, s in self.recordings.items() if s.fitts_filename}
        scores = {(h, f): score for h, head in heads.items() for f, test in fitts.items()
                  if (score := self._pair_score(head, test)) is not None}

        def unique_best(candidates):
            if not candidates:
                return None
            best_score = max(score for _, score in candidates)
            best = [name for name, score in candidates if score == best_score]
            return best[0] if len(best) == 1 else None

        best_heads = {f: unique_best([(h, score) for (h, test), score in scores.items() if test == f])
                      for f in fitts}
        best_fitts = {h: unique_best([(f, score) for (head, f), score in scores.items() if head == h])
                      for h in heads}
        paired_heads = set()
        rows = []
        for f in fitts:
            h = best_heads[f]
            if h is not None and best_fitts[h] == f:
                paired_heads.add(h)
            else:
                if any(test == f for _, test in scores):
                    self.warnings.append(f"{f}: multiple possible timestamp matches; review file assignments.")
                h = ""
            rows.append(Assignment(session=PurePosixPath(f).stem, head_file=h, fitts_file=f))
        rows.extend(Assignment(session=PurePosixPath(h).stem, head_file=h)
                    for h in heads if h not in paired_heads)
        return rows

    def describe(self, name: str) -> str:
        recording = self.recordings[name]
        if recording.head_filename:
            _, aligned = recording.head_times()
            hosts = [s.host_time for s in recording.samples]
            span = (min(hosts), max(hosts)) if aligned is not None else None
            details = f"{len(recording.samples)} head samples"
        else:
            _, _, host, clock_span = recording.fitts_times()
            span = clock_span if host else None
            details = f"{len(recording.trials)} targets"
            access = recording.metadata.get("access_method")
            notes = recording.metadata.get("notes")
            if access:
                details += f"; access: {access}"
            if notes:
                details += f"; notes: {notes}"
        clock = f"host timestamps {span[0]:.1f} to {span[1]:.1f}" if span else "No shared host timestamps; standalone playback"
        return f"{name}: {details}; {clock}"


def load_archive(content: bytes) -> list[Session]:
    """Load labelled archives; manifestless datasets are reviewed before committing."""
    dataset = ArchiveDataset.read(content)
    return dataset.build_sessions(dataset.assignments)
