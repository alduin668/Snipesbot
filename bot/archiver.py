"""
Keeps storage/archive_store.py's local archive current. This is the ONLY
module that ever downloads a file, or calls conversations_history/
conversations_replies for historical purposes. Everything else in the
codebase reads from the archive once something's been archived -- never
from Slack directly.

Messages get in here two ways:

  - Live: app.py calls archive_message() for every message event (and its
    edits) as they arrive, so nothing the bot is running to see ever ends
    up depending on Slack still having it 90 days from now.
  - Historical: sync_from_slack() pulls a time range from Slack (used by
    scripts/backfill.py's run_backfill(), which the periodic catch-up in
    bot/catchup.py already calls, and by scripts/archive_sync.py for an
    explicit one-off run) -- this is the only part of the whole system
    still racing Slack's retention clock. Once a message is archived, it's
    safe forever; only the sync step itself can miss something, and only
    if it never gets attempted before that message ages out.

Archiving is designed to be cheap to repeat: re-archiving an already-saved
message just overwrites it with whatever Slack has now (picking up edits),
and re-archiving an already-downloaded file is a no-op lookup. So calling
this liberally -- on every live event, on every historical re-scan -- is
always safe and also self-healing (a file that failed to download last
time just gets retried next time something touches that message again).
"""
import time

import requests

import config
from storage import archive_store


def _download_file(file_obj: dict) -> bool:
    """Best-effort: a file that's somehow already inaccessible (a Slack
    hiccup, a permission edge case) is logged and skipped rather than
    raised, so one bad file never aborts an entire archive pass."""
    file_id = file_obj.get("id")
    if not file_id:
        return False
    if archive_store.has_file(file_id):
        return True

    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        return False

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"[archiver] file {file_id} download returned HTTP {resp.status_code}", flush=True)
            return False
        archive_store.save_file(file_id, file_obj.get("name", ""), file_obj.get("mimetype", ""), resp.content)
        return True
    except Exception as e:
        print(f"[archiver] failed to download file {file_id}: {e}", flush=True)
        return False


def archive_message(message: dict, client=None) -> dict:
    """Archives one raw Slack message (idempotent -- safe to call for an
    already-archived ts, e.g. on every live event, or a re-scan picking up
    an edit) and downloads every image/video it carries. Returns the
    message as stored. `client` is accepted but unused (downloads go
    straight through `requests` with the bot token) -- kept so every call
    site reads the same regardless of whether a client happens to be
    needed."""
    from bot.detection import _all_media_files  # local import avoids a circular import

    if not message.get("ts"):
        return message

    for f in _all_media_files(message):
        _download_file(f)

    archive_store.save_message(message)
    return message


def archive_thread_replies(channel: str, thread_ts: str, client) -> int:
    """A message with reply_count > 0 only has its top-level self returned
    by conversations_history -- replies need their own conversations_replies
    call. Best-effort: a thread Slack no longer has anything for just
    archives nothing further, rather than raising. Returns how many replies
    got archived."""
    count = 0
    try:
        cursor = None
        while True:
            resp = client.conversations_replies(channel=channel, ts=thread_ts, cursor=cursor, limit=200)
            for reply in resp.get("messages", []):
                if reply.get("ts") == thread_ts:
                    continue  # the parent itself -- archived separately by the caller
                reply = dict(reply)
                reply["channel"] = channel
                archive_message(reply, client)
                count += 1
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        print(f"[archiver] failed to archive replies for thread {thread_ts}: {e}", flush=True)
    return count


def sync_from_slack(channel: str, oldest_ts: float, client) -> dict:
    """Pulls every message Slack still has from `oldest_ts` forward and
    archives it (plus its files, plus its thread replies). This is the part
    still racing Slack's retention window -- run it often enough (the
    periodic catch-up already does, and scripts/archive_sync.py is here for
    running it by hand too) and nothing should ever fall outside it."""
    archived, replies_archived = 0, 0
    cursor = None

    while True:
        resp = client.conversations_history(channel=channel, oldest=str(oldest_ts), cursor=cursor, limit=200)
        for msg in resp.get("messages", []):
            msg = dict(msg)
            msg["channel"] = channel
            archive_message(msg, client)
            archived += 1
            if msg.get("reply_count"):
                replies_archived += archive_thread_replies(channel, msg["ts"], client)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(1)

    return {"archived": archived, "replies_archived": replies_archived}
