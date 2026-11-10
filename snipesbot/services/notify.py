"""Proactive review DMs, driven by a timer and stored state.

The old version kept per-photo threading.Timers and an in-memory waiting list,
so a restart silently dropped every pending notification. Now eligibility is
computed from the database (`notified_at`, the photo's age, and how many
messages followed it), so it survives restarts and needs no timers.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..env import Env
from ..slack_io import post_dm
from . import review_cards

log = logging.getLogger("snipesbot")

SETTING_KEY = "dm_review_enabled"


def enabled(env: Env) -> bool:
    return bool(env.store.settings.get(SETTING_KEY, False))


def set_enabled(env: Env, on: bool) -> None:
    env.store.settings.set(SETTING_KEY, bool(on))


def run_once(env: Env, now: Optional[float] = None) -> int:
    """DM the reviewer about unidentified live photos whose context window has
    closed (enough later messages, or the delay elapsed). Returns DMs sent."""
    if not enabled(env) or env.slack is None:
        return 0
    now = now or time.time()
    cfg = env.cfg
    sent = 0
    for entry in env.store.entries.due_for_review_dm(posted_after_ts=now - cfg.review_notify_max_age_seconds):
        waited = now - entry.created_at
        later = env.store.archive.count_after(entry.channel, entry.message_ts, cfg.context_window)
        if waited < cfg.review_notify_delay_seconds and later < cfg.context_window:
            continue
        # Mark first: a crash after sending is better than a DM storm on restart.
        env.store.entries.update(entry.id, notified_at=now)
        ok = post_dm(env, cfg.effective_reviewer, "A snipe photo needs review.",
                     blocks=review_cards.build_blocks(env, entry))
        sent += int(ok)
    return sent
