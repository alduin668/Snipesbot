"""
Everything that changes an entry's classification goes through `apply_state`.

The old code repeated the same ten-line "update the store, update the dict,
sync the reaction, sync the log, check do-not-snipe" block in a dozen places,
each slightly different. Here there is one place that does it, and the
public functions below only decide *what* the new state is:

    record_photo          a new photo (live or historical)
    handle_edit           a caption was edited in Slack
    resolve_pending       a person resolves a pending photo
    set_kind              the webapp's "set this entry to X"
    resolve_via_thread_reply
    record_star / set_notable
    create_special / dismiss_special
    handle_deleted        the Slack message was deleted
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Iterable, Optional

from .. import classifier
from ..env import Env
from ..models import (PENDING_KINDS, RESOLVABLE_KINDS, Entry, FileRef, Kind,
                      new_id)
from ..slack_io import sync_reaction
from ..store import media_files
from . import dns, people

log = logging.getLogger("snipesbot")


class InvalidTransition(ValueError):
    """The requested change makes no sense (e.g. a snipe with nobody sniped)."""


class EntryNotFound(LookupError):
    """No entry with that id."""


# ---------------------------------------------------------------------------
# the one transition path
# ---------------------------------------------------------------------------

def _default_sniped(entry: Entry, kind: Kind) -> tuple:
    if kind is Kind.SNIPE:
        return entry.sniped or tuple(classifier.extract_mentions(entry.caption, exclude=entry.sniper))
    if kind is Kind.LIKELY_SNIPE:
        return entry.sniped
    return ()


def apply_state(env: Env, entry_id: str, kind: Kind | str, *, sniped: Optional[Iterable[str]] = None,
                manual: bool = True, caption: Optional[str] = None, allow_incomplete: bool = False) -> Entry:
    """Move an entry to `kind`, then bring Slack and do-not-snipe in line.

    `sniped=None` keeps the sensible default for the kind (see _default_sniped);
    pass a list to override. `manual=True` locks the decision against automatic
    re-classification; automatic callers pass False.

    A snipe normally needs someone sniped. `allow_incomplete=True` (used when Zeke
    marks a photo with a Slack reaction, where there's nowhere to say who) lets it
    through anyway: it stays a snipe, scores nothing, and shows in the review queue.
    """
    kind = Kind(kind)
    with env.store.db.tx():
        entry = env.store.entries.get(entry_id)
        if entry is None:
            raise EntryNotFound(f"no entry {entry_id}")
        if sniped is not None:
            new_sniped = tuple(dict.fromkeys(s for s in sniped if s))
        else:
            new_sniped = _default_sniped(entry, kind)
        if kind is Kind.SNIPE and not new_sniped and not allow_incomplete:
            raise InvalidTransition("a snipe needs at least one sniped user")
        changes = {"kind": kind, "sniped": new_sniped}
        if manual:
            changes["manually_resolved"] = True
        if caption is not None:
            changes["caption"] = caption
        updated = env.store.entries.update(entry_id, **changes)
    return _after_change(env, updated)


def _after_change(env: Env, entry: Entry) -> Entry:
    """Side effects, outside the DB transaction. A Slack failure never undoes a
    saved decision; it just leaves `reacted_kind` stale so the next sync repairs it."""
    if env.slack is not None and not entry.is_derived and entry.reacted_kind is not entry.kind:
        if sync_reaction(env, entry.channel, entry.message_ts, entry.kind):
            entry = env.store.entries.update(entry.id, reacted_kind=entry.kind)
    if entry.kind is Kind.SNIPE:
        dns.check(env, entry)
    return entry


# ---------------------------------------------------------------------------
# new photos
# ---------------------------------------------------------------------------

def record_photo(env: Env, message: dict, *, source: str = "live") -> tuple:
    """Create (or refresh) the entry for a Slack message carrying media.

    Returns (entry, created). `message` is a raw Slack message that also has
    `channel`. Idempotent: an already-tracked message just has its caption and
    file references refreshed (an edit made while the bot was offline is only
    ever seen here).
    """
    channel, ts, user = message.get("channel"), message.get("ts"), message.get("user")
    files = media_files(message)
    if not (channel and ts and user and files):
        return None, False

    text = message.get("text") or ""
    refs = [FileRef.from_slack(f) for f in files]

    existing = env.store.entries.get_by_message(channel, ts)
    if existing is not None:
        changes = {}
        if existing.caption != text:
            changes["caption"] = text
        if existing.file != refs[0]:
            changes["file"] = refs[0]
        if existing.extra_files != tuple(refs[1:]):
            changes["extra_files"] = tuple(refs[1:])
        if changes:
            existing = env.store.entries.update(existing.id, **changes)
        return existing, False

    result = classifier.classify(text, user, snipe_words=env.cfg.snipe_keywords,
                                 snope_words=env.cfg.snope_keywords)
    entry = Entry(
        id=new_id(), kind=result.kind, sniper=user, sniped=result.sniped, channel=channel,
        message_ts=ts, caption=text, file=refs[0], extra_files=tuple(refs[1:]),
        source=source, created_at=time.time(),
    )
    try:
        entry = env.store.entries.insert(entry)
    except sqlite3.IntegrityError:  # another process recorded it first
        return env.store.entries.get_by_message(channel, ts), False
    return _after_change(env, entry), True


# ---------------------------------------------------------------------------
# edits
# ---------------------------------------------------------------------------

def _repair_reaction(env: Env, entry: Entry) -> Entry:
    """Bring a stale bot reaction in line (no-op when it already matches)."""
    if entry.reacted_kind is not entry.kind and not entry.is_derived:
        return _after_change(env, entry)
    return entry


def reclassify(env: Env, entry: Entry, text: str) -> Entry:
    """Re-run the detector on `text` for an automatic entry. Manual decisions
    are never changed; only their cached caption is refreshed. Either way a
    stale Slack reaction is repaired."""
    if entry.manually_resolved:
        if entry.caption != text:
            entry = env.store.entries.update(entry.id, caption=text)
        return _repair_reaction(env, entry)
    result = classifier.classify(text, entry.sniper, snipe_words=env.cfg.snipe_keywords,
                                 snope_words=env.cfg.snope_keywords)
    if result.kind is entry.kind and result.sniped == entry.sniped:
        if entry.caption != text:
            entry = env.store.entries.update(entry.id, caption=text)
        return _repair_reaction(env, entry)
    return apply_state(env, entry.id, result.kind, sniped=result.sniped, manual=False, caption=text)


def handle_edit(env: Env, channel: str, ts: str, new_text: str) -> Optional[Entry]:
    entry = env.store.entries.get_by_message(channel, ts)
    if entry is None or entry.deleted:
        return None
    return reclassify(env, entry, new_text)


# ---------------------------------------------------------------------------
# human decisions
# ---------------------------------------------------------------------------

def resolve_pending(env: Env, entry_id: str, kind: Kind | str, sniped: Optional[Iterable[str]] = None) -> Optional[Entry]:
    """Resolve a photo in the review queue (pending, or a snipe with nobody sniped yet)
    to snipe / snope / NAS. None if it's no longer in the queue (someone else got
    there first) or the kind isn't resolvable."""
    kind = Kind(kind)
    entry = env.store.entries.get(entry_id)
    if entry is None or entry.deleted or not entry.needs_review or kind not in RESOLVABLE_KINDS:
        return None
    if kind is not Kind.SNIPE and sniped is None:
        sniped = ()
    return apply_state(env, entry_id, kind, sniped=sniped)


