"""
One-off: sync the permanent local archive (storage/archive_store.py) from
Slack right now, covering the full season from SEASON_START_DATE. Doesn't
touch snipes.json or re-run classification at all -- it ONLY makes sure
every message and file Slack still has gets saved locally before Slack's
own retention window (SLACK_MESSAGE_RETENTION_DAYS, 90 by default) closes
over any of it.

Safe to run anytime, as often as you like -- archiving is fully idempotent;
nothing already saved is ever re-downloaded or overwritten.
bot/catchup.py's periodic background pass already does this automatically
as part of every catch-up (scripts/backfill.py's run_backfill() calls the
exact same bot/archiver.py.sync_from_slack() this does) -- this script is
here for running it by hand, e.g. right after deploying this for the first
time, to immediately grab anything getting close to aging out.

Usage:
    python -m scripts.archive_sync
"""
from datetime import datetime

from slack_sdk import WebClient

import config
from bot import archiver


def main():
    client = WebClient(token=config.SLACK_BOT_TOKEN)
    oldest_ts = datetime.strptime(config.SEASON_START_DATE, "%Y-%m-%d").timestamp()

    print(f"[archive_sync] syncing #snipes from {config.SEASON_START_DATE}...", flush=True)
    stats = archiver.sync_from_slack(config.SNIPES_CHANNEL_ID, oldest_ts, client)
    print(
        f"[archive_sync] done: {stats['archived']} message(s), "
        f"{stats['replies_archived']} thread repl(y/ies) archived.",
        flush=True,
    )


if __name__ == "__main__":
    main()
