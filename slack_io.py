"""Thin, defensive wrappers around the Slack Web API."""
from __future__ import annotations

import logging
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from .env import Env
from .models import Kind

log = logging.getLogger("snipesbot")


def add_rate_limit_retry(client) -> None:
    """Make a WebClient wait out Slack rate limits (HTTP 429) instead of failing."""
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))


def make_client(token: str) -> WebClient:
    client = WebClient(token=token)
    add_rate_limit_retry(client)
    return client


def _error_code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    try:
        return resp["error"] if resp is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def sync_reaction(env: Env, channel: str, ts: str, kind: Kind) -> bool:
    """Make the bot's own classification reaction on a message match `kind`.

    Only reactions placed by the bot are removed; other people's are never
    touched. Returns True when the message is known to carry the right
    reaction afterwards. A failed lookup never fans out into a pile of blind
    removals, so a rate-limited run costs one extra call, not nine.
    """
    wanted = env.cfg.emoji_for(kind)
    bot_id = env.bot_user_id()
    managed = set(env.cfg.managed_emojis())
    client = env.slack

    try:
        resp = client.reactions_get(channel=channel, timestamp=ts)
    except Exception as exc:  # noqa: BLE001
        log.warning("reactions.get failed for %s/%s: %s", channel, ts, exc)
        resp = None

    if resp is not None and bot_id:
        has_wanted = False
        for reaction in (resp.get("message") or {}).get("reactions", []) or []:
            name, users = reaction.get("name"), set(reaction.get("users", []))
            if bot_id not in users:
                continue
            if name == wanted:
                has_wanted = True
            elif name in managed:
                try:
                    client.reactions_remove(channel=channel, timestamp=ts, name=name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not remove :%s: from %s/%s: %s", name, channel, ts, exc)
        if has_wanted:
            return True

    try:
        client.reactions_add(channel=channel, timestamp=ts, name=wanted)
        return True
    except Exception as exc:  # noqa: BLE001
        if _error_code(exc) == "already_reacted":
            return True
        log.warning("could not add :%s: to %s/%s: %s", wanted, channel, ts, exc)
        return False


def post_dm(env: Env, user_id: str, text: str, blocks: Optional[list] = None) -> bool:
    try:
        env.slack.chat_postMessage(channel=user_id, text=text, blocks=blocks)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("DM to %s failed: %s", user_id, exc)
        return False
