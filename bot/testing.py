"""
/test and /cleartest -- posts a battery of synthetic test photos into
#snipes and drives them straight through bot/detection.py's own
handle_message(), so a change to the classification rules (or to the
do-not-snipe warning flow) can be sanity-checked by eye instead of manually
posting six photos with just-right captions every time.

Shared by:
  - the /test and /cleartest slash commands (app.py), using Bolt's own client
  - scripts/test_detector.py and scripts/cleartest_detector.py, standalone
    scripts with their own client -- same pattern as backfill.py/resync.py --
    so this is also runnable from the webapp's Scripts tab, not just Slack.

WHY THIS DOESN'T JUST POST AND WAIT FOR THE LIVE LISTENER:
app.py's own on_message handler bails out immediately on any event carrying
a "bot_id" -- `if event.get("bot_id"): return` -- specifically so the bot
never reacts to its own messages. Every message this module posts is sent
with the bot's own token, so that early return means the live listener
would NEVER see them; posting and waiting would silently classify nothing.
Instead, this module posts the message so you can SEE it in Slack, fetches
that same message back (to get Slack's own "files" field), and feeds it
straight into detection.handle_message() itself -- the exact function
app.py's listener would have called -- so the classification path under
test is identical either way.

That also removes the need for the old "make the bot mention itself" trick
for the do-not-snipe case. Since handle_message() is called directly here,
the synthetic event's "user" field (who's recorded as the sniper) can be
set to a real human (ZEKE_USER_ID) regardless of who actually clicked
"post message" in Slack. So the do-not-snipe case just makes the bot the
SNIPED party with a real sniper from the start -- no self-exclusion
workaround, no patching an entry after the fact -- and the warning DM goes
straight to ZEKE_USER_ID exactly as it would for a real violation.

Every case mentions the BOT as the sniped party, never a real human -- on
purpose. The bot is never a leaderboard participant, so a test entry can
never dent anyone's real ranking, whether or not /cleartest gets run right
away. Since the synthetic sniper is always ZEKE_USER_ID and the mention is
always the bot's own id, the two are never equal, so the detector's own
self-mention exclusion (which stops a real human "sniping themselves")
never gets in the way -- no target user needs to be configured at all.

Everything posted is tracked in data/test_messages.json (channel+ts+label
per message) so /cleartest can find and remove: the Slack message itself,
any do-not-snipe warning reply, and the corresponding entry in the local
log/.xlsx -- so test runs never pollute the real leaderboard.
"""
import json
import os
import struct
import tempfile
import time
import zlib

import config
from bot import detection
from storage import json_store, xlsx_store

TRACKED_JSON = config.TEST_MESSAGES_JSON


# ---------- tracking which messages this tool posted ----------

def _load_tracked() -> list:
    if not os.path.exists(TRACKED_JSON):
        return []
    try:
        with open(TRACKED_JSON, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_tracked(tracked: list):
    os.makedirs(os.path.dirname(TRACKED_JSON) or ".", exist_ok=True)
    tmp = TRACKED_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tracked, f, indent=2)
    os.replace(tmp, TRACKED_JSON)


def _track(channel: str, ts: str, label: str):
    tracked = _load_tracked()
    tracked.append({"channel": channel, "ts": ts, "label": label})
    _save_tracked(tracked)


# ---------- building + posting a throwaway test image ----------

def _make_test_png(size: int = 160, color=(88, 196, 130)) -> bytes:
    """Builds a tiny solid-color PNG by hand (no Pillow dependency) -- just
    enough for Slack to accept it as a real image file."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit depth, RGB
    row = bytes(color) * size
    raw = (b"\x00" + row) * size  # filter-type-0 prefix per scanline
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _find_recent_message_ts(client, caption: str):
    """files_upload_v2's own response doesn't reliably carry the resulting
    message's ts across slack_sdk versions, so just look it up by caption
    text -- more robust than parsing upload-response internals."""
    hist = client.conversations_history(channel=config.SNIPES_CHANNEL_ID, limit=10)
    for msg in hist.get("messages", []):
        if msg.get("text", "").strip() == caption.strip():
            return msg["ts"]
    return None


def _fetch_message(client, channel: str, ts: str) -> dict:
    """Look the message back up by ts so we get Slack's own "files" field --
    far more robust than trying to parse a file object out of the upload
    API's own response, which varies across slack_sdk versions."""
    hist = client.conversations_history(channel=channel, latest=ts, oldest=ts, inclusive=True, limit=1)
    msgs = hist.get("messages", [])
    return msgs[0] if msgs else {}


def _post_photo(client, caption: str, label: str, sniper: str):
    """Posts a throwaway test photo, tracks it for /cleartest, then runs it
    through detection.handle_message() as if `sniper` had posted it. Returns
    the resulting entry, or None if anything along the way failed."""
    png_bytes = _make_test_png()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        path = f.name
    try:
        client.files_upload_v2(
            channel=config.SNIPES_CHANNEL_ID, file=path, filename="snipe_test.png",
            initial_comment=caption,
        )
    finally:
        os.unlink(path)

    time.sleep(1.5)  # give Slack a moment to finish posting the message for this upload
    ts = _find_recent_message_ts(client, caption)
    if not ts:
        print(f"[testing] posted '{label}' but couldn't confirm its ts -- "
              f"skipping classification, and /cleartest won't find it")
        return None

    _track(config.SNIPES_CHANNEL_ID, ts, label)
    message = _fetch_message(client, config.SNIPES_CHANNEL_ID, ts)
    if not message:
        print(f"[testing] posted '{label}' but couldn't re-fetch it -- skipping classification")
        return None

    event = {**message, "channel": config.SNIPES_CHANNEL_ID, "user": sniper}
    detection.handle_message(event, client)
    return json_store.find_snipe_by_message(config.SNIPES_CHANNEL_ID, ts)


