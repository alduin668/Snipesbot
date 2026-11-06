"""
/donotsnipe slash command and cleanup of the bot's DNS warning reply when an
offending snipe message is deleted.
"""
import re

import config
from storage import json_store, user_names

_MENTION_RE = re.compile(r"<@([A-Z0-9]+)")


def _resolve_target(text: str, client):
    """Turn whatever Zeke typed after /donotsnipe into a Slack user id.

    Slash-command text is NOT auto-linkified the way real message text is --
    `<@U12345>` only shows up if Slack's own @-mention autocomplete dropdown
    was actually used while typing the command. If someone just types
    "@bob" (or "bob") and Slack didn't convert it, the old code silently
    fell through and left `target` as the caller themselves -- which is why
    /donotsnipe always ended up adding Zeke no matter what was typed.

    This tries, in order: a real `<@ID>` mention, the display-name cache
    (cheap, no API call), then a live Slack users.list lookup by display
    name / real name / username."""
    text = text.strip()
    if not text:
        return None

    m = _MENTION_RE.search(text)
    if m:
        return m.group(1)

    name = text.lstrip("@").strip().lower()
    if not name:
        return None

    for uid, cached_name in user_names.get_cached_names().items():
        if cached_name and cached_name.lower() == name:
            return uid

    try:
        resp = client.users_list()
    except Exception as e:
        print(f"[donotsnipe] users_list lookup failed: {e}")
        return None

    for member in resp.get("members", []):
        profile = member.get("profile", {})
        candidates = {
            (profile.get("display_name") or "").lower(),
            (profile.get("real_name") or "").lower(),
            (member.get("name") or "").lower(),
        }
        if name in candidates:
            return member.get("id")

    return None


def handle_donotsnipe_command(ack, command, client):
    ack()
    user = command["user_id"]
    text = command.get("text", "").strip()
    channel = command["channel_id"]

    target = user
    if text:
        resolved = _resolve_target(text, client)
        if not resolved:
            client.chat_postEphemeral(
                channel=channel,
                user=user,
                text=(
                    f"Couldn't find a Slack user matching `{text}`. Pick them from "
                    f"the @-mention popup as you type, or add them from the "
                    f"webapp's Do Not Snipe tab instead."
                ),
            )
            return
        target = resolved

    if target != user and user != config.ZEKE_USER_ID:
        client.chat_postEphemeral(
            channel=channel,
            user=user,
            text="You can only run /donotsnipe on yourself.",
        )
        return

    json_store.add_dns(target)
    user_names.ensure_cached(target, client)
    client.chat_postEphemeral(
        channel=channel,
        user=user,
        text=f"<@{target}> added to the do-not-snipe list.",
    )


def handle_message_deleted(event: dict, client):
    channel = event.get("channel")
    deleted_ts = event.get("deleted_ts") or event.get("previous_message", {}).get("ts")
    if not deleted_ts:
        return

    warnings = json_store.pop_dns_warnings_for_snipe(channel, deleted_ts)
    for warning in warnings:
        try:
            client.chat_delete(
                channel=warning["warning_channel"],
                ts=warning["warning_ts"],
            )
        except Exception as e:
            print(f"[dns] failed to delete warning message: {e}")
