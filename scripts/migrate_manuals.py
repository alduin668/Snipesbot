"""
One-time migration for historical manual decisions.

Before manually_resolved existed, the JSON log could not tell whether a green
or purple state was chosen by the bot or by Zeke. This script uses the current
Slack reaction users as the best available evidence: if Zeke himself reacted
with a classification emoji, that state is marked manual and protected.

It also converts Zeke's legacy fingerprint/thumbsup NAS decisions and treats a
Zeke red reaction on a confirmed snipe as the new incorrect-snipe correction.

Run this once before the first full recount if historical manual decisions
need to be preserved.

Usage:
    python -m scripts.migrate_manuals
"""
from slack_sdk import WebClient

import config
from bot import detection
from storage import json_store


client = WebClient(token=config.SLACK_BOT_TOKEN)


def zeke_reactions(channel, ts):
    try:
        response = client.reactions_get(channel=channel, timestamp=ts)
    except Exception as exc:
        print(f"[migrate] reactions_get failed for {ts}: {exc}", flush=True)
        return set()

    found = set()
    for reaction in response.get("message", {}).get("reactions", []):
        if config.ZEKE_USER_ID in reaction.get("users", []):
            found.add(reaction.get("name"))
    return found


def main():
    migrated = 0
    checked = 0

    for entry in json_store.load_snipes():
        if entry.get("manually_resolved"):
            continue

        checked += 1
        reactions = zeke_reactions(entry["channel"], entry["message_ts"])

        if config.INCORRECT_SNIPE_EMOJI in reactions and entry.get("kind") == "snipe":
            if detection.mark_incorrect_snipe(entry["id"], client):
                print(f"[migrate] {entry['id']}: Zeke red -> incorrect_snipe", flush=True)
                migrated += 1
            continue

        if "fingerprint" in reactions or "thumbsup" in reactions:
            if detection.mark_manual_kind(entry["id"], "not_a_snipe", client):
                print(f"[migrate] {entry['id']}: legacy NAS -> not_a_snipe", flush=True)
                migrated += 1
            continue

        if config.SNIPE_EMOJI in reactions:
            if detection.mark_manual_kind(entry["id"], "snipe", client):
                print(f"[migrate] {entry['id']}: Zeke green -> manual snipe", flush=True)
                migrated += 1
            continue

        if config.SNOPE_EMOJI in reactions:
            if detection.mark_manual_kind(entry["id"], "snope", client):
                print(f"[migrate] {entry['id']}: Zeke purple -> manual snope", flush=True)
                migrated += 1
            continue

        if config.LIKELY_SNIPE_EMOJI in reactions:
            if detection.mark_manual_kind(entry["id"], "likely_snipe", client):
                print(f"[migrate] {entry['id']}: Zeke orange -> manual likely_snipe", flush=True)
                migrated += 1

    print(f"Migration complete: checked {checked}, protected {migrated} historical manual decision(s).")


if __name__ == "__main__":
    main()
