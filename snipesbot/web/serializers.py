"""Entries and messages as JSON for the UI. All text is plain: the UI escapes it."""
from __future__ import annotations

from .. import classifier
from ..env import Env
from ..models import Entry, Kind
from ..services import people


def photo_url(entry: Entry) -> str | None:
    return f"/api/files/{entry.file.id}" if entry.file else None


def guessed_ids(entry: Entry) -> list:
    """Pre-fill for the 'who was sniped' box: what's already recorded, or for an
    unidentified photo, any @mentions in its caption."""
    if entry.sniped:
        return list(entry.sniped)
    if entry.kind is Kind.UNIDENTIFIED:
        return classifier.extract_mentions(entry.caption, exclude=entry.sniper)
    return []


def entry_view(env: Env, e: Entry, names: dict) -> dict:
    guessed = guessed_ids(e)
    t = e.local_time(env.cfg.tz)
    return {
        "id": e.id,
        "kind": e.kind.value,
        "kind_label": e.kind.label,
        "manually_resolved": e.manually_resolved,
        "notable": e.notable,
        "derived": e.is_derived,
        "sniper": people.display(e.sniper, names),
        "sniper_id": e.sniper,
        "sniped_ids": list(e.sniped),
        "sniped_display": [people.display(u, names) for u in e.sniped],
        "guessed_ids": guessed,
        "guessed_display": [people.display(u, names) for u in guessed],
        "caption": people.render_mentions(e.caption, names),
        "photo_url": photo_url(e),
        "is_video": bool(e.file and e.file.is_video),
        "extra_files": [{"id": f.id, "name": f.name, "is_video": f.is_video} for f in e.extra_files],
        "date_time": t.isoformat(timespec="seconds"),
        "date": t.strftime("%Y-%m-%d"),
        "time": t.strftime("%H:%M:%S"),
        "permalink": env.permalink(e.channel, e.message_ts),
    }
