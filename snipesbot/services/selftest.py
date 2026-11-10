"""
/test and /cleartest: post a battery of synthetic photos and drive them
through the real detection path, so a rules change can be sanity-checked by
eye in Slack.

Why it feeds the message in directly instead of waiting for the live
listener: the listener ignores events carrying a bot_id (so the bot never
reacts to itself), and everything posted here is posted by the bot. So the
message is posted (so you can SEE it), fetched back (to get Slack's own
`files` field), and handed to `record_photo` as if the sniper had posted it.

Every case mentions the bot itself as the sniped party, so a test can never
touch a real person's ranking even if cleanup is forgotten. Set
TEST_CHANNEL_ID to run it somewhere other than the live channel.
"""
from __future__ import annotations

import os
import struct
import tempfile
import time
import zlib

from ..env import Env
from . import archiver, dns
from . import entries as entry_service


def _make_test_png(size: int = 160, color=(88, 196, 130)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = (b"\x00" + bytes(color) * size) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _post_photo(env: Env, channel: str, caption: str, label: str, sniper: str):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_make_test_png())
        path = f.name
    try:
        env.slack.files_upload_v2(channel=channel, file=path, filename="snipe_test.png", initial_comment=caption)
    finally:
        os.unlink(path)
    time.sleep(1.5)
    ts = None
    for msg in env.slack.conversations_history(channel=channel, limit=10).get("messages", []):
        if (msg.get("text") or "").strip() == caption.strip():
            ts = msg["ts"]
            break
    if not ts:
        return None
    env.store.tests.add(channel, ts, label)
    hist = env.slack.conversations_history(channel=channel, latest=ts, oldest=ts, inclusive=True, limit=1)
    msgs = hist.get("messages", [])
    if not msgs:
        return None
    message = {**msgs[0], "channel": channel, "user": sniper}
    archiver.archive_message(env, message)
    entry, _ = entry_service.record_photo(env, message, source="live")
    return entry


def run_test_batch(env: Env) -> str:
    bot = env.slack.auth_test()["user_id"]
    sniper, channel = env.cfg.zeke_user_id, env.cfg.effective_test_channel
    lines = [f"Posting 6 test messages to <#{channel}> as <@{sniper}>, sniping the bot itself so nobody's ranking is touched..."]

    def case(n, caption, label, expected):
        entry = _post_photo(env, channel, caption, f"{n}. {label}", sniper)
        got = entry.kind.value if entry else "FAILED"
        lines.append(f"{n}. {label} -> got '{got}', expected '{expected}'")
        return entry

    case(1, f"<@{bot}> snipe", "mention + snipe keyword", "snipe")
    case(2, f"<@{bot}> snope", "mention + snope keyword", "snope")
    case(3, f"<@{bot}>", "bare mention only", "likely_snipe")

    ts4 = env.slack.chat_postMessage(channel=channel, text=f"<@{bot}> no photo test")["ts"]
    env.store.tests.add(channel, ts4, "4. mention, no photo")
    entry4, _ = entry_service.record_photo(env, {"channel": channel, "ts": ts4, "user": sniper, "text": "x"}, source="live")
    lines.append(f"4. mention, no photo -> {'no entry created (correct)' if entry4 is None else 'FAILED -- an entry was created'}")

    already = env.store.dns.is_listed(bot)
    env.store.dns.add(bot)
    try:
        entry5 = case(5, f"<@{bot}> snipe", "do-not-snipe (bot is DNS-listed)", "snipe")
        if entry5:
            lines.append(f"   check #snipes for the public sNOPE reply and <@{sniper}>'s DMs for the delete request.")
    finally:
        if not already:
            env.store.dns.remove(bot)

    case(6, "just a photo, nothing else", "no mention at all", "unidentified")
    lines.append("Done. Run the clear-test action when you're satisfied.")
    return "\n".join(lines)


def clear_test_batch(env: Env) -> str:
    tracked = env.store.tests.all()
    if not tracked:
        return "Nothing tracked -- either the test hasn't been run, or it's already been cleared."
    messages = entries = warnings = 0
    for item in tracked:
        channel, ts = item["channel"], item["ts"]
        warnings += dns.remove_warnings_for_message(env, channel, ts)
        for e in env.store.entries.for_message(channel, ts):
            env.store.entries.delete(e.id)
            entries += 1
        env.store.archive.purge_message(channel, ts)
        try:
            env.slack.chat_delete(channel=channel, ts=ts)
            messages += 1
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
    env.store.tests.clear()
    return f"Cleared {messages} test message(s), {warnings} warning(s) and {entries} entr(y/ies)."
