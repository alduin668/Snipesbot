"""Slack Block Kit cards for the review flow (presentation only)."""
from __future__ import annotations

from ..env import Env
from ..models import Entry, Kind
from . import context as context_service
from .. import classifier


def _msg_block(m: dict) -> dict:
    text = m["text"] or "_(no text)_"
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"<@{m['user_id']}>: {text}"}}


def build_blocks(env: Env, entry: Entry) -> list:
    """Top-to-bottom the way the conversation happened: earlier messages, the
    photo, later messages, then the identification controls."""
    ctx = context_service.around(env, entry)
    guessed = classifier.guessed_sniped(entry.caption, entry.sniper, context_service.context_texts(env, entry))
    link = env.permalink(entry.channel, entry.message_ts)

    blocks = [_msg_block(m) for m in ctx["before"] if m["user_id"]]
    if entry.file and not entry.file.is_video:
        blocks.append({"type": "image", "slack_file": {"id": entry.file.id}, "alt_text": "snipe photo"})
    elif entry.file:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "🎥 _video attached -- view it in #snipes_"}]})
    caption = entry.caption
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": f"📷 <@{entry.sniper}>" + (f": {caption}" if caption else " _(no caption)_")}]})
    blocks += [_msg_block(m) for m in ctx["after"] if m["user_id"]]

    label = "Likely snipe (bare mention, unconfirmed)" if entry.kind is Kind.LIKELY_SNIPE else "Unidentified"
    heading = f"*Identify this photo* -- _{label}_"
    if link:
        heading += f"\n<{link}|View in #snipes>"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": heading}})

    def button(action_id, text, style=None):
        b = {"type": "button", "action_id": action_id, "text": {"type": "plain_text", "text": text}, "value": entry.id}
        if style:
            b["style"] = style
        return b

    blocks.append({"type": "actions", "block_id": f"review_{entry.id}", "elements": [
        button("review_mark_snipe", "It's a snipe", "primary"),
        button("review_mark_snope", "It's a snope"),
        button("review_mark_nas", "Not a snipe"),
        button("review_skip", "Skip for now"),
    ]})
    if guessed:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": "Guessed mention(s) from context: " + ", ".join(f"<@{g}>" for g in guessed)}]})
    return blocks
