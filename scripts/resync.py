"""
Rebuild/synchronize the true XLSX log from the canonical snipes.json store.

This does not reclassify anything. Use scripts/recount.py when detection
rules changed; use this when the workbook itself needs to be repaired.

Usage:
    python -m scripts.resync
"""
from slack_sdk import WebClient

import config
from bot import detection
from storage import json_store, xlsx_store


def main():
    client = WebClient(token=config.SLACK_BOT_TOKEN)
    entries = json_store.load_snipes()

    synced = 0
    deleted = 0
    for entry in entries:
        if entry.get("ignored"):
            xlsx_store.delete_true_log_row(entry["id"])
            deleted += 1
            continue

        detection._sync_entry_to_log(entry, client)
        synced += 1
        print(f"[resync] {entry['id']} -> {entry.get('kind')}", flush=True)

    print(
        f"Resync complete: {synced} row(s) synced, {deleted} ignored row(s) removed.",
        flush=True,
    )


if __name__ == "__main__":
    main()
