"""
Who is on the roster.

The matrices, adjustments and Compare grid used to include every cached name
in the whole Slack workspace, which made them enormous. The roster is now only:

  * people @mentioned in the snipes channel (messages, and entries' victims)
  * people who post in the snipes channel, and its members (cached from Slack)
  * people on the do-not-snipe list

plus anyone who already has a manual/human number or adjustment, so an edit
can never silently disappear from the page.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from ..classifier import MENTION_RE
from ..env import Env

log = logging.getLogger("snipesbot")

MEMBERS_KEY = "channel_members"
MEMBERS_AT_KEY = "channel_members_refreshed_at"


def refresh_channel_members(env: Env, *, force: bool = False, max_age: float = 24 * 3600) -> int:
    """Cache the snipes channel's member ids (needs channels:read / groups:read).
    Returns how many were stored, 0 if skipped or it failed (posters still count)."""
    last = env.store.settings.get(MEMBERS_AT_KEY)
    if env.slack is None or (not force and last and time.time() - last < max_age):
        return 0
    found, cursor = [], None
    try:
        while True:
            resp = env.slack.conversations_members(channel=env.cfg.channel_id, cursor=cursor, limit=200)
            found += list(resp.get("members", []) or [])
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list #snipes members (needs channels:read): %s", exc)
        return 0
    try:
        bot = env.bot_user_id()
    except Exception:  # noqa: BLE001
        bot = None
    found = [u for u in dict.fromkeys(found) if u != bot]
    env.store.settings.set(MEMBERS_KEY, found)
    env.store.settings.set(MEMBERS_AT_KEY, time.time())
    return len(found)


def participants(env: Env) -> set:
    """Everyone who belongs in the snipes channel's world (see module docstring)."""
    store, channel = env.store, env.cfg.channel_id
    people: set = set(store.dns.list())
    people |= set(store.settings.get(MEMBERS_KEY) or [])
    for e in store.entries.all_active():
        if e.channel == channel:
            people.add(e.sniper)
            people.update(e.sniped)
    rows = store.db.query(
        "SELECT user, text FROM messages WHERE channel = ? AND deleted = 0 AND bot_id IS NULL", (channel,))
    for r in rows:
        if r["user"]:
            people.add(r["user"])
        people.update(MENTION_RE.findall(r["text"] or ""))
    people.discard("")
    people.discard(None)
    return people


def roster_ids(env: Env, *extra: Iterable[str]) -> set:
    """participants + everyone with a manual cell/adjustment (+ any `extra` ids)."""
    s = env.store
    ids = participants(env)
    manual = s.scoring.matrix()
    ids |= set(manual) | {v for row in manual.values() for v in row}
    ids |= set(s.scoring.adjustments())
    for more in extra:
        ids |= set(more)
    return ids
