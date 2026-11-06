"""
Permanent local archive of #snipes -- every message (top-level and thread
replies) ever seen, and every image/video file ever attached to one,
downloaded once and kept on disk for good.

WHY THIS EXISTS: Slack's free tier only keeps messages (and their files)
for config.SLACK_MESSAGE_RETENTION_DAYS (90 by default) before they become
permanently inaccessible via the API. Anything built by re-reading Slack on
demand -- a lazily-loaded context image, a historical backfill scan -- would
quietly start failing once that window closes over it. This module is the
fix: once a message or file has been archived here, nothing ever needs to
ask Slack for it again.

bot/archiver.py is the only thing that writes here. Everything else
(bot/detection.py, web/routes.py) only ever reads -- if something isn't
archived yet, that's archiver.py's job to fix, not theirs.

Messages are stored in one JSON file keyed by ts (a season's worth of
channel traffic is small enough that this is simpler than one file per
message), atomic-written the same way as every other *_store.py. Files are
plain files on disk under ARCHIVE_FILES_DIR, named by Slack file id plus
original extension; a small index JSON tracks each one's original name/
mimetype (needed to serve the right Content-Type without ever having to
ask Slack's files.info again).
"""
import os
import threading
import uuid

import config
from storage.json_store import _load_json, _save_json_atomic  # reuse the same atomic helpers

_messages_lock = threading.Lock()
_files_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def _load_messages() -> dict:
    return _load_json(config.ARCHIVE_MESSAGES_JSON, {})


def has_message(ts: str) -> bool:
    return ts in _load_messages()


def load_message(ts: str):
    return _load_messages().get(ts)


def save_message(message: dict):
    """Archives (or re-archives, e.g. after an edit) one raw Slack message
    by its own ts. Idempotent -- safe to call repeatedly for the same ts."""
    ts = message.get("ts")
    if not ts:
        return
    with _messages_lock:
        messages = _load_messages()
        messages[ts] = message
        _save_json_atomic(config.ARCHIVE_MESSAGES_JSON, messages)


def all_messages() -> list:
    """Every archived message (top-level and thread replies alike), oldest
    first."""
    return sorted(_load_messages().values(), key=lambda m: m.get("ts", ""))


def message_count() -> int:
    return len(_load_messages())


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _load_file_index() -> dict:
    return _load_json(config.ARCHIVE_FILE_INDEX_JSON, {})


def file_info(file_id: str):
    """{"path", "name", "mimetype"} for an archived file, or None if it
    hasn't been archived (yet)."""
    return _load_file_index().get(file_id)


def file_path(file_id: str):
    info = file_info(file_id)
    return info["path"] if info and os.path.exists(info["path"]) else None


def has_file(file_id: str) -> bool:
    return file_path(file_id) is not None


def save_file(file_id: str, name: str, mimetype: str, data: bytes) -> str:
    """Downloads once, permanently. Idempotent -- if already archived,
    returns the existing path without re-writing anything."""
    existing = file_path(file_id)
    if existing:
        return existing

    ext = (
        os.path.splitext(name or "")[1]
        or (".mp4" if (mimetype or "").startswith("video/") else ".jpg")
    )
    os.makedirs(config.ARCHIVE_FILES_DIR, exist_ok=True)
    path = os.path.join(config.ARCHIVE_FILES_DIR, f"{file_id}{ext}")
    tmp_path = os.path.join(config.ARCHIVE_FILES_DIR, f".{file_id}.{uuid.uuid4().hex}.tmp")

    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, path)

    with _files_lock:
        index = _load_file_index()
        index[file_id] = {"path": path, "name": name, "mimetype": mimetype}
        _save_json_atomic(config.ARCHIVE_FILE_INDEX_JSON, index)

    return path
