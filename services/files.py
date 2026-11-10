"""Where the photo files are, whether the database can find them, and how to
reconnect them when it can't."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..env import Env
from ..models import Entry

Progress = Callable[[str], None]


@dataclass
class FileHealth:
    entries_with_files: int = 0
    available: int = 0
    missing: list = field(default_factory=list)      # [(Entry, FileRef)]
    files_on_disk: int = 0
    files_registered: int = 0

    @property
    def missing_count(self) -> int:
        return len(self.missing)


def _file_refs(entry: Entry) -> list:
    return ([entry.file] if entry.file else []) + list(entry.extra_files)


def file_health(env: Env) -> FileHealth:
    """How many tracked photos can actually be served from the archive."""
    h = FileHealth()
    seen = set()
    for entry in env.store.entries.all_active():
        for ref in _file_refs(entry):
            if ref.id in seen:
                continue
            seen.add(ref.id)
            h.entries_with_files += 1
            if env.store.archive.has_file(ref.id):
                h.available += 1
            else:
                h.missing.append((entry, ref))
    files_dir = env.cfg.archive_files_dir
    if files_dir.exists():
        h.files_on_disk = sum(1 for p in files_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    h.files_registered = env.store.db.one("SELECT COUNT(*) FROM archive_files")[0]
    return h


@dataclass
class RelinkReport:
    scanned: int = 0
    linked: int = 0
    still_missing: int = 0

    def summary(self) -> str:
        return (f"scanned {self.scanned} file(s): reconnected {self.linked}; "
                f"{self.still_missing} photo(s) still have no file")


def relink_files(env: Env, directory: Path, progress: Progress = print) -> RelinkReport:
    """Reconnect photo files that exist somewhere on disk but that the database
    can't find. Searches `directory` recursively for files whose name (without
    extension) is either the Slack file id (the archive's naming, e.g. F0123ABC.jpg)
    or an entry id (the old photos/ folder's naming, e.g. 3f2a-....jpg)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a directory")
    by_stem: dict = {}
    report = RelinkReport()
    for p in directory.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            by_stem.setdefault(p.stem, p)
            report.scanned += 1

    for entry, ref in file_health(env).missing:
        src = by_stem.get(ref.id) or (by_stem.get(entry.id) if ref == entry.file else None)
        if src is None:
            report.still_missing += 1
            continue
        env.store.archive.adopt_file(ref.id, src, ref.name or src.name, ref.mimetype)
        report.linked += 1
    progress(f"[relink] {report.summary()}")
    return report


def describe_locations(env: Env) -> list:
    """Human-readable 'where is everything' lines, independent of Slack."""
    cfg = env.cfg
    return [
        f"data directory : {cfg.data_dir.resolve()}",
        f"database       : {cfg.db_path.resolve()}",
        f"photo files    : {cfg.archive_files_dir.resolve()}",
    ]