def set_kind(env: Env, entry_id: str, kind: Kind | str, sniped: Optional[Iterable[str]] = None) -> Entry:
    """The webapp / admin path: any entry to any kind."""
    kind = Kind(kind)
    if sniped is None and kind not in (Kind.SNIPE, Kind.LIKELY_SNIPE):
        sniped = ()
    return apply_state(env, entry_id, kind, sniped=sniped)


def resolve_via_thread_reply(env: Env, channel: str, parent_ts: str, reply_text: str) -> bool:
    """Zeke replies "#snipe @someone" (or "#snipe nas") in a photo's thread."""
    entry = env.store.entries.get_by_message(channel, parent_ts)
    if entry is None or entry.deleted or entry.manually_resolved:
        return False

    if entry.pending and classifier.says_nas(reply_text) and not classifier.extract_mentions(reply_text):
        apply_state(env, entry.id, Kind.NOT_A_SNIPE, sniped=())
        return True

    result = classifier.classify(f"{entry.caption} {reply_text}", entry.sniper,
                                 snipe_words=env.cfg.snipe_keywords, snope_words=env.cfg.snope_keywords)
    if result.kind in PENDING_KINDS:
        return False  # the reply didn't settle anything; leave it in the queue
    apply_state(env, entry.id, result.kind, sniped=result.sniped)
    return True


