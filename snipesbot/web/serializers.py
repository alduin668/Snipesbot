"""Entries and messages as JSON for the UI. All text is plain: the UI escapes it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .. import classifier
from ..env import Env
from ..models import Entry, Kind
from ..services import people

INCOMPLETE_LABEL = "Snipe \u2014 who was sniped?"


@dataclass
class ViewContext:
    """What it takes to turn entries into JSON, gathered once per response:
    resolved names and the current do-not-snipe list."""
    env: Env
    names: dict
    dns: frozenset

    @classmethod
    def build(cls, env: Env, entries: Iterable[Entry] = ()) -> "ViewContext":
        ids: list = []
        for e in entries:
            ids.append(e.sniper)
            ids.extend(e.sniped)
            ids.extend(people.ids_in(e.caption))
        people.ensure_names(env, ids)
        return cls(env, env.store.names.all(), frozenset(env.store.dns.list()))


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


def entry_view(ctx: ViewContext, e: Entry) -> dict:
    env, names = ctx.env, ctx.names
    guessed = guessed_ids(e)
    no_points = [u for u in e.sniped if u in ctx.dns]
    t = e.local_time(env.cfg.tz)
    return {
        "id": e.id,
        "kind": e.kind.value,
        "kind_label": INCOMPLETE_LABEL if e.incomplete else e.kind.label,
        "incomplete": e.incomplete,
        "needs_review": e.needs_review,
        "manually_resolved": e.manually_resolved,
        "notable": e.notable,
        "derived": e.is_derived,
        "sniper": people.display(e.sniper, names),
        "sniper_id": e.sniper,
        "sniped_ids": list(e.sniped),
        "sniped_display": [people.display(u, names) for u in e.sniped],
        "guessed_ids": guessed,
        "guessed_display": [people.display(u, names) for u in guessed],
        # A snipe of someone on the do-not-snipe list still counts as a snipe, but
        # earns the sniper and the target nothing.
        "no_points_ids": no_points,
        "no_points_display": [people.display(u, names) for u in no_points],
        "caption": people.render_text(e.caption, names),
        "photo_url": photo_url(e),
        "is_video": bool(e.file and e.file.is_video),
        "extra_files": [{"id": f.id, "name": f.name, "is_video": f.is_video} for f in e.extra_files],
        "date_time": t.isoformat(timespec="seconds"),
        "date": t.strftime("%Y-%m-%d"),
        "time": t.strftime("%H:%M:%S"),
        "permalink": env.permalink(e.channel, e.message_ts),
    }
