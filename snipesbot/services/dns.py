"""Do-not-snipe enforcement."""
from __future__ import annotations

import logging

from ..env import Env
from ..models import Entry, Kind, is_slack_user_id

log = logging.getLogger("snipesbot")


def check(env: Env, entry: Entry) -> int:
    """Warn (once per snipe+target) when a confirmed snipe tags someone on the
    do-not-snipe list. Returns how many warnings were posted."""
    if entry.kind is not Kind.SNIPE or env.slack is None:
        return 0
    posted = 0
    for target in entry.sniped:
        if not is_slack_user_id(target) or not env.store.dns.is_listed(target):
            continue
        # Reserve first: if another process already claimed it, do nothing.
        if not env.store.dns.reserve_warning(entry.channel, entry.message_ts, target, entry.sniper):
            continue
        try:
            reply = env.slack.chat_postMessage(
                channel=env.cfg.channel_id,
                thread_ts=entry.message_ts,
                text=f"that's a sNOPE, please do not snipe <@{target}>",
            )
            env.store.dns.record_warning_message(
                entry.channel, entry.message_ts, target, env.cfg.channel_id, reply["ts"]
            )
            if is_slack_user_id(entry.sniper):
                env.slack.chat_postMessage(
                    channel=entry.sniper,
                    text=(f"Hey — <@{target}> is on the do-not-snipe list. "
                          f"Please delete your recent snipe of them in #snipes."),
                )
            posted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("do-not-snipe warning failed for %s: %s", entry.id, exc)
            env.store.dns.release_warning(entry.channel, entry.message_ts, target)
    return posted


def remove_warnings_for_message(env: Env, channel: str, ts: str) -> int:
    """Delete the bot's warning replies for a snipe message that no longer exists."""
    removed = 0
    for w in env.store.dns.pop_warnings_for_snipe(channel, ts):
        if not (w.get("warning_channel") and w.get("warning_ts")) or env.slack is None:
            continue
        try:
            env.slack.chat_delete(channel=w["warning_channel"], ts=w["warning_ts"])
            removed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("could not delete warning message: %s", exc)
    return removed