def _post_text(client, text: str, label: str, sniper: str) -> str:
    """Posts a text-only test message and runs it through handle_message()
    too, for consistency -- it's a no-op either way since there's no image,
    which is exactly what case 4 is checking. Returns the message's ts."""
    resp = client.chat_postMessage(channel=config.SNIPES_CHANNEL_ID, text=text)
    ts = resp["ts"]
    _track(config.SNIPES_CHANNEL_ID, ts, label)
    event = {"channel": config.SNIPES_CHANNEL_ID, "user": sniper, "ts": ts, "text": text}
    detection.handle_message(event, client)
    return ts


# ---------- the six test cases ----------

def run_test_batch(client) -> str:
    bot_id = client.auth_test()["user_id"]
    sniper = config.ZEKE_USER_ID
    lines = [f"Bot user ID is {bot_id}. Posting 6 test messages to <#{config.SNIPES_CHANNEL_ID}>, "
             f"attributed to <@{sniper}> as the sniper, with the bot itself as the sniped party throughout "
             f"so nobody's real ranking is touched..."]

    entry1 = _post_photo(client, f"<@{bot_id}> snipe", "1. mention + snipe keyword", sniper)
    lines.append(f"1. mention + snipe keyword -> got '{entry1['kind'] if entry1 else 'FAILED'}', expected 'snipe'")

    entry2 = _post_photo(client, f"<@{bot_id}> snope", "2. mention + snope keyword", sniper)
    lines.append(f"2. mention + snope keyword -> got '{entry2['kind'] if entry2 else 'FAILED'}', expected 'snope'")

    entry3 = _post_photo(client, f"<@{bot_id}>", "3. bare mention only", sniper)
    lines.append(f"3. bare mention only -> got '{entry3['kind'] if entry3 else 'FAILED'}', expected 'likely_snipe'")

    ts4 = _post_text(client, f"<@{bot_id}> no photo test", "4. mention, no photo", sniper)
    no_entry = json_store.find_snipe_by_message(config.SNIPES_CHANNEL_ID, ts4)
    lines.append(f"4. mention, no photo -> {'no entry created (correct)' if not no_entry else 'FAILED -- an entry was created'}")

    # --- do-not-snipe case: same mention as case 1, but the bot is briefly
    # DNS-listed so _check_do_not_snipe actually fires this time ---
    already_dns = json_store.is_dns(bot_id)
    if not already_dns:
        json_store.add_dns(bot_id)
    try:
        entry5 = _post_photo(client, f"<@{bot_id}> snipe", "5. do-not-snipe (bot is DNS-listed)", sniper)
        if entry5:
            lines.append(
                f"5. snipe of the bot while it's do-not-snipe-listed -> got '{entry5['kind']}', expected 'snipe' -> "
                f"check #snipes for the public 'that's a sNOPE' thread reply, and check <@{sniper}>'s DMs for "
                f"'please delete your recent snipe' -- THAT DM is the actual thing being tested."
            )
        else:
            lines.append("5. FAILED -- couldn't confirm the photo posted, skipping the do-not-snipe check.")
    finally:
        if not already_dns:
            json_store.remove_dns(bot_id)

    entry6 = _post_photo(client, "just a photo, nothing else", "6. no mention at all", sniper)
    lines.append(f"6. no mention at all -> got '{entry6['kind'] if entry6 else 'FAILED'}', expected 'unidentified'")

    lines.append("Done -- reactions should already be on the messages in #snipes. Run /cleartest "
                 "(or the Scripts tab) once you're satisfied.")
    return "\n".join(lines)


def clear_test_batch(client) -> str:
    tracked = _load_tracked()
    if not tracked:
        return "Nothing tracked -- either /test hasn't been run, or it's already been cleared."

    removed_messages, removed_entries, removed_warnings = 0, 0, 0
    for item in tracked:
        channel, ts = item["channel"], item["ts"]

        # Do-not-snipe warnings (thread reply + record) -- popped explicitly
        # here rather than relying on the live bot's own message_deleted
        # listener, since this may run as a standalone script with no live
        # listener attached at all.
        for warning in json_store.pop_dns_warnings_for_snipe(channel, ts):
            try:
                client.chat_delete(channel=warning["warning_channel"], ts=warning["warning_ts"])
                removed_warnings += 1
            except Exception:
                pass

        entry = json_store.find_snipe_by_message(channel, ts)
        if entry:
            if entry.get("photo_path") and os.path.exists(entry["photo_path"]):
                try:
                    os.remove(entry["photo_path"])
                except OSError:
                    pass
            xlsx_store.delete_true_log_row(entry["id"])
            json_store.delete_snipe(entry["id"])
            removed_entries += 1

        try:
            client.chat_delete(channel=channel, ts=ts)
            removed_messages += 1
        except Exception:
            pass

    _save_tracked([])
    return (f"Cleared {removed_messages} test message(s), {removed_warnings} do-not-snipe warning(s), "
            f"and {removed_entries} log entr(y/ies).")
