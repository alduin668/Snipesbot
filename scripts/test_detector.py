"""
Standalone runner for the /test battery (see bot/testing.py for the actual
logic). Same pattern as scripts/backfill.py and scripts/resync.py: its own
Slack client, since this runs as a separate process from the live bot --
so it's usable from the webapp's Scripts tab, not just as a slash command.

    python -m scripts.test_detector
"""
from slack_sdk import WebClient

import config
from bot import testing


def main():
    client = WebClient(token=config.SLACK_BOT_TOKEN)
    print(testing.run_test_batch(client))


if __name__ == "__main__":
    main()
