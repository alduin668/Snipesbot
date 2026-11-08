"""Display names: lookup, caching, and rendering."""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..classifier import MENTION_RE
from ..env import Env
from ..models import is_slack_user_id

log = logging.getLogger("snipesbot")


def ensure_name(env: Env, user_id: str) -> str:
    """Cached display name for a Slack user, fetched once via users.info.
    Falls back to the bare id (and doesn't cache the failure)."""
    if not user_id or not is_slack_user_id(user_id):
        return user_id
    cached = env.store.names.get(user_id)
    if cached:
        return cached
    if env.slack is None:
        return user_id
    try:
        user = env.slack.users_info(user=user_id).get("user", {}) or {}
        profile = user.get("profile", {}) or {}
        name = (profile.get("display_name") or profile.get("real_name")
                or user.get("real_name") or user.get("name"))
    except Exception as exc:  # noqa: BLE001
        log.warning("users.info failed for %s: %s", user_id, exc)
        return user_id
    if name:
        env.store.names.set(user_id, name)
        return name
    return user_id


def display(user_id: Optional[str], names: dict) -> str:
    """"Name (ID)" once a name is known; the raw id (or free-text name) otherwise."""
    if not user_id:
        return ""
    name = names.get(user_id)
    return f"{name} ({user_id})" if name else user_id


def render_mentions(text: Optional[str], names: dict) -> str:
    """Replace <@U123> in message text with "Name (ID)". Plain text out: callers escape."""
    return MENTION_RE.sub(lambda m: display(m.group(1), names), text or "")


_ID_IN_TEXT = re.compile(r"<@([UW][A-Z0-9]+)")


def resolve_target(env: Env, text: str) -> Optional[str]:
    """Turn what someone typed after /donotsnipe into a Slack user id: a real
    <@ID> mention, the cached-name table, then a live users.list lookup."""
    text = (text or "").strip()
    if not text:
        return None
    m = _ID_IN_TEXT.search(text)
    if m:
        return m.group(1)
    name = text.lstrip("@").strip().lower()
    if not name:
        return None
    for uid, cached in env.store.names.all().items():
        if cached and cached.lower() == name:
            return uid
    try:
        members = env.slack.users_list().get("members", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("users.list lookup failed: %s", exc)
        return None
    for member in members:
        profile = member.get("profile", {}) or {}
        candidates = {(profile.get("display_name") or "").lower(),
                      (profile.get("real_name") or "").lower(),
                      (member.get("name") or "").lower()}
        if name in candidates:
            return member.get("id")
    return None
