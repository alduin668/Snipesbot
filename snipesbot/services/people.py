"""
Names. Everything shown to a person goes through here, so ids never leak into
the UI, exports or CLI output if a name can be found.

  display(id, names)       "Gabe Nell"; ambiguous names get a short id suffix;
                           an unresolved real id reads "Unknown (U0123…)"
  render_text(text, names) Slack message text -> plain readable text:
                           <@U1> -> @Name, <#C1|general> -> #general, <!here> -> @here,
                           <https://x|label> -> label, &amp; -> &
  refresh_directory(env)   one users.list pass caches every workspace member
  ensure_names(env, ids)   look up any stragglers (bounded, failures remembered)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional

from ..classifier import MENTION_RE
from ..env import Env
from ..models import is_slack_user_id

log = logging.getLogger("snipesbot")

DIRECTORY_KEY = "names_refreshed_at"
_MISS_RETRY_SECONDS = 600


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def display(user_id: Optional[str], names: dict) -> str:
    if not user_id:
        return ""
    name = names.get(user_id)
    if name:
        if name.lower() in getattr(names, "ambiguous", ()):
            return f"{name} ({user_id[-4:]})"      # two people share this name
        return name
    if is_slack_user_id(user_id):
        return f"Unknown ({user_id})"
    return user_id                                  # free text, e.g. "deer"


_CHANNEL_RE = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]*))?>")
_SPECIAL_RE = re.compile(r"<!([^>|]+)(?:\|([^>]*))?>")
_LINK_RE = re.compile(r"<((?:https?://|mailto:)[^>|]+)(?:\|([^>]*))?>")
_SPECIAL_NAMES = {"here": "@here", "channel": "@channel", "everyone": "@everyone"}


def _special(match: re.Match) -> str:
    kind, label = match.group(1), match.group(2)
    if kind in _SPECIAL_NAMES:
        return _SPECIAL_NAMES[kind]
    if kind.startswith("subteam^"):
        return label or "@group"
    return label or ""                              # <!date^...|fallback>


def render_text(text: Optional[str], names: dict) -> str:
    """Slack's wire format -> text a person can read. Plain text out: callers escape."""
    out = MENTION_RE.sub(lambda m: "@" + display(m.group(1), names), text or "")
    out = _CHANNEL_RE.sub(lambda m: "#" + (m.group(2) or "channel"), out)
    out = _SPECIAL_RE.sub(_special, out)
    out = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), out)
    # Slack escapes exactly these three; undo them last so they can't fake markup above.
    return out.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


render_mentions = render_text   # the old name


def ids_in(text: Optional[str]) -> list:
    return MENTION_RE.findall(text or "")


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def _profile_name(user: dict) -> Optional[str]:
    profile = user.get("profile", {}) or {}
    return (profile.get("display_name") or profile.get("real_name")
            or user.get("real_name") or user.get("name") or None)


def ensure_name(env: Env, user_id: str) -> str:
    """Cached display name for a Slack user, fetched once via users.info.
    Falls back to the bare id; failures are remembered for a few minutes."""
    if not user_id or not is_slack_user_id(user_id):
        return user_id
    cached = env.store.names.get(user_id)
    if cached:
        return cached
    if env.slack is None or time.time() - env._name_misses.get(user_id, 0) < _MISS_RETRY_SECONDS:
        return user_id
    try:
        name = _profile_name(env.slack.users_info(user=user_id).get("user", {}) or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("users.info failed for %s: %s", user_id, exc)
        env._name_misses[user_id] = time.time()
        return user_id
    if name:
        env.store.names.set(user_id, name)
        return name
    env._name_misses[user_id] = time.time()
    return user_id


def ensure_names(env: Env, user_ids: Iterable[str], limit: int = 20) -> int:
    """Resolve up to `limit` uncached real user ids. Returns how many were looked up."""
    known = env.store.names.all()
    looked = 0
    for uid in dict.fromkeys(user_ids):
        if looked >= limit:
            break
        if is_slack_user_id(uid) and uid not in known:
            ensure_name(env, uid)
            looked += 1
    return looked


def refresh_directory(env: Env, *, force: bool = False, max_age: float = 24 * 3600) -> int:
    """Cache every workspace member's name in one paginated users.list pass, so
    mentions of people the bot never otherwise met still show as names.
    Needs the users:read scope. Returns the number of names stored (0 if skipped)."""
    last = env.store.settings.get(DIRECTORY_KEY)
    if env.slack is None or (not force and last and time.time() - last < max_age):
        return 0
    found: dict = {}
    cursor = None
    try:
        while True:
            resp = env.slack.users_list(cursor=cursor, limit=200)
            for member in resp.get("members", []) or []:
                name = _profile_name(member)
                if member.get("id") and name:
                    found[member["id"]] = name
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("could not refresh the member directory (needs users:read): %s", exc)
        return 0
    env.store.names.set_many(found)
    env.store.settings.set(DIRECTORY_KEY, time.time())
    env._name_misses.clear()
    return len(found)


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
