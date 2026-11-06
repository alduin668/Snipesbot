"""
JSON-backed storage for SnipesBot's canonical data:

    data/snipes.json         -- every photo entry the bot has ever seen
    data/do_not_snipe.json   -- the do-not-snipe consent list
    data/dns_warnings.json   -- outstanding DNS warning messages (so they can
                                 be auto-deleted when the offending snipe is)
    data/settings.json       -- small key/value settings (review-DM toggle,
                                 the catch-up watermark, etc.)

Everything is written atomically (write to a temp file, then os.replace) so
that a reader -- including the separate webapp.py process -- never sees a
half-written file, and a crash mid-write can't corrupt the store. A
per-file lock also serializes read-modify-write sequences within this
process; NOTE this does NOT protect against two *processes* writing the
same file at the exact same instant. In practice only the bot process
writes snipes.json/dns files, while the webapp only writes via the same
handler functions (bot/*.py) over its own in-process calls, so this has
been safe in practice -- keep it that way if you add a second writer.
"""
import json
import os
import threading
import uuid
from typing import Optional

import config

_locks = {}


def _lock_for(path: str) -> threading.Lock:
    if path not in _locks:
        _locks[path] = threading.Lock()
    return _locks[path]


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def _save_json_atomic(path: str, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp_path, path)  # atomic on POSIX and Windows


# ---------------------------------------------------------------------------
# snipes.json -- the canonical per-photo entry log
# ---------------------------------------------------------------------------

def load_snipes() -> list:
    return _load_json(config.SNIPES_JSON, [])


def _save_snipes(entries: list):
    _save_json_atomic(config.SNIPES_JSON, entries)


def add_snipe(entry: dict):
    with _lock_for(config.SNIPES_JSON):
        entries = load_snipes()
        entries.append(entry)
        _save_snipes(entries)


def update_snipe(entry_id: str, **fields) -> Optional[dict]:
    """Patch an existing entry by id. Returns the updated dict, or None if
    no entry with that id exists."""
    with _lock_for(config.SNIPES_JSON):
        entries = load_snipes()
        for entry in entries:
            if entry.get("id") == entry_id:
                entry.update(fields)
                _save_snipes(entries)
                return entry
    return None


def delete_snipe(entry_id: str) -> bool:
    """Remove an entry by id entirely (used by scripts/cleartest_detector.py
    so test runs don't leave rows behind in snipes.json). Returns True if
    something was actually removed."""
    with _lock_for(config.SNIPES_JSON):
        entries = load_snipes()
        remaining = [e for e in entries if e.get("id") != entry_id]
        if len(remaining) == len(entries):
            return False
        _save_snipes(remaining)
        return True


def find_snipe_by_id(entry_id: str) -> Optional[dict]:
    for entry in load_snipes():
        if entry.get("id") == entry_id:
            return entry
    return None


def find_snipe_by_message(channel: str, ts: str) -> Optional[dict]:
    """The ORIGINAL entry the bot itself created for this message. A
    message that's been flagged "special" (bot/detection.py's
    create_special_snipe()) can have any number of additional derived
    entries sharing this same channel+ts -- those are deliberately excluded
    here so that reactions, edits, and recount always keep resolving to the
    one container entry, never to one of its derived children. Look those
    up with find_derived_snipes() instead."""
    for entry in load_snipes():
        if (
            entry.get("channel") == channel
            and entry.get("message_ts") == ts
            and not entry.get("derived_from")
        ):
            return entry
    return None


def find_derived_snipes(parent_id: str) -> list:
    """Every entry that was split out of a given "special" entry via the
    Special tab (bot/detection.py's create_special_snipe())."""
    return [e for e in load_snipes() if e.get("derived_from") == parent_id]


def load_special() -> list:
    """Every entry currently flagged "special" (fingerprint reaction) and
    still open -- i.e. not yet dismissed or reclassified into something
    else. Its own dedicated queue, separate from load_unidentified()."""
    return [e for e in load_snipes() if e.get("kind") == "special"]


def load_unidentified() -> list:
    """Every entry still awaiting a human decision -- "unidentified" (no
    usable evidence) and "likely_snipe" (strong guess, unconfirmed). Both
    kinds live in the same review queue (see bot/review.py)."""
    from bot.detection import PENDING_KINDS
    return [e for e in load_snipes() if e.get("kind") in PENDING_KINDS]


def next_unidentified() -> Optional[dict]:
    """The oldest pending entry, for /reviewsnipes."""
    pending = sorted(load_unidentified(), key=lambda e: e.get("message_ts", ""))
    return pending[0] if pending else None


# ---------------------------------------------------------------------------
# do_not_snipe.json -- consent list
# ---------------------------------------------------------------------------

def _load_dns() -> list:
    return _load_json(config.DNS_JSON, [])


def add_dns(user_id: str):
    with _lock_for(config.DNS_JSON):
        users = _load_dns()
        if user_id not in users:
            users.append(user_id)
            _save_json_atomic(config.DNS_JSON, users)


def is_dns(user_id: str) -> bool:
    return user_id in _load_dns()


def list_dns() -> list:
    return _load_dns()


def remove_dns(user_id: str):
    with _lock_for(config.DNS_JSON):
        users = _load_dns()
        if user_id in users:
            users.remove(user_id)
            _save_json_atomic(config.DNS_JSON, users)


