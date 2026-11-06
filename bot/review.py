"""
Review tool for photos the automatic detector couldn't classify -- surfaced
two ways, both using the same Block Kit message + modal:

  1. `/reviewsnipes` -- pulls up the oldest pending photo, with buttons.
  2. A proactive DM to REVIEWER_USER_ID, once turned on with `/reviewmode on`,
     sent automatically as soon as a photo's short context-capture window
     finishes (bot/detection.py calls back into here). Only true "?"
     (unidentified) photos trigger this DM -- a likely_snipe (orange,
     already has a guessed @mention) is lower-urgency and easier to resolve
     with a single reaction or a later `/reviewsnipes` pass, so it no longer
     interrupts anyone.

Nothing here ever guesses on someone's behalf -- the "guessed mention(s)"
line is just a hint pulled from the logged before/photo/after text.
"""
import json as _json

from slack_sdk import WebClient

import config
from storage import json_store
from bot import detection

# Standalone client for background notifications fired from a timer thread,
# outside of any Bolt request/event context.
_bg_client = WebClient(token=config.SLACK_BOT_TOKEN)


# ---------- building the review card ----------

def _guessed_mentions(entry: dict) -> list:
    combined = " ".join(
        [m.get("text", "") for m in entry.get("context_before", [])]
        + [entry.get("photo_text", "")]
        + [m.get("text", "") for m in entry.get("context_after", [])]
    )
    return detection._extract_mentions(combined, exclude_user=entry["sniper"])


def _review_blocks(entry: dict, client) -> list:
    """Sequential layout: each before-context message as its own line, then
    the actual photo as a rendered image (not just a text placeholder), then
    each after-context message, then the identification controls -- so it
    reads top-to-bottom the same way the conversation actually happened."""
    link = detection._message_permalink(client, entry["channel"], entry["message_ts"])
    guessed = _guessed_mentions(entry)

    blocks = []
    for m in entry.get("context_before", []):
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"<@{m['user']}>: {m['text'] or '_(no text)_'}"}})

    file_obj = entry.get("file") or {}
    file_id = file_obj.get("id")
    is_video = file_obj.get("mimetype", "").startswith("video/")
    if file_id and not is_video:
        blocks.append({
            "type": "image",
            "slack_file": {"id": file_id},
            "alt_text": "snipe photo",
        })
    elif file_id and is_video:
        # Slack's Block Kit "image" element can't render video -- it's
        # already visible in the thread/channel itself, so just note it's
        # there rather than trying (and failing) to inline it here.
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": "🎥 _video attached -- view it in #snipes_"}]})
    caption = entry.get("photo_text")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": f"📷 <@{entry['sniper']}>" + (f": {caption}" if caption else " _(no caption)_")}]})

    for m in entry.get("context_after", []):
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"<@{m['user']}>: {m['text'] or '_(no text)_'}"}})

    kind_label = "Likely snipe (bare mention, unconfirmed)" if entry.get("kind") == "likely_snipe" else "Unidentified"
    blocks.append({"type": "section", "text": {"type": "mrkdwn",
        "text": f"*Identify this photo* -- _{kind_label}_\n<{link}|View in #snipes>"}})
    blocks.append({"type": "actions", "block_id": f"review_{entry['id']}", "elements": [
        {"type": "button", "action_id": "review_mark_snipe", "style": "primary",
         "text": {"type": "plain_text", "text": "It's a snipe"}, "value": entry["id"]},
        {"type": "button", "action_id": "review_mark_snope",
         "text": {"type": "plain_text", "text": "It's a snope"}, "value": entry["id"]},
        {"type": "button", "action_id": "review_mark_nas",
         "text": {"type": "plain_text", "text": "Not a snipe"}, "value": entry["id"]},
        {"type": "button", "action_id": "review_skip",
         "text": {"type": "plain_text", "text": "Skip for now"}, "value": entry["id"]},
    ]})
    if guessed:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"Guessed mention(s) from context: {', '.join(f'<@{g}>' for g in guessed)}"}]})
    return blocks


# ---------- proactive DM ----------

def maybe_notify_reviewer(entry_id: str):
    """Proactive DM ping -- unidentified ("?") only. likely_snipe entries
    still show up in /reviewsnipes and can be one-tap confirmed via a
    reaction, but they no longer interrupt the reviewer with a DM."""
    if not json_store.get_setting("dm_review_enabled", False):
        return
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry or entry.get("kind") != "unidentified":
        return
    try:
        _bg_client.chat_postMessage(
            channel=config.REVIEWER_USER_ID,
            text="A snipe photo needs review.",
            blocks=_review_blocks(entry, _bg_client),
        )
    except Exception as e:
        print(f"[review] failed to DM reviewer: {e}")


