"""/reviewsnipes, /reviewmode and the buttons/modal on the review card."""
from __future__ import annotations

import json
import logging

import requests

from ..env import Env
from .. import classifier
from ..models import Kind, is_slack_user_id
from ..services import context as context_service, entries as svc, notify, review_cards

log = logging.getLogger("snipesbot")


def _reply(env: Env, channel: str, user: str, text: str, blocks=None) -> None:
    try:
        env.slack.chat_postEphemeral(channel=channel, user=user, text=text, blocks=blocks)
    except Exception as exc:  # noqa: BLE001 - ack() already fired; make the failure visible in logs
        log.warning("chat.postEphemeral failed (channel=%s): %s", channel, exc)


def reviewsnipes(env: Env, command: dict) -> None:
    pending = env.store.entries.needs_review()
    channel, user = command["channel_id"], command["user_id"]
    if not pending:
        _reply(env, channel, user, "Nothing to review — the queue is empty! 🎉")
        return
    _reply(env, channel, user, "Here's the next unidentified photo.",
           blocks=review_cards.build_blocks(env, pending[0]))


def reviewmode(env: Env, command: dict) -> None:
    arg = (command.get("text") or "").strip().lower()
    channel, user = command["channel_id"], command["user_id"]
    if arg not in ("on", "off"):
        current = "on" if notify.enabled(env) else "off"
        _reply(env, channel, user, f"Usage: `/reviewmode on` or `/reviewmode off` (currently *{current}*).")
        return
    notify.set_enabled(env, arg == "on")
    _reply(env, channel, user, f"DM review notifications turned *{arg}*.")


def _replace_message(response_url: str, text: str) -> None:
    """Edit the card in place via the interaction's response_url (works for
    ephemeral replies and DMs alike)."""
    if not response_url:
        return
    try:
        requests.post(response_url, json={
            "replace_original": True, "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        }, timeout=10)
    except requests.RequestException as exc:
        log.warning("could not update review card: %s", exc)


def _resolve_and_update(env: Env, response_url: str, entry_id: str, kind: Kind, sniped=None) -> None:
    try:
        entry = svc.resolve_pending(env, entry_id, kind, sniped=sniped)
    except svc.InvalidTransition as exc:
        _replace_message(response_url, f"Couldn't resolve that one: {exc}.")
        return
    if entry is None:
        _replace_message(response_url, "This one's already been resolved.")
        return
    label = {
        Kind.SNIPE: f"✅ Marked as a snipe on <@{entry.sniper}>'s photo — sniped: "
                    + ", ".join(f"<@{u}>" for u in entry.sniped),
        Kind.SNOPE: "🟣 Marked as a snope.",
        Kind.NOT_A_SNIPE: "⛔ Marked not a snipe.",
    }[kind]
    _replace_message(response_url, label)


def mark(env: Env, body: dict, kind: Kind) -> None:
    _resolve_and_update(env, body.get("response_url"), body["actions"][0]["value"], kind)


def skip(body: dict) -> None:
    _replace_message(body.get("response_url"), "Skipped — run `/reviewsnipes` again to see it later.")


def open_snipe_modal(env: Env, body: dict) -> None:
    """Ask who got sniped, pre-filled with any guessed mention(s)."""
    entry_id = body["actions"][0]["value"]
    entry = env.store.entries.get(entry_id)
    element = {"type": "multi_users_select", "action_id": "sniped_users"}
    if entry:
        guess = list(entry.sniped) or classifier.guessed_sniped(
            entry.caption, entry.sniper, context_service.context_texts(env, entry))
        guess = [u for u in guess if is_slack_user_id(u)]
        if guess:
            element["initial_users"] = guess
    try:
        env.slack.views_open(trigger_id=body["trigger_id"], view={
            "type": "modal", "callback_id": "review_snipe_modal",
            "private_metadata": json.dumps({"entry_id": entry_id, "response_url": body.get("response_url")}),
            "title": {"type": "plain_text", "text": "Who got sniped?"},
            "submit": {"type": "plain_text", "text": "Confirm"},
            "blocks": [{"type": "input", "block_id": "sniped_block",
                        "label": {"type": "plain_text", "text": "Sniped user(s)"}, "element": element}],
        })
    except Exception as exc:  # noqa: BLE001 - trigger_ids expire after 3 seconds
        log.warning("views.open failed for %s: %s", entry_id, exc)


def submit_snipe_modal(env: Env, view: dict) -> None:
    origin = json.loads(view["private_metadata"])
    sniped = view["state"]["values"]["sniped_block"]["sniped_users"]["selected_users"]
    _resolve_and_update(env, origin["response_url"], origin["entry_id"], Kind.SNIPE, sniped=sniped)
