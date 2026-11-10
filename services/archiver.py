"""
Keeps the local archive current. The only module that downloads files or
pages through Slack history.

Messages arrive two ways: live (every event the bot sees) and historical
(`sync_from_slack`, racing Slack's retention window). Archiving is idempotent
and cheap to repeat: re-saving a message picks up edits, and an
already-downloaded file is a no-op lookup.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

from ..env import Env
from ..store import media_files

log = logging.getLogger("snipesbot")

Progress = Callable[[str], None]


def _download(env: Env, f: dict) -> bool:
    """Best-effort: one unreachable file never aborts an archive pass."""
    file_id = f.get("id")
    if not file_id:
        return False
    if env.store.archive.has_file(file_id):
        return True
    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        return False
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {env.cfg.bot_token}"}, timeout=30)
    except requests.RequestException as exc:
        log.warning("download of file %s failed: %s", file_id, exc)
        return False
    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code != 200 or ctype.startswith("text/html"):
        # Slack answers a missing files:read scope with an HTML page, not an error code.
        log.warning("file %s not downloadable (HTTP %s, %s)", file_id, resp.status_code, ctype)
        return False
    env.store.archive.save_file(file_id, f.get("name", ""), f.get("mimetype", ""), resp.content)
    return True


def archive_message(env: Env, message: dict) -> dict:
    """Archive one raw message (with `channel` set) and its media."""
    if not message.get("ts") or not message.get("channel"):
        return message
    for f in media_files(message):
        _download(env, f)
    env.store.archive.save_message(message)
    return message


def _archive_thread(env: Env, channel: str, thread_ts: str, seen: set) -> int:
    count, cursor = 0, None
    try:
        while True:
            resp = env.slack.conversations_replies(channel=channel, ts=thread_ts, cursor=cursor, limit=200)
            batch = []
            for reply in resp.get("messages", []):
                if reply.get("ts") == thread_ts:
                    continue  # the parent is archived by the caller
                reply = dict(reply, channel=channel)
                seen.add(reply.get("ts"))
                for f in media_files(reply):
                    _download(env, f)
                batch.append(reply)
            env.store.archive.save_messages(batch)
            count += len(batch)
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("could not archive replies for thread %s: %s", thread_ts, exc)
    return count


def sync_from_slack(env: Env, oldest_ts: float, progress: Optional[Progress] = None) -> dict:
    """Pull every message Slack still has since `oldest_ts` (plus thread replies
    and files) into the archive. One DB transaction per page, not per message.
    Returns counts plus `seen`, the set of message timestamps Slack returned."""
    channel = env.cfg.channel_id
    archived = replies = 0
    seen: set = set()
    cursor = None
    while True:
        resp = env.slack.conversations_history(channel=channel, oldest=str(oldest_ts), cursor=cursor, limit=200)
        page = []
        for msg in resp.get("messages", []):
            msg = dict(msg, channel=channel)
            for f in media_files(msg):
                _download(env, f)
            page.append(msg)
        env.store.archive.save_messages(page)
        archived += len(page)
        seen.update(m["ts"] for m in page)
        for msg in page:
            if msg.get("reply_count"):
                replies += _archive_thread(env, channel, msg["ts"], seen)
        if progress:
            progress(f"[archive] {archived} messages, {replies} replies so far")
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(1)  # be polite to conversations.history
    return {"archived": archived, "replies": replies, "seen": seen}


def fetch_file(env: Env, file_id: str):
    """Path of an archived file, downloading it now if the archive doesn't have
    it yet (an entry recorded before archiving existed)."""
    path = env.store.archive.file_path(file_id)
    if path or env.slack is None:
        return path
    try:
        info = env.slack.files_info(file=file_id).get("file") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("files.info failed for %s: %s", file_id, exc)
        return None
    _download(env, dict(info, id=file_id))
    return env.store.archive.file_path(file_id)
