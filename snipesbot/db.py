"""
SQLite storage. WAL mode with a busy timeout lets the bot, the webapp, and
CLI jobs write to the same database safely; every read-modify-write runs in
a single `BEGIN IMMEDIATE` transaction, so two processes can no longer
overwrite each other's changes the way whole-file JSON rewrites could.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Sequence

_MIGRATIONS = [
    # --- v1: initial schema -------------------------------------------------
    """
    CREATE TABLE entries (
        id                TEXT PRIMARY KEY,
        kind              TEXT NOT NULL,
        sniper            TEXT NOT NULL,
        sniped            TEXT NOT NULL DEFAULT '[]',
        channel           TEXT NOT NULL,
        message_ts        TEXT NOT NULL,
        caption           TEXT NOT NULL DEFAULT '',
        file              TEXT,
        extra_files       TEXT NOT NULL DEFAULT '[]',
        star_users        TEXT NOT NULL DEFAULT '[]',
        notable_auto      INTEGER NOT NULL DEFAULT 0,
        notable_override  INTEGER,
        manually_resolved INTEGER NOT NULL DEFAULT 0,
        derived_from      TEXT,
        reacted_kind      TEXT,
        source            TEXT NOT NULL DEFAULT 'live',
        created_at        REAL NOT NULL,
        notified_at       REAL,
        deleted_at        REAL
    );
    CREATE UNIQUE INDEX entries_message ON entries(channel, message_ts) WHERE derived_from IS NULL;
    CREATE INDEX entries_kind   ON entries(kind);
    CREATE INDEX entries_parent ON entries(derived_from);
    CREATE INDEX entries_ts     ON entries(message_ts);

    CREATE TABLE messages (
        channel   TEXT NOT NULL,
        ts        TEXT NOT NULL,
        user      TEXT,
        text      TEXT NOT NULL DEFAULT '',
        thread_ts TEXT,
        subtype   TEXT,
        bot_id    TEXT,
        deleted   INTEGER NOT NULL DEFAULT 0,
        raw       TEXT NOT NULL,
        PRIMARY KEY (channel, ts)
    );
    CREATE INDEX messages_thread ON messages(channel, thread_ts);

    CREATE TABLE archive_files (
        file_id  TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        name     TEXT NOT NULL DEFAULT '',
        mimetype TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE settings   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE dns_users  (user_id TEXT PRIMARY KEY, added_at REAL NOT NULL);
    CREATE TABLE dns_warnings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        channel         TEXT NOT NULL,
        snipe_ts        TEXT NOT NULL,
        target          TEXT NOT NULL,
        sniper          TEXT NOT NULL,
        warning_channel TEXT,
        warning_ts      TEXT,
        UNIQUE (channel, snipe_ts, target)
    );
    CREATE TABLE adjustments  (user_id TEXT PRIMARY KEY, value REAL NOT NULL);
    CREATE TABLE manual_matrix (
        sniper TEXT NOT NULL, victim TEXT NOT NULL, value INTEGER NOT NULL,
        PRIMARY KEY (sniper, victim)
    );
    CREATE TABLE user_names   (user_id TEXT PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE locks (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);
    CREATE TABLE test_messages (
        channel TEXT NOT NULL, ts TEXT NOT NULL, label TEXT NOT NULL,
        PRIMARY KEY (channel, ts)
    );
    """,
    # --- v2: the human-provided "truth" used by the Compare tab ----------------
    """
    CREATE TABLE human_matrix (
        sniper TEXT NOT NULL, victim TEXT NOT NULL, value INTEGER NOT NULL,
        PRIMARY KEY (sniper, victim)
    );
    CREATE TABLE human_adjustments (user_id TEXT PRIMARY KEY, value REAL NOT NULL)
    """,
]

SCHEMA_VERSION = len(_MIGRATIONS)


class Database:
    def __init__(self, path):
        self.path = str(path) if str(path) == ":memory:" else str(Path(path).resolve())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._migrate()

    # -- connections -----------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # autocommit; we BEGIN ourselves
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
            self._local.depth = 0
        return c

    @contextmanager
    def tx(self):
        """A write transaction. Re-entrant: nested calls join the outer one."""
        c = self.conn()
        if self._local.depth == 0:
            c.execute("BEGIN IMMEDIATE")
        self._local.depth += 1
        try:
            yield c
        except BaseException:
            self._local.depth -= 1
            if self._local.depth == 0:
                c.execute("ROLLBACK")
            raise
        else:
            self._local.depth -= 1
            if self._local.depth == 0:
                c.execute("COMMIT")

    # -- convenience -----------------------------------------------------
    def query(self, sql: str, params: Sequence = ()) -> list:
        return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence = ()):
        return self.conn().execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        return self.conn().execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence]) -> None:
        self.conn().executemany(sql, rows)

    # -- schema ----------------------------------------------------------
    def _migrate(self) -> None:
        c = self.conn()
        current = c.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema v{current} is newer than this code (v{SCHEMA_VERSION}); upgrade SnipesBot."
            )
        for version in range(current, SCHEMA_VERSION):
            with self.tx() as conn:
                for statement in _split(_MIGRATIONS[version]):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version + 1}")

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


def _split(script: str) -> list:
    return [s.strip() for s in script.split(";") if s.strip()]