# ---------------------------------------------------------------------------
# stars / notable
# ---------------------------------------------------------------------------

def record_star(env: Env, channel: str, ts: str, user: str, added: bool) -> Optional[Entry]:
    """A star reaction was added or removed. Notable if Zeke starred it, or
    enough other people did."""
    if added:
        people.ensure_name(env, user)
    with env.store.db.tx():
        entry = env.store.entries.get_by_message(channel, ts)
        if entry is None:
            return None
        stars = set(entry.star_users)
        stars.add(user) if added else stars.discard(user)
        zeke = env.cfg.zeke_user_id
        notable = zeke in stars or len(stars - {zeke}) >= env.cfg.notable_min_other_stars
        return env.store.entries.update(entry.id, star_users=tuple(sorted(stars)), notable_auto=notable)


def set_notable(env: Env, entry_id: str, notable: Optional[bool]) -> Optional[Entry]:
    """Pin the notable flag by hand (None clears the override, back to star-driven)."""
    if env.store.entries.get(entry_id) is None:
        return None
    return env.store.entries.update(entry_id, notable_override=notable)


# ---------------------------------------------------------------------------
# special entries
# ---------------------------------------------------------------------------

SPECIAL_CHILD_KINDS = (Kind.SNIPE, Kind.SNOPE, Kind.NOT_A_SNIPE, Kind.INCORRECT_SNIPE)


def create_special(env: Env, parent_id: str, kind: Kind | str, sniper: str, sniped: Iterable[str]) -> Optional[Entry]:
    """Split a new, independent entry out of a "special" photo. Sniper/sniped may
    be real Slack ids or free text (e.g. "deer")."""
    kind = Kind(kind)
    parent = env.store.entries.get(parent_id)
    if kind not in SPECIAL_CHILD_KINDS or parent is None:
        return None
    sniper = (sniper or "").strip()
    if not sniper:
        return None
    if kind in (Kind.SNIPE, Kind.SNOPE):
        victims = tuple(dict.fromkeys(s.strip() for s in (sniped or []) if s and s.strip()))
        if not victims:
            return None
    else:
        victims = ()
    child = Entry(
        id=new_id(), kind=kind, sniper=sniper, sniped=victims, channel=parent.channel,
        message_ts=parent.message_ts, caption=parent.caption, file=parent.file,
        extra_files=parent.extra_files, manually_resolved=True, derived_from=parent.id,
        source=parent.source, created_at=time.time(),
    )
    child = env.store.entries.insert(child)
    return _after_change(env, child)


def dismiss_special(env: Env, entry_id: str) -> Entry:
    """Looked at it, nothing worth logging: same as any other NAS."""
    return apply_state(env, entry_id, Kind.NOT_A_SNIPE, sniped=())


# ---------------------------------------------------------------------------
# deletions
# ---------------------------------------------------------------------------

def handle_deleted(env: Env, channel: str, ts: str) -> list:
    """The Slack message was deleted. The entry (and any entries derived from it)
    stop counting and leave every queue, the bot's do-not-snipe warning replies
    are removed, and (by default) the archived files are deleted too, so a
    photo someone asked to have removed doesn't linger on disk."""
    env.store.archive.mark_deleted(channel, ts)
    now = time.time()
    entries = [e for e in env.store.entries.for_message(channel, ts) if not e.deleted]
    for e in entries:
        env.store.entries.update(e.id, deleted_at=now)
    dns.remove_warnings_for_message(env, channel, ts)
    if env.cfg.purge_files_on_delete:
        file_ids = {f.id for e in entries for f in ([e.file] if e.file else []) + list(e.extra_files)}
        raw = env.store.archive.get_message(channel, ts) or {}
        file_ids |= {f.get("id") for f in media_files(raw) if f.get("id")}
        for fid in file_ids:
            env.store.archive.delete_file(fid)
    return entries
