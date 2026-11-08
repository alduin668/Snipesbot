"""Entry persistence. Every mutation is one SQL statement or one transaction."""
from __future__ import annotations

import json
import time
from typing import Iterable, Optional, Sequence

from ..db import Database
from ..models import Entry, FileRef, Kind

# Columns `update()` may change, with the encoder for each.
_ENCODERS = {
    "kind": lambda v: Kind(v).value,
    "sniper": str,
    "sniped": lambda v: json.dumps(list(v)),
    "caption": str,
    "file": lambda v: json.dumps(v.to_dict()) if v else None,
    "extra_files": lambda v: json.dumps([f.to_dict() for f in v]),
    "star_users": lambda v: json.dumps(list(v)),
    "notable_auto": int,
    "notable_override": lambda v: None if v is None else int(v),
    "manually_resolved": int,
    "reacted_kind": lambda v: None if v is None else Kind(v).value,
    "notified_at": lambda v: v,
    "deleted_at": lambda v: v,
}


def _row_to_entry(r) -> Entry:
    return Entry(
        id=r["id"],
        kind=Kind(r["kind"]),
        sniper=r["sniper"],
        sniped=tuple(json.loads(r["sniped"])),
        channel=r["channel"],
        message_ts=r["message_ts"],
        caption=r["caption"],
        file=FileRef.from_dict(json.loads(r["file"])) if r["file"] else None,
        extra_files=tuple(FileRef.from_dict(f) for f in json.loads(r["extra_files"]) if f),
        star_users=tuple(json.loads(r["star_users"])),
        notable_auto=bool(r["notable_auto"]),
        notable_override=None if r["notable_override"] is None else bool(r["notable_override"]),
        manually_resolved=bool(r["manually_resolved"]),
        derived_from=r["derived_from"],
        reacted_kind=Kind(r["reacted_kind"]) if r["reacted_kind"] else None,
        source=r["source"],
        created_at=r["created_at"],
        notified_at=r["notified_at"],
        deleted_at=r["deleted_at"],
    )


class EntryStore:
    def __init__(self, db: Database):
        self.db = db

    # -- writes ----------------------------------------------------------
    def insert(self, e: Entry) -> Entry:
        self.db.execute(
            """INSERT INTO entries (id, kind, sniper, sniped, channel, message_ts, caption, file,
                   extra_files, star_users, notable_auto, notable_override, manually_resolved,
                   derived_from, reacted_kind, source, created_at, notified_at, deleted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                e.id, e.kind.value, e.sniper, json.dumps(list(e.sniped)), e.channel, e.message_ts,
                e.caption, json.dumps(e.file.to_dict()) if e.file else None,
                json.dumps([f.to_dict() for f in e.extra_files]), json.dumps(list(e.star_users)),
                int(e.notable_auto), None if e.notable_override is None else int(e.notable_override),
                int(e.manually_resolved), e.derived_from,
                e.reacted_kind.value if e.reacted_kind else None,
                e.source, e.created_at or time.time(), e.notified_at, e.deleted_at,
            ),
        )
        return self.get(e.id)

    def update(self, entry_id: str, **fields) -> Optional[Entry]:
        if fields:
            unknown = set(fields) - set(_ENCODERS)
            if unknown:
                raise KeyError(f"cannot update column(s): {sorted(unknown)}")
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(
                f"UPDATE entries SET {cols} WHERE id = ?",
                [_ENCODERS[k](v) for k, v in fields.items()] + [entry_id],
            )
        return self.get(entry_id)

    def delete(self, entry_id: str) -> None:
        self.db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    # -- single reads ----------------------------------------------------
    def get(self, entry_id: str) -> Optional[Entry]:
        r = self.db.one("SELECT * FROM entries WHERE id = ?", (entry_id,))
        return _row_to_entry(r) if r else None

    def get_by_message(self, channel: str, ts: str) -> Optional[Entry]:
        """The original entry for a message. Derived entries that share the
        same message are excluded so reactions/edits always resolve to the
        one container entry. Includes soft-deleted entries (so a deleted
        photo isn't re-discovered)."""
        r = self.db.one(
            "SELECT * FROM entries WHERE channel = ? AND message_ts = ? AND derived_from IS NULL",
            (channel, ts),
        )
        return _row_to_entry(r) if r else None

    def derived(self, parent_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM entries WHERE derived_from = ? AND deleted_at IS NULL ORDER BY created_at", (parent_id,)
        )
        return [_row_to_entry(r) for r in rows]

    def for_message(self, channel: str, ts: str) -> list:
        """Parent and derived entries for one message."""
        rows = self.db.query("SELECT * FROM entries WHERE channel = ? AND message_ts = ?", (channel, ts))
        return [_row_to_entry(r) for r in rows]

    # -- lists -----------------------------------------------------------
    def find(self, *, kinds: Optional[Iterable[Kind]] = None, manual: Optional[bool] = None,
             sniper: Optional[str] = None, text: Optional[str] = None, include_deleted: bool = False,
             newest_first: bool = True, limit: Optional[int] = None, offset: int = 0) -> list:
        where, params = [], []
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if kinds is not None:
            ks = [Kind(k).value for k in kinds]
            if not ks:
                return []
            where.append(f"kind IN ({','.join('?' * len(ks))})")
            params += ks
        if manual is not None:
            where.append("manually_resolved = ?")
            params.append(int(manual))
        if sniper:
            where.append("sniper = ?")
            params.append(sniper)
        if text:
            where.append("caption LIKE ? ESCAPE '\\'")
            params.append("%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql = "SELECT * FROM entries"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY message_ts " + ("DESC" if newest_first else "ASC") + ", created_at"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return [_row_to_entry(r) for r in self.db.query(sql, params)]

    def all_active(self) -> list:
        """Every non-deleted entry, oldest first (scoring, stats, sync)."""
        return self.find(newest_first=False)

    def pending(self, *, oldest_first: bool = True) -> list:
        return self.find(kinds=[Kind.UNIDENTIFIED, Kind.LIKELY_SNIPE], newest_first=not oldest_first)

    def special_open(self) -> list:
        return self.find(kinds=[Kind.SPECIAL], newest_first=False)

    def count(self, kinds: Optional[Sequence[Kind]] = None) -> int:
        if kinds is None:
            return self.db.one("SELECT COUNT(*) FROM entries WHERE deleted_at IS NULL")[0]
        ks = [Kind(k).value for k in kinds]
        return self.db.one(
            f"SELECT COUNT(*) FROM entries WHERE deleted_at IS NULL AND kind IN ({','.join('?' * len(ks))})", ks
        )[0]

    def due_for_review_dm(self, *, posted_after_ts: float) -> list:
        """Live, still-unidentified, never-notified photos recent enough to be
        worth a DM. (Historical entries never trigger one.)"""
        rows = self.db.query(
            """SELECT * FROM entries
               WHERE deleted_at IS NULL AND kind = ? AND notified_at IS NULL
                 AND source = 'live' AND derived_from IS NULL AND CAST(message_ts AS REAL) >= ?
               ORDER BY message_ts""",
            (Kind.UNIDENTIFIED.value, posted_after_ts),
        )
        return [_row_to_entry(r) for r in rows]
