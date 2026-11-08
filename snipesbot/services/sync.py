"""
The one maintenance job. It replaces `backfill`, `recount`, `archive_sync`
and the xlsx `resync`, which were four scripts doing overlapping slices of
the same work:

    1. archive   pull messages/files/replies from Slack into the local archive
    2. discover  record any archived photo that has no entry yet
    3. recount   re-run the detector on automatic entries (manual decisions
                 are never touched) and repair any stale Slack reaction
    4. deletions notice tracked photos that vanished from Slack while offline

Safe to run any time, from the bot's timer, the CLI, or the webapp: a
cross-process lock stops two runs overlapping, and nothing here makes a Slack
call per entry unless that entry actually changed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Callable, Optional

from ..env import Env
from ..models import Kind
from ..slack_io import sync_reaction
from . import archiver, entries as entry_service

log = logging.getLogger("snipesbot")

WATERMARK_KEY = "last_sync_ts"
LOCK_NAME = "sync"
_OVERLAP_SECONDS = 300          # re-scan a little before the last pass, never miss the boundary
_MAX_DELETIONS_PER_PASS = 10    # a bigger "everything vanished" is a Slack problem, not deletions


@dataclass
class SyncStats:
    archived: int = 0
    replies: int = 0
    discovered: int = 0
    reclassified: int = 0
    reactions_repaired: int = 0
    deleted: int = 0
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"archived {self.archived} messages (+{self.replies} replies), "
                f"{self.discovered} new photo(s), {self.reclassified} reclassified, "
                f"{self.reactions_repaired} reaction(s) repaired, {self.deleted} deleted")


def season_start_ts(env: Env) -> float:
    return datetime.combine(env.cfg.season_start, dtime.min, tzinfo=env.cfg.tz).timestamp()


def run_sync(env: Env, *, since: Optional[float] = None, full: bool = False,
             repair_reactions: bool = False, progress: Callable[[str], None] = print) -> SyncStats:
    """Run one sync pass. Raises `store.Busy` if another pass is running."""
    default_run = since is None and not full
    started = time.time()
    if full:
        since = season_start_ts(env)
    elif since is None:
        since = env.store.settings.get(WATERMARK_KEY) or season_start_ts(env)

    stats = SyncStats()
    with env.store.locks.held(LOCK_NAME, ttl=2 * 3600):
        progress(f"[sync] scanning #snipes since {datetime.fromtimestamp(since, env.cfg.tz).isoformat(timespec='seconds')}")

        res = archiver.sync_from_slack(env, since, progress)
        stats.archived, stats.replies = res["archived"], res["replies"]

        _discover(env, since, stats, progress)
        _recount(env, stats, repair_reactions, progress)
        _detect_deletions(env, since, res["seen"], stats, progress)

        if default_run:
            env.store.settings.set(WATERMARK_KEY, started - _OVERLAP_SECONDS)
    progress(f"[sync] done: {stats.summary()}")
    return stats


def _discover(env: Env, since: float, stats: SyncStats, progress) -> None:
    for message in env.store.archive.media_messages(env.cfg.channel_id, since):
        entry, created = entry_service.record_photo(env, message, source="historical")
        if created:
            stats.discovered += 1
            progress(f"[sync] new photo {entry.message_ts}: {entry.kind.value}")


def _recount(env: Env, stats: SyncStats, repair_reactions: bool, progress) -> None:
    """Re-run the detector on automatic entries. Slack is only asked about an
    entry whose bot reaction is known to be stale (or for every entry when
    `repair_reactions` asks to verify everything)."""
    for entry in env.store.entries.all_active():
        if entry.is_derived or entry.kind is Kind.SPECIAL:
            continue
        before = entry
        if not entry.manually_resolved:
            entry = entry_service.reclassify(env, entry, entry.caption)   # also repairs a stale reaction
            if entry.kind is not before.kind or entry.sniped != before.sniped:
                stats.reclassified += 1
                progress(f"[sync] {entry.id}: {before.kind.value} -> {entry.kind.value}")
        stale = entry.reacted_kind is not entry.kind
        if env.slack is not None and (stale or repair_reactions):
            if sync_reaction(env, entry.channel, entry.message_ts, entry.kind) and stale:
                entry = env.store.entries.update(entry.id, reacted_kind=entry.kind)
        if before.reacted_kind is not before.kind and entry.reacted_kind is entry.kind:
            stats.reactions_repaired += 1


def _detect_deletions(env: Env, since: float, seen: set, stats: SyncStats, progress) -> None:
    """A photo deleted in Slack while the bot was offline is only noticed here.
    Only looks inside Slack's retention window (older messages are legitimately
    absent from history) and refuses to act on a suspiciously large batch."""
    if not seen:
        return
    floor = max(since, time.time() - (env.cfg.retention_days - 1) * 86400)
    missing = [e for e in env.store.entries.all_active()
               if not e.is_derived and e.channel == env.cfg.channel_id
               and e.posted_at >= floor and e.message_ts not in seen]
    if not missing:
        return
    if len(missing) > _MAX_DELETIONS_PER_PASS:
        note = f"{len(missing)} tracked photos missing from Slack; not treating as deletions"
        stats.notes.append(note)
        progress(f"[sync] WARNING: {note}")
        return
    for e in missing:
        entry_service.handle_deleted(env, e.channel, e.message_ts)
        stats.deleted += 1
        progress(f"[sync] {e.message_ts} no longer exists in Slack; removed")
