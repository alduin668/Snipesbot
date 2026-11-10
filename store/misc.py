"""Small stores: settings, do-not-snipe, manual scoring inputs, names, job locks."""
from __future__ import annotations

import functools
import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from typing import Any, Optional

from ..db import Database


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        r = self.db.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


class DnsStore:
    """The do-not-snipe consent list and the warnings posted for violations."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO dns_users (user_id, added_at) VALUES (?, ?)", (user_id, time.time()))

    def remove(self, user_id: str) -> None:
        self.db.execute("DELETE FROM dns_users WHERE user_id = ?", (user_id,))

    def is_listed(self, user_id: str) -> bool:
        return self.db.one("SELECT 1 FROM dns_users WHERE user_id = ?", (user_id,)) is not None

    def list(self) -> list:
        return [r["user_id"] for r in self.db.query("SELECT user_id FROM dns_users ORDER BY added_at")]

    # -- warnings ---------------------------------------------------------
    def reserve_warning(self, channel: str, snipe_ts: str, target: str, sniper: str) -> bool:
        """Claim the right to warn about (snipe, target). False if already claimed,
        so two processes can never double-post the same warning."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO dns_warnings (channel, snipe_ts, target, sniper) VALUES (?,?,?,?)",
            (channel, snipe_ts, target, sniper),
        )
        return cur.rowcount == 1

    def record_warning_message(self, channel: str, snipe_ts: str, target: str,
                               warning_channel: str, warning_ts: str) -> None:
        self.db.execute(
            "UPDATE dns_warnings SET warning_channel = ?, warning_ts = ? WHERE channel = ? AND snipe_ts = ? AND target = ?",
            (warning_channel, warning_ts, channel, snipe_ts, target),
        )

    def release_warning(self, channel: str, snipe_ts: str, target: str) -> None:
        self.db.execute(
            "DELETE FROM dns_warnings WHERE channel = ? AND snipe_ts = ? AND target = ?", (channel, snipe_ts, target)
        )

    def pop_warnings_for_snipe(self, channel: str, snipe_ts: str) -> list:
        with self.db.tx():
            rows = self.db.query("SELECT * FROM dns_warnings WHERE channel = ? AND snipe_ts = ?", (channel, snipe_ts))
            self.db.execute("DELETE FROM dns_warnings WHERE channel = ? AND snipe_ts = ?", (channel, snipe_ts))
        return [dict(r) for r in rows]


class ScoringStore:
    """Manual inputs layered on top of what the bot counted."""

    def __init__(self, db: Database):
        self.db = db

    # -- per-person adjustments (may be fractional or negative) ----------
    def adjustments(self) -> dict:
        return {r["user_id"]: r["value"] for r in self.db.query("SELECT * FROM adjustments")}

    def set_adjustment(self, user_id: str, value: float) -> None:
        if value:
            self.db.execute(
                "INSERT INTO adjustments (user_id, value) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET value = excluded.value",
                (user_id, value),
            )
        else:
            self.db.execute("DELETE FROM adjustments WHERE user_id = ?", (user_id,))

    # -- sniper -> victim count corrections ------------------------------
    def matrix(self) -> dict:
        out: dict = {}
        for r in self.db.query("SELECT * FROM manual_matrix"):
            out.setdefault(r["sniper"], {})[r["victim"]] = r["value"]
        return out

    def set_cell(self, sniper: str, victim: str, value: int) -> None:
        if value:
            self.db.execute(
                """INSERT INTO manual_matrix (sniper, victim, value) VALUES (?,?,?)
                   ON CONFLICT(sniper, victim) DO UPDATE SET value = excluded.value""",
                (sniper, victim, value),
            )
        else:
            self.db.execute("DELETE FROM manual_matrix WHERE sniper = ? AND victim = ?", (sniper, victim))


