"""Plain functions behind the Slack listeners, so they can be tested without Bolt."""
from __future__ import annotations

import logging
import re

from ..env import Env
from ..services import archiver, entries as svc, people
from ..store import media_files
from . import review_ui

log = logging.getLogger("snipesbot")
_SNIPE_REPLY_RE = re.compile(r"#snipe\b", re.IGNORECASE)


def handle_message_event(env: Env, event: dict) -> None:
    cfg = env.cfg
    if event.get("bot_id"):
        return
    subtype, channel = event.get("subtype"), event.get("channel")

    if subtype == "message_deleted":
        if channel == cfg.channel_id:
            ts = event.get("deleted_ts") or (event.get("previous_message") or {}).get("ts")
            if ts:
                svc.handle_deleted(env, channel, ts)
        return

    if subtype == "message_changed":
        new = dict(event.get("message") or {})
        if channel == cfg.channel_id and new.get("ts"):
            new["channel"] = channel
            archiver.archive_message(env, new)
            svc.handle_edit(env, channel, new["ts"], new.get("text", ""))
        return

    if channel != cfg.channel_id:
        return

    # Archive first, thread replies included: nothing downstream depends on
    # Slack still having the message later.
    archiver.archive_message(env, event)

    thread_ts = event.get("thread_ts")
    if thread_ts and thread_ts != event.get("ts") and event.get("user") == cfg.zeke_user_id:
        if _SNIPE_REPLY_RE.search(event.get("text", "")):
            svc.resolve_via_thread_reply(env, channel, thread_ts, event.get("text", ""))
        return  # Zeke's thread replies are corrections, never new photos

    if media_files(event) and event.get("user") and event.get("ts"):
        svc.record_photo(env, event, source="live")


def handle_donotsnipe(env: Env, command: dict) -> None:
    cfg = env.cfg
    user, text, channel = command["user_id"], (command.get("text") or "").strip(), command["channel_id"]
    target = user
    if text:
        target = people.resolve_target(env, text)
        if not target:
            review_ui._reply(env, channel, user,
                             f"Couldn't find a Slack user matching `{text}`. Pick them from the @-mention "
                             f"popup as you type, or add them from the webapp's Do Not Snipe tab.")
            return
    if target != user and user != cfg.zeke_user_id:
        review_ui._reply(env, channel, user, "You can only run /donotsnipe on yourself.")
        return
    env.store.dns.add(target)
    people.ensure_name(env, target)
    review_ui._reply(env, channel, user, f"<@{target}> added to the do-not-snipe list.")