# ---------- slash commands ----------

def _reply(client, channel, user, text, blocks=None):
    """ack() has already fired by the time these run, so if chat_postEphemeral
    itself fails (bot not in this channel, missing scope, bad channel id) the
    command silently does nothing in Slack -- log it so it shows up instead."""
    try:
        client.chat_postEphemeral(channel=channel, user=user, text=text, blocks=blocks)
    except Exception as e:
        print(f"[review] chat_postEphemeral failed (channel={channel}): {e}")


def handle_reviewsnipes_command(ack, command, client):
    ack()
    entry = json_store.next_unidentified()
    channel, user = command["channel_id"], command["user_id"]
    if not entry:
        _reply(client, channel, user, "Nothing to review — the unidentified queue is empty! 🎉")
        return
    _reply(client, channel, user, "Here's the next unidentified photo.", blocks=_review_blocks(entry, client))


def handle_reviewmode_command(ack, command, client):
    ack()
    arg = command.get("text", "").strip().lower()
    channel, user = command["channel_id"], command["user_id"]
    if arg not in ("on", "off"):
        current = "on" if json_store.get_setting("dm_review_enabled", False) else "off"
        _reply(client, channel, user, f"Usage: `/reviewmode on` or `/reviewmode off` (currently *{current}*).")
        return
    json_store.set_setting("dm_review_enabled", arg == "on")
    _reply(client, channel, user, f"DM review notifications turned *{arg}*.")


# ---------- resolving an entry from a button click or the modal ----------

def _replace_message(response_url, text):
    """Uses the interaction's response_url so this works the same whether the
    original card was an ephemeral /reviewsnipes reply or a real DM."""
    import requests
    if not response_url:
        return
    try:
        requests.post(response_url, json={
            "replace_original": True,
            "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        }, timeout=10)
    except Exception:
        pass


def _resolve_and_update(response_url, client, entry_id, kind, sniped=None):
    entry = detection.resolve_entry(entry_id, kind, client, sniped=sniped)
    if not entry:
        _replace_message(response_url, "This one's already been resolved.")
        return

    label = {
        "snipe": f"✅ Marked as a snipe on <@{entry['sniper']}>'s photo — sniped: "
                 + ", ".join(f"<@{u}>" for u in entry["sniped"]),
        "snope": "🟣 Marked as a snope.",
        "not_a_snipe": "⛔ Marked not a snipe.",
    }[kind]
    _replace_message(response_url, label)


def handle_mark_snope(ack, body, client):
    ack()
    entry_id = body["actions"][0]["value"]
    _resolve_and_update(body.get("response_url"), client, entry_id, "snope")


def handle_mark_nas(ack, body, client):
    ack()
    entry_id = body["actions"][0]["value"]
    _resolve_and_update(body.get("response_url"), client, entry_id, "not_a_snipe")


def handle_skip(ack, body, client):
    ack()
    _replace_message(body.get("response_url"), "Skipped — run `/reviewsnipes` again to see it later.")


def handle_mark_snipe(ack, body, client):
    """Opens a modal to pick who was sniped. Pre-fills the picker with the
    already-guessed user(s) for a likely_snipe entry, since that's usually
    just a one-click confirm rather than a real pick from scratch."""
    ack()
    entry_id = body["actions"][0]["value"]
    entry = json_store.find_snipe_by_id(entry_id)
    origin = {"entry_id": entry_id, "response_url": body.get("response_url")}
    element = {"type": "multi_users_select", "action_id": "sniped_users"}
    if entry and entry.get("sniped"):
        element["initial_users"] = entry["sniped"]
    try:
        client.views_open(trigger_id=body["trigger_id"], view={
            "type": "modal",
            "callback_id": "review_snipe_modal",
            "private_metadata": _json.dumps(origin),
            "title": {"type": "plain_text", "text": "Who got sniped?"},
            "submit": {"type": "plain_text", "text": "Confirm"},
            "blocks": [{
                "type": "input", "block_id": "sniped_block",
                "label": {"type": "plain_text", "text": "Sniped user(s)"},
                "element": element,
            }],
        })
    except Exception as e:
        # trigger_id is only valid for 3 seconds -- if this fails you'll see
        # "trigger_id" errors here rather than the modal just never opening
        print(f"[review] views_open failed for entry {entry_id}: {e}")


def handle_review_snipe_modal_submission(ack, body, client, view):
    ack()
    origin = _json.loads(view["private_metadata"])
    sniped = view["state"]["values"]["sniped_block"]["sniped_users"]["selected_users"]
    _resolve_and_update(origin["response_url"], client, origin["entry_id"], "snipe", sniped=sniped)
