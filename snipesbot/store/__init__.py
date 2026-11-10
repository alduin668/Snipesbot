"""Data access. `Store` bundles every repository over one Database."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from .archive import ArchiveStore, media_files
from .entries import EntryStore
from .misc import (Busy, DnsStore, HumanStore, LockStore, NameStore, ScoringStore,
                   SettingsStore, TestMessageStore)

__all__ = ["Store", "Busy", "media_files"]


class Store:
    def __init__(self, db: Database, files_dir: Path):
        self.db = db
        self.entries = EntryStore(db)
        self.archive = ArchiveStore(db, files_dir)
        self.settings = SettingsStore(db)
        self.dns = DnsStore(db)
        self.scoring = ScoringStore(db)
        self.names = NameStore(db)
        self.human = HumanStore(db)
        self.locks = LockStore(db)
        self.tests = TestMessageStore(db)

    @classmethod
    def open(cls, cfg) -> "Store":
        return cls(Database(cfg.db_path), cfg.archive_files_dir)
