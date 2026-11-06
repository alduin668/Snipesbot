"""
Slack user ID -> display name cache (data/user_names.json).

Only the bot process (which holds a live Slack client) ever calls
Slack's users.info to populate this; the webapp and analysis code are
read-only consumers, same as they read the .xlsx/.json stores without
needing a Slack token at all.
"""
import threading

import config
from storage.json_store import _load_json, _save_json_atomic  # reuse the same atomic helpers

_lock = threading.Lock()


def _load() -> dict:
    return _load_json(config.USER_NAMES_JSON, {})


def get_cached_names() -> dict:
    """user_id -> display name, for every user we've ever looked up."""
    return _load()


def ensure_cached(user_id: str, client) -> str:
    """Looks up and caches `user_id`'s display name if we don't already
    have it. Returns the (possibly newly-fetched) name, or the bare user id
    if the Slack lookup fails."""
    if not user_id:
        return user_id

    names = _load()
    if user_id in names:
        return names[user_id]

    name = user_id
    try:
        info = client.users_info(user=user_id)
        profile = info.get("user", {})
        name = (
            profile.get("profile", {}).get("display_name")
            or profile.get("profile", {}).get("real_name")
            or profile.get("real_name")
            or profile.get("name")
            or user_id
        )
    except Exception as e:
        print(f"[user_names] failed to look up {user_id}: {e}")

    with _lock:
        names = _load()
        names[user_id] = name
        _save_json_atomic(config.USER_NAMES_JSON, names)
    return name


def format_side_by_side(user_id: str, names: dict) -> str:
    """"Name (ID)" formatting used throughout the webapp, so a raw Slack ID
    is never shown on its own once a name is known."""
    if not user_id:
        return ""
    name = names.get(user_id)
    return f"{name} ({user_id})" if name else user_id
