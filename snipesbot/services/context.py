"""Neighbouring messages and thread replies for a photo, read from the archive.

Nothing here is stored on the entry: context is a range query over the
archive, so it is never stale, survives restarts, and "load more" is just a
bigger window.
"""
from __future__ import annotations

from ..env import Env
from ..models import Entry, FileRef
from ..store import media_files
from . import people


def shape_message(raw: dict, names: dict) -> dict:
    """A raw archived message as the UI wants it (plain text; the UI escapes)."""
    return {
        "user": people.display(raw.get("user"), names),
        "user_id": raw.get("user"),
        "text": people.render_text(raw.get("text"), names),
        "raw": raw.get("text") or "",             # Slack's own text, for Slack's own cards
        "ts": raw.get("ts"),
        "files": [
            {"id": f.get("id"), "name": f.get("name") or "",
             "is_video": (f.get("mimetype") or "").startswith("video/")}
            for f in media_files(raw)
        ],
    }


def _names_for(env: Env, raws: list) -> dict:
    """Names for everyone who wrote, or is mentioned in, these messages."""
    ids = []
    for raw in raws:
        ids.append(raw.get("user"))
        ids.extend(people.ids_in(raw.get("text")))
    people.ensure_names(env, [i for i in ids if i])
    return env.store.names.all()


def around(env: Env, entry: Entry, before: int | None = None, after: int | None = None) -> dict:
    n = env.cfg.context_window
    before = n if before is None else before
    after = n if after is None else after
    pre = env.store.archive.before(entry.channel, entry.message_ts, max(0, before))
    post = env.store.archive.after(entry.channel, entry.message_ts, max(0, after))
    names = _names_for(env, pre + post)
    return {"before": [shape_message(m, names) for m in pre],
            "after": [shape_message(m, names) for m in post]}


def replies(env: Env, entry: Entry) -> list:
    raws = env.store.archive.replies(entry.channel, entry.message_ts)
    names = _names_for(env, raws)
    return [shape_message(m, names) for m in raws]


def context_texts(env: Env, entry: Entry) -> list:
    """Raw text of the neighbouring messages (for the reviewer's 'guessed' hint)."""
    n = env.cfg.context_window
    return [m.get("text", "") for m in
            env.store.archive.before(entry.channel, entry.message_ts, n)
            + env.store.archive.after(entry.channel, entry.message_ts, n)]


def file_ref_dict(f: FileRef) -> dict:
    return {"id": f.id, "name": f.name, "is_video": f.is_video}
