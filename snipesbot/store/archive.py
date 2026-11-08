"""
Permanent local archive of #snipes. Slack's free tier makes messages and
files inaccessible after a retention window, so everything the bot sees is
copied here once and read from here ever after.

Messages are rows (indexed, so context lookups are a cheap range query
instead of loading the whole archive); files are plain files on disk named by
Slack file id, with a row recording the original name and mimetype.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Iterable, Optional

from ..db import Database

_CONTEXT_FILTER = (
    "deleted = 0 AND subtype IS NULL AND user IS NOT NULL AND (thread_ts IS NULL OR thread_ts = ts)"
)


def media_files(message: dict) -> list:
    """Image/video attachments on a raw Slack message, in Slack's own order."""
    return [
        f for f in (message.get("files") or [])
        if (f.get("mimetype") or "").startswith(("image/", "video/"))
    ]


class ArchiveStore:
    def __init__(self, db: Database, files_dir: Path):
        self.db = db
        self.files_dir = Path(files_dir)

    # ------------------------------------------------------------------ messages
    def save_messages(self, messages: Iterable[dict]) -> int:
        """Insert or refresh raw Slack messages (each must carry channel and ts)."""
        rows = []
        for m in messages:
            if not m.get("ts") or not m.get("channel"):
                continue
            rows.append((
                m["channel"], m["ts"], m.get("user"), m.get("text") or "", m.get("thread_ts"),
                m.get("subtype"), m.get("bot_id"), json.dumps(m),
            ))
        if not rows:
            return 0
        with self.db.tx():
            self.db.executemany(
                """INSERT INTO messages (channel, ts, user, text, thread_ts, subtype, bot_id, raw)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(channel, ts) DO UPDATE SET
                     user = excluded.user, text = excluded.text, thread_ts = excluded.thread_ts,
                     subtype = excluded.subtype, bot_id = excluded.bot_id, raw = excluded.raw, deleted = 0""",
                rows,
            )
        return len(rows)

    def save_message(self, message: dict) -> None:
        self.save_messages([message])

    def get_message(self, channel: str, ts: str) -> Optional[dict]:
        r = self.db.one("SELECT raw, deleted FROM messages WHERE channel = ? AND ts = ?", (channel, ts))
        return json.loads(r["raw"]) if r else None

    def mark_deleted(self, channel: str, ts: str) -> None:
        self.db.execute("UPDATE messages SET deleted = 1 WHERE channel = ? AND ts = ?", (channel, ts))

    def count(self) -> int:
        return self.db.one("SELECT COUNT(*) FROM messages")[0]

    def purge_message(self, channel: str, ts: str) -> None:
        """Remove a message and its files entirely (test cleanup)."""
        for f in media_files(self.get_message(channel, ts) or {}):
            if f.get("id"):
                self.delete_file(f["id"])
        self.db.execute("DELETE FROM messages WHERE channel = ? AND ts = ?", (channel, ts))

    def before(self, channel: str, ts: str, n: int) -> list:
        """The `n` channel messages immediately before `ts`, oldest first."""
        rows = self.db.query(
            f"SELECT raw FROM messages WHERE channel = ? AND ts < ? AND {_CONTEXT_FILTER} ORDER BY ts DESC LIMIT ?",
            (channel, ts, n),
        )
        return [json.loads(r["raw"]) for r in reversed(rows)]

    def after(self, channel: str, ts: str, n: int) -> list:
        rows = self.db.query(
            f"SELECT raw FROM messages WHERE channel = ? AND ts > ? AND {_CONTEXT_FILTER} ORDER BY ts ASC LIMIT ?",
            (channel, ts, n),
        )
        return [json.loads(r["raw"]) for r in rows]

    def count_after(self, channel: str, ts: str, limit: int) -> int:
        return len(self.after(channel, ts, limit))

    def replies(self, channel: str, thread_ts: str) -> list:
        rows = self.db.query(
            "SELECT raw FROM messages WHERE channel = ? AND thread_ts = ? AND ts != ? AND deleted = 0 ORDER BY ts",
            (channel, thread_ts, thread_ts),
        )
        return [json.loads(r["raw"]) for r in rows]

    def media_messages(self, channel: str, oldest_ts: float = 0.0) -> list:
        """Archived, non-deleted messages carrying at least one image/video."""
        rows = self.db.query(
            """SELECT raw FROM messages
               WHERE channel = ? AND deleted = 0 AND user IS NOT NULL
                 AND CAST(ts AS REAL) >= ? AND raw LIKE '%"mimetype"%'
               ORDER BY ts""",
            (channel, oldest_ts),
        )
        return [m for m in (json.loads(r["raw"]) for r in rows) if media_files(m)]

    # --------------------------------------------------------------------- files
    def file_info(self, file_id: str) -> Optional[dict]:
        r = self.db.one("SELECT * FROM archive_files WHERE file_id = ?", (file_id,))
        if not r:
            return None
        return {"path": self.files_dir / r["filename"], "name": r["name"], "mimetype": r["mimetype"]}

    def file_path(self, file_id: str) -> Optional[Path]:
        info = self.file_info(file_id)
        return info["path"] if info and info["path"].exists() else None

    def knows_file(self, file_id: str) -> bool:
        """True if this file id belongs to something we archived. Guards the file
        proxy so it can't be used to read arbitrary files from the workspace."""
        if not file_id.isalnum():
            return False
        if self.db.one("SELECT 1 FROM archive_files WHERE file_id = ?", (file_id,)):
            return True
        return self.db.one("SELECT 1 FROM messages WHERE raw LIKE ? LIMIT 1", (f'%"id": "{file_id}"%',)) is not None

    def has_file(self, file_id: str) -> bool:
        return self.file_path(file_id) is not None

    @staticmethod
    def _extension(name: str, mimetype: str) -> str:
        return os.path.splitext(name or "")[1] or (".mp4" if (mimetype or "").startswith("video/") else ".jpg")

    def save_file(self, file_id: str, name: str, mimetype: str, data: bytes) -> Path:
        """Store bytes once. Idempotent: an already-archived file is returned as is."""
        existing = self.file_path(file_id)
        if existing:
            return existing
        self.files_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{file_id}{self._extension(name, mimetype)}"
        path = self.files_dir / filename
        tmp = self.files_dir / f".{file_id}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, path)
        self._register(file_id, filename, name, mimetype)
        return path

    def adopt_file(self, file_id: str, source: Path, name: str, mimetype: str) -> Path:
        """Register an existing file on disk (legacy import) under `file_id`."""
        return self.save_file(file_id, name, mimetype, Path(source).read_bytes())

    def _register(self, file_id: str, filename: str, name: str, mimetype: str) -> None:
        self.db.execute(
            """INSERT INTO archive_files (file_id, filename, name, mimetype) VALUES (?,?,?,?)
               ON CONFLICT(file_id) DO UPDATE SET filename = excluded.filename,
                 name = excluded.name, mimetype = excluded.mimetype""",
            (file_id, filename, name or "", mimetype or ""),
        )

    def register_existing(self, file_id: str, filename: str, name: str, mimetype: str) -> None:
        """Record a file already sitting in `files_dir` (legacy import)."""
        self._register(file_id, filename, name, mimetype)

    def delete_file(self, file_id: str) -> None:
        info = self.file_info(file_id)
        if info:
            try:
                info["path"].unlink()
            except OSError:
                pass
        self.db.execute("DELETE FROM archive_files WHERE file_id = ?", (file_id,))
