"""
Backfill/reconcile #snipes history.

This is a real reconciliation pass, done in two phases:

  1. Sync the permanent local archive (storage/archive_store.py) from Slack
     for everything since `oldest_ts` (bot/archiver.py's sync_from_slack())
     -- the ONLY part of this still racing Slack's
     SLACK_MESSAGE_RETENTION_DAYS retention window. Whatever Slack still
     has gets saved locally for good; anything already past that window is
     simply unavailable from Slack and can't be recovered, archived or not.
  2. Discover any photo/video messages in the (now up to date) LOCAL
     ARCHIVE that aren't in snipes.json yet, add them, then run the
     current detector over the entire canonical log. This half is
     entirely local -- no further Slack dependency, so it's exactly as
     reliable whether the messages involved are a day old or a year old.

Manual decisions are never overwritten by step 2.

`run_backfill()` is the shared core used by the CLI below, by
bot/catchup.py's periodic background pass, and by scripts/recount.py --
so none of the three can ever drift out of sync with what running this by
hand would do.

CLI usage:
    python -m scripts.backfill --since 2026-08-15
"""
import argparse
from datetime import datetime

from slack_sdk import WebClient

import config
from bot import archiver, detection
from storage import archive_store


def run_backfill(oldest_ts: float, client: WebClient) -> dict:
    """Syncs the local archive from `oldest_ts` (a unix timestamp) forward,
    discovers any photos in the archive missing from snipes.json, then
    recounts everything. Returns a stats dict; see print_summary() for the
    human-readable form."""
    sync_stats = archiver.sync_from_slack(config.SNIPES_CHANNEL_ID, oldest_ts, client)

    discovered = 0
    existing = 0
    photos = 0

    for msg in archive_store.all_messages():
        try:
            if float(msg.get("ts", 0)) < oldest_ts:
                continue
        except (TypeError, ValueError):
            continue
        if msg.get("subtype") or not msg.get("user"):
            continue
        if not detection._has_image(msg):
            continue

        photos += 1
        entry, created = detection.record_historical_photo(msg, client)
        if entry is None:
            continue

        if created:
            discovered += 1
            print(f"[backfill] added {entry['message_ts']} -> {entry['kind']}", flush=True)
        else:
            existing += 1

    changed = detection.recount_all(client, progress=lambda m: print(m, flush=True))

    return {
        "photos": photos,
        "added": discovered,
        "existing": existing,
        "changed": changed,
        "archived_messages": sync_stats["archived"],
        "archived_replies": sync_stats["replies_archived"],
    }


def print_summary(stats: dict):
    print(
        f"[backfill] archive sync complete: {stats['archived_messages']} message(s) "
        f"({stats['archived_replies']} thread repl(y/ies)) archived locally.",
        flush=True,
    )
    print(
        f"[backfill] local scan complete: {stats['photos']} photo(s), "
        f"{stats['added']} added, {stats['existing']} already tracked.",
        flush=True,
    )
    print(
        f"Backfill/recount complete: {stats['added']} new photo(s), "
        f"{stats['changed']} automatic classification change(s).",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="YYYY-MM-DD, how far back to scan")
    args = parser.parse_args()

    since_ts = datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    slack_client = WebClient(token=config.SLACK_BOT_TOKEN)
    result = run_backfill(since_ts, slack_client)
    print_summary(result)