class HumanStore:
    """The matrix + per-person adjustments a human produced by reading the whole
    chat log. It is the reference the Compare tab holds the bot against; it never
    feeds the live leaderboard (only the Manual matrix / Adjustments do that)."""

    META_KEY = "human_truth_meta"

    def __init__(self, db: Database):
        self.db = db

    def matrix(self) -> dict:
        out: dict = {}
        for r in self.db.query("SELECT * FROM human_matrix"):
            out.setdefault(r["sniper"], {})[r["victim"]] = r["value"]
        return out

    def set_cell(self, sniper: str, victim: str, value: int) -> None:
        if value:
            self.db.execute(
                """INSERT INTO human_matrix (sniper, victim, value) VALUES (?,?,?)
                   ON CONFLICT(sniper, victim) DO UPDATE SET value = excluded.value""",
                (sniper, victim, value),
            )
        else:
            self.db.execute("DELETE FROM human_matrix WHERE sniper = ? AND victim = ?", (sniper, victim))

    def adjustments(self) -> dict:
        return {r["user_id"]: r["value"] for r in self.db.query("SELECT * FROM human_adjustments")}

    def set_adjustment(self, user_id: str, value: float) -> None:
        if value:
            self.db.execute(
                "INSERT INTO human_adjustments (user_id, value) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET value = excluded.value", (user_id, value))
        else:
            self.db.execute("DELETE FROM human_adjustments WHERE user_id = ?", (user_id,))

    def replace_matrix(self, cells: dict) -> None:
        """Swap the whole human matrix for `cells` ({sniper: {victim: n}}) in one transaction."""
        with self.db.tx():
            self.db.execute("DELETE FROM human_matrix")
            self.db.executemany(
                "INSERT INTO human_matrix (sniper, victim, value) VALUES (?,?,?)",
                [(s, v, n) for s, row in cells.items() for v, n in row.items() if n])


class Names(dict):
    """user id -> display name, plus which names are shared by more than one person
    (so they can be told apart instead of showing two identical labels)."""

    @functools.cached_property
    def ambiguous(self) -> frozenset:
        seen: dict = {}
        for name in self.values():
            seen[name.lower()] = seen.get(name.lower(), 0) + 1
        return frozenset(n for n, c in seen.items() if c > 1)


class NameStore:
    """Slack user id -> display name cache."""

    def __init__(self, db: Database):
        self.db = db

    def all(self) -> Names:
        return Names({r["user_id"]: r["name"] for r in self.db.query("SELECT * FROM user_names")})

    def set_many(self, items: dict) -> None:
        if not items:
            return
        with self.db.tx():
            self.db.executemany(
                "INSERT INTO user_names (user_id, name) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
                list(items.items()),
            )

    def set(self, user_id: str, name: str) -> None:
        self.db.execute(
            "INSERT INTO user_names (user_id, name) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
            (user_id, name),
        )

    def get(self, user_id: str) -> Optional[str]:
        r = self.db.one("SELECT name FROM user_names WHERE user_id = ?", (user_id,))
        return r["name"] if r else None


class Busy(RuntimeError):
    """Another process holds the lock."""


class LockStore:
    """Cross-process mutual exclusion with expiry (so a crash can't wedge it)."""

    def __init__(self, db: Database):
        self.db = db
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    def acquire(self, name: str, ttl: float) -> bool:
        now = time.time()
        with self.db.tx():
            row = self.db.one("SELECT owner, expires_at FROM locks WHERE name = ?", (name,))
            if row and row["expires_at"] > now and row["owner"] != self.owner:
                return False
            self.db.execute(
                """INSERT INTO locks (name, owner, expires_at) VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET owner = excluded.owner, expires_at = excluded.expires_at""",
                (name, self.owner, now + ttl),
            )
        return True

    def release(self, name: str) -> None:
        self.db.execute("DELETE FROM locks WHERE name = ? AND owner = ?", (name, self.owner))

    def holder(self, name: str) -> Optional[dict]:
        """Who holds `name` right now (None if free or expired). `mine` is true when it's
        this process, so a UI can tell its own job from the bot's scheduled pass."""
        row = self.db.one("SELECT owner, expires_at FROM locks WHERE name = ?", (name,))
        if row and row["expires_at"] > time.time():
            return {"owner": row["owner"], "mine": row["owner"] == self.owner, "expires_at": row["expires_at"]}
        return None

    @contextmanager
    def held(self, name: str, ttl: float = 3600):
        if not self.acquire(name, ttl):
            raise Busy(f"'{name}' is already running (started by another process).")
        try:
            yield
        finally:
            self.release(name)


class TestMessageStore:
    """Messages posted by the self-test, so they can be cleaned up afterwards."""
    __test__ = False  # not a pytest class

    def __init__(self, db: Database):
        self.db = db

    def add(self, channel: str, ts: str, label: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO test_messages (channel, ts, label) VALUES (?,?,?)", (channel, ts, label))

    def all(self) -> list:
        return [dict(r) for r in self.db.query("SELECT * FROM test_messages ORDER BY ts")]

    def clear(self) -> None:
        self.db.execute("DELETE FROM test_messages")
