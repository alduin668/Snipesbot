"""
Full SnipesBot reconciliation -- the command to run after changing
detection rules.

This now simply delegates to scripts/backfill.py's run_backfill(), scanning
from SEASON_START_DATE: that (1) syncs the local archive from Slack for
anything new, (2) discovers any photos in the archive missing from
snipes.json, then (3) re-runs the current detector over the entire
canonical log and repairs the bot's classification reaction on every photo.
Previously this had its own separate Slack-scanning loop, duplicating
backfill.py's -- now there's exactly one such loop to maintain, and recount
gets the same local-archive robustness backfill does for free.

Manual decisions are immutable throughout.

Usage:
    python -m scripts.recount
"""
from datetime import datetime

from slack_sdk import WebClient

import config
from scripts.backfill import run_backfill, print_summary


def main():
    client = WebClient(token=config.SLACK_BOT_TOKEN)
    oldest_ts = datetime.strptime(config.SEASON_START_DATE, "%Y-%m-%d").timestamp()
    stats = run_backfill(oldest_ts, client)
    print_summary(stats)


if __name__ == "__main__":
    main()