# ---------------------------------------------------------------------------
# dns_warnings.json -- warning messages posted for a DNS violation, so they
# can be cleaned up automatically if the offending snipe is deleted
# ---------------------------------------------------------------------------

def _load_dns_warnings() -> list:
    return _load_json(config.DNS_WARNINGS_JSON, [])


def add_dns_warning(channel: str, snipe_ts: str, warning_channel: str,
                     warning_ts: str, sniper: str, target: str):
    with _lock_for(config.DNS_WARNINGS_JSON):
        warnings = _load_dns_warnings()
        warnings.append({
            "channel": channel,
            "snipe_ts": snipe_ts,
            "warning_channel": warning_channel,
            "warning_ts": warning_ts,
            "sniper": sniper,
            "target": target,
        })
        _save_json_atomic(config.DNS_WARNINGS_JSON, warnings)


def has_dns_warning(channel: str, snipe_ts: str, target: str) -> bool:
    return any(
        w["channel"] == channel and w["snipe_ts"] == snipe_ts and w["target"] == target
        for w in _load_dns_warnings()
    )


def pop_dns_warnings_for_snipe(channel: str, snipe_ts: str) -> list:
    """Removes and returns every warning tied to this snipe message (there
    can be more than one if multiple DNS users were tagged in one photo)."""
    with _lock_for(config.DNS_WARNINGS_JSON):
        warnings = _load_dns_warnings()
        matching = [w for w in warnings if w["channel"] == channel and w["snipe_ts"] == snipe_ts]
        if matching:
            remaining = [w for w in warnings if w not in matching]
            _save_json_atomic(config.DNS_WARNINGS_JSON, remaining)
        return matching


# ---------------------------------------------------------------------------
# settings.json -- small key/value store (review-DM toggle, catch-up
# watermark, and anywhere else a single persisted value is handy)
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    return _load_json(config.SETTINGS_JSON, {})


def get_setting(key: str, default=None):
    return _load_settings().get(key, default)


def set_setting(key: str, value):
    with _lock_for(config.SETTINGS_JSON):
        settings = _load_settings()
        settings[key] = value
        _save_json_atomic(config.SETTINGS_JSON, settings)


# ---------------------------------------------------------------------------
# adjustments.json -- manual, per-person score adjustments (can be
# fractional, positive or negative). analysis/stats.py's leaderboard() adds
# these directly to a user's score alongside the Snipes/Sniped counts it
# derives from snipes.json -- nothing in here is ever touched automatically
# by the detector; it only changes when someone edits it via the webapp's
# Adjustments tab (or scripts). A user with no adjustment simply doesn't
# appear in the file at all.
# ---------------------------------------------------------------------------

def _load_adjustments() -> dict:
    return _load_json(config.ADJUSTMENTS_JSON, {})


def get_adjustments() -> dict:
    """user_id -> adjustment (float), for every user who currently has a
    nonzero one. Callers should treat a missing user as 0."""
    return _load_adjustments()


def get_adjustment(user_id: str) -> float:
    return float(_load_adjustments().get(user_id, 0) or 0)


def set_adjustment(user_id: str, value: float):
    """Setting a value of exactly 0 removes the entry entirely, so the file
    only ever holds people who actually have a nonzero adjustment."""
    with _lock_for(config.ADJUSTMENTS_JSON):
        adjustments = _load_adjustments()
        if value:
            adjustments[user_id] = value
        else:
            adjustments.pop(user_id, None)
        _save_json_atomic(config.ADJUSTMENTS_JSON, adjustments)


# ---------------------------------------------------------------------------
# manual_matrix.json -- manual sniper->victim count corrections/additions,
# layered on top of the bot's own "coded" matrix (built from snipes.json) to
# produce the "true" matrix (see analysis/stats.py's true_matrix()). A cell
# here can be positive (a snipe Zeke knows happened but the bot never
# caught) or negative (correcting a snipe the bot over-counted). Kept in
# its own file, never touched by recount/backfill, so it's safe from ever
# being clobbered by a re-scan of Slack history.
# ---------------------------------------------------------------------------

def _load_manual_matrix() -> dict:
    return _load_json(config.MANUAL_MATRIX_JSON, {})


def get_manual_matrix() -> dict:
    """{sniper_id: {victim_id: count}} for every cell that currently has a
    nonzero manual correction/addition."""
    return _load_manual_matrix()


def get_manual_cell(sniper_id: str, victim_id: str) -> int:
    return int(_load_manual_matrix().get(sniper_id, {}).get(victim_id, 0) or 0)


def set_manual_cell(sniper_id: str, victim_id: str, value: int):
    """Setting a value of exactly 0 removes the cell (and the sniper's row
    entirely, if that was its last nonzero cell), so the file only ever
    holds cells that actually differ from the coded matrix."""
    with _lock_for(config.MANUAL_MATRIX_JSON):
        matrix = _load_manual_matrix()
        row = matrix.setdefault(sniper_id, {})
        if value:
            row[victim_id] = value
        else:
            row.pop(victim_id, None)
        if not row:
            matrix.pop(sniper_id, None)
        _save_json_atomic(config.MANUAL_MATRIX_JSON, matrix)
