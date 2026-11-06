"""
One-time repair for "malformed snipes" -- entries with kind == "snipe" but
an empty sniped list. These came from confirming "It's a snipe" (in Slack's
modal or the old webapp flow) without actually picking a sniped user; the
webapp's Review Queue / Manual Decisions / Live Log now refuse to create new
ones (see web/routes.py's set-kind endpoint), but that doesn't fix entries
that already exist in snipes.json from before that guard existed.

There's no reliable way to *guess* who was actually sniped from data alone
-- the sniped list is empty precisely because nobody recorded it. So this
script doesn't guess. It demotes each malformed entry back to
"unidentified" (clearing its Slack reaction to match) so it lands back in
the Review Queue / Manual Decisions tab. Once there, the webapp's
unidentified-entry fallback (see routes.py's _guessed_sniped) auto-suggests
whatever @mentions are in the photo's own caption as pre-filled chips, so
fixing most of these is just opening the card and clicking "Snipe" again
with the right name(s) already in the box.

Nothing is deleted -- this only changes `kind` (via the same
mark_manual_kind path every other manual reclassification uses), so the
entry keeps its full history and photo, and shows up in both the Review
Queue (pending kinds) and Manual Decisions tab (it's still a manual call)
until it's re-confirmed with a real sniped user.

Usage:
    python -m scripts.fix_malformed_snipes
"""
from slack_sdk import WebClient

import config
from bot import detection
from storage import json_store


def main():
    client = WebClient(token=config.SLACK_BOT_TOKEN)

    malformed = [
        e for e in json_store.load_snipes()
        if e.get("kind") == "snipe" and not e.get("sniped")
    ]

    if not malformed:
        print("No malformed snipes found -- nothing to do.")
        return

    print(f"Found {len(malformed)} malformed snipe(s) (sniper set, no sniped user). Demoting to unidentified...")

    for entry in malformed:
        detection.mark_manual_kind(entry["id"], "unidentified", client)
        print(
            f"[fix] {entry['id']} ({entry.get('message_ts')}, sniper={entry.get('sniper')}): "
            f"snipe w/ empty sniped -> unidentified",
            flush=True,
        )

    print(
        f"\nDone: {len(malformed)} entr{'y' if len(malformed) == 1 else 'ies'} moved back to unidentified. "
        f"Check the Review Queue or Manual Decisions tab to re-confirm them."
    )


if __name__ == "__main__":
    main()
