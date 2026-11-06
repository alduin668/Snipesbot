"""
Snipe detection -- per-photo model.

Every image posted in #snipes is classified independently, using ONLY that
message's own caption text:

    - snope keyword found         -> kind="snope"         (purple dot)
    - snipe keyword + @mention    -> kind="snipe"         (green dot)
    - bare @mention, no other text -> kind="likely_snipe"  (orange dot)
    - anything else               -> kind="unidentified"  (red "?" dot)

There is no "sandwich" logic (before/photo/after from the same author)
deciding classification -- that was the source of a nasty bug: two photos
posted back-to-back would bleed into each other's context and one of them
could get finalized on the wrong data. Every photo now stands on its own.

Neighboring messages still get logged alongside a pending photo, but only
as a hint for a human reviewer (see bot/review.py) -- they never change
kind/sniped automatically.

This module is the one place that owns an entry's classification and Slack
reaction state. Every caller that resolves, corrects, or recounts a photo
(bot/reactions.py, bot/review.py, bot/blocking.py's thread-reply handler,
the webapp's review queue, scripts/recount.py, ...) funnels through the
functions in the "entry points" and "shared resolution path" sections
below, so the actual state-machine logic exists exactly once.
"""
import os
import re
import shutil
import threading
import uuid
from datetime import datetime

import requests

import config
from storage import archive_store, json_store, user_names

MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")

# A real Slack user id: upper-case alphanumeric, starting with U (or W for
# some legacy/shared-channel accounts). Used to tell a real account apart
# from an arbitrary custom sniper/sniped string entered on the Special tab
# (e.g. "deer") -- custom strings are exempt from anything that assumes a
# real account exists (Slack DMs, users.info lookups, do-not-snipe checks).
_SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{2,}$")


def _looks_like_slack_id(value: str) -> bool:
    return bool(value) and bool(_SLACK_ID_RE.match(value))


# Kinds that still need a human decision.
PENDING_KINDS = ("unidentified", "likely_snipe")

# channel_id -> list of {"entry_id", "needed"}
_awaiting_after_context = {}

# entry_id -> threading.Timer
_context_timers = {}

_bot_user_id = None


# ---------- classification primitives ----------

def _contains_snope_keyword(text: str) -> bool:
    text = (text or "").lower()
    return any(kw in text for kw in config.SNOPE_KEYWORDS)


def _contains_snipe_keyword(text: str) -> bool:
    text = (text or "").lower()
    return any(kw in text for kw in config.SNIPE_KEYWORDS)


def _is_bare_mention_caption(text: str) -> bool:
    """True if, after stripping out @mention tokens, nothing else is left."""
    stripped = MENTION_RE.sub("", text or "").strip()
    return stripped == ""


def _extract_mentions(text: str, exclude_user: str = None) -> list:
    mentions = MENTION_RE.findall(text or "")
    seen, out = set(), []

    for m in mentions:
        if m == exclude_user:
            continue
        if m not in seen:
            seen.add(m)
            out.append(m)

    return out


def _classify(text: str, sniper: str):
    """Returns (kind, sniped_list) using ONLY the given text.

    A snope keyword always wins.

    Otherwise:
      - @mention + snipe keyword -> confirmed "snipe"
      - @mention alone -> "likely_snipe"
      - @mention + ordinary text -> "unidentified"
      - no mention -> "unidentified"
    """
    if _contains_snope_keyword(text):
        return "snope", _extract_mentions(
            text,
            exclude_user=sniper,
        )

    sniped = _extract_mentions(
        text,
        exclude_user=sniper,
    )

    if not sniped:
        return "unidentified", []

    if _contains_snipe_keyword(text):
        return "snipe", sniped

    if _is_bare_mention_caption(text):
        return "likely_snipe", sniped

    return "unidentified", []


def _all_media_files(event: dict) -> list:
    """Every image/video file attached to a message, in Slack's own order.
    A single post can carry more than one (two photos, a photo and a
    video, etc) -- the first is always what gets treated as "the" photo
    everywhere (downloaded to disk, classified, shown as the main image);
    the rest ride along as entry["extra_files"]/context "extra_files",
    lazy-loadable the same way context images already are (see
    _slim_file() and web/routes.py's /api/slack-file proxy)."""
    return [
        f for f in (event.get("files", []) or [])
        if f.get("mimetype", "").startswith("image/") or f.get("mimetype", "").startswith("video/")
    ]


def _has_image(event: dict):
    """Despite the name, also matches video files (a "video snipe") -- kept
    under this name rather than renamed since it's called from several
    other modules (scripts/backfill.py, scripts/recount.py,
    bot/testing.py). Returns only the first media file; use
    _all_media_files() for the complete set."""
    files = _all_media_files(event)
    return files[0] if files else None


def _slim_file(file_obj: dict):
    """Trim a Slack file object down to just what the webapp needs to lazy-
    load a context-message image (or video) later (see web/routes.py's
    /api/slack-file proxy). We deliberately don't store url_private -- those
    URLs are tied to a short-lived session and can go stale -- so the proxy
    always re-asks Slack for a fresh one via files.info, keyed on this
    stable file id."""
    if not file_obj:
        return None
    return {
        "id": file_obj.get("id"),
        "name": file_obj.get("name"),
        "is_video": file_obj.get("mimetype", "").startswith("video/"),
    }


def _context_msg(m: dict) -> dict:
    """Shape a raw Slack message into what we persist for context_before/
    context_after -- same shape whether it came from the live per-channel
    buffer or a historical Slack API fetch. "file" is the first attached
    image/video (if any); "extra_files" holds any additional ones from the
    same message (see _all_media_files())."""
    files = _all_media_files(m)
    return {
        "user": m.get("user"),
        "text": m.get("text", ""),
        "ts": m.get("ts"),
        "file": _slim_file(files[0]) if files else None,
        "extra_files": [_slim_file(f) for f in files[1:]],
    }


def _context_from_archive(channel: str, ts: str) -> tuple:
    """(context_before, context_after), each up to CONTEXT_WINDOW messages,
    for a photo -- a thin convenience wrapper over _fetch_context_before()/
    _fetch_context_after() for callers that just want "the normal amount"
    rather than a specific count. Since those now read the permanent local
    archive rather than the live in-memory buffer or a live Slack call,
    this works the same way for ANY photo, live or historical, and is
    immune to the bot process ever restarting. "after" context still can't
    be known the instant a brand new live photo is created (nothing's
    happened after it yet in real time), so that half is only ever filled
    in later -- by the usual live capture window, or by recount_all()'s
    retroactive backfill.

    Used by record_historical_photo() for newly discovered photos, and by
    recount_all() to retroactively backfill any entry recorded with empty
    context (because its neighborhood wasn't archived yet at the time, or
    because it predates this function existing at all)."""
    before = _fetch_context_before(channel, ts, None, config.CONTEXT_WINDOW)
    after = _fetch_context_after(channel, ts, None, config.CONTEXT_WINDOW)
    return before, after


def _reaction_for_kind(kind: str) -> str:
    return {
        "snipe": config.SNIPE_EMOJI,
        "snope": config.SNOPE_EMOJI,
        "unidentified": config.UNIDENTIFIED_EMOJI,
        "likely_snipe": config.LIKELY_SNIPE_EMOJI,
        "not_a_snipe": config.NOT_A_SNIPE_EMOJI,
        "incorrect_snipe": config.INCORRECT_SNIPE_EMOJI,
        "special": config.SPECIAL_EMOJI,
    }[kind]


def _classification_emojis() -> list:
    """All state reactions the bot may have placed."""
    emojis = [
        config.SNIPE_EMOJI,
        config.SNOPE_EMOJI,
        config.UNIDENTIFIED_EMOJI,
        config.LIKELY_SNIPE_EMOJI,
        config.NOT_A_SNIPE_EMOJI,
        config.INCORRECT_SNIPE_EMOJI,
        config.SPECIAL_EMOJI,
        "thumbsup",
    ]

    return list(dict.fromkeys(emojis))


def _sync_reaction_state(client, channel: str, ts: str, kind: str):
    """Make the bot's classification reaction match `kind`.

    Only reactions owned by the bot are removed. Other users' reactions
    are never touched.
    """
    global _bot_user_id

    wanted = _reaction_for_kind(kind)

    try:
        if _bot_user_id is None:
            _bot_user_id = client.auth_test().get("user_id")

        response = client.reactions_get(
            channel=channel,
            timestamp=ts,
        )

        reactions = response.get(
            "message",
            {},
        ).get(
            "reactions",
            [],
        )

        bot_has_wanted = False

        for reaction in reactions:
            emoji = reaction.get("name")
            users = set(reaction.get("users", []))

            if emoji == wanted and _bot_user_id in users:
                bot_has_wanted = True

            elif (
                emoji in _classification_emojis()
                and _bot_user_id in users
            ):
                try:
                    client.reactions_remove(
                        channel=channel,
                        timestamp=ts,
                        name=emoji,
                    )
                except Exception:
                    pass

        if not bot_has_wanted:
            _react(
                client,
                channel,
                ts,
                wanted,
            )

        return

    except Exception:
        # Fallback if reactions_get/auth_test fails.
        for emoji in _classification_emojis():
            if emoji == wanted:
                continue

            try:
                client.reactions_remove(
                    channel=channel,
                    timestamp=ts,
                    name=emoji,
                )
            except Exception:
                pass

        _react(
            client,
            channel,
            ts,
            wanted,
        )


# ---------- Slack / IO helpers ----------

def _download_photo(file_obj: dict, entry_id: str) -> str:
    """Saves the entry's own copy of its primary photo/video under
    PHOTOS_DIR, named by entry id (not file id) -- this is the file the
    webapp serves directly from /photos/<name>. By the time this runs for
    a live photo, bot/archiver.py has almost always already downloaded this
    same file into the permanent archive (see app.py's on_message), so
    this just copies those bytes rather than re-fetching from Slack. If it
    hasn't been archived yet (e.g. a photo discovered by a historical scan
    whose archiving pass hasn't reached it), this falls back to fetching it
    directly, same as before -- and archives it while it's at it, so
    nothing else has to redo that fetch later."""
    os.makedirs(
        config.PHOTOS_DIR,
        exist_ok=True,
    )

    ext = (
        os.path.splitext(
            file_obj.get("name", "")
        )[1]
        or (
            ".mp4" if file_obj.get("mimetype", "").startswith("video/") else ".jpg"
        )
    )

    local_path = os.path.join(
        config.PHOTOS_DIR,
        f"{entry_id}{ext}",
    )

    file_id = file_obj.get("id")
    archived_path = archive_store.file_path(file_id) if file_id else None
    if archived_path:
        shutil.copyfile(archived_path, local_path)
        return local_path

    url = (
        file_obj.get("url_private_download")
        or file_obj.get("url_private")
    )

    headers = {
        "Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"
    }

    resp = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    resp.raise_for_status()

    with open(local_path, "wb") as f:
        f.write(resp.content)

    if file_id:
        archive_store.save_file(file_id, file_obj.get("name", ""), file_obj.get("mimetype", ""), resp.content)

    return local_path


def _message_permalink(client, channel: str, ts: str) -> str:
    try:
        r = client.chat_getPermalink(
            channel=channel,
            message_ts=ts,
        )

        return r.get("permalink", "")

    except Exception:
        return ""


def _react(client, channel, ts, emoji):
    try:
        client.reactions_add(
            channel=channel,
            timestamp=ts,
            name=emoji,
        )
    except Exception:
        pass


# ---------- context fetching (webapp review cards) ----------
#
# Context is read from the permanent local archive (storage/archive_store.py,
# bot/archiver.py), not a live Slack call -- so it works the same way for a
# photo from five minutes ago or five months ago, regardless of Slack's own
# retention window, as long as the period in question has already been
# archived. Still fetched lazily rather than eagerly for every historical
# photo during backfill/recount (record_historical_photo() and
# recount_all() both do try an initial archive-backed fill, but the webapp
# asks again itself the first time a card is actually opened for review --
# see ensure_initial_context(), called from GET /api/entries/<id>/context --
# in case more of the archive has filled in since). The result is cached
# back into snipes.json either way, so it's only ever recomputed once per
# entry unless the entry has nothing yet. "Load more" (load_more_context)
# always re-reads the archive fresh, since it's cheap now.

def _archived_messages_for_channel(channel: str) -> list:
    """Every archived message for `channel`, in chronological order,
    excluding subtyped messages (edits/joins/etc) and anything with no
    user -- the same filtering a conversations_history call always had
    applied for this purpose too."""
    return [
        m for m in archive_store.all_messages()
        if m.get("channel") == channel and not m.get("subtype") and m.get("user")
    ]


def _fetch_context_before(channel: str, ts: str, client, count: int) -> list:
    """The `count` archived messages immediately before `ts` (doesn't
    require `ts` itself to be an archived message -- just compares
    timestamps, so a sparse or not-yet-archived neighborhood just yields a
    sparser, never wrong, result). Despite the name (and the now-unused
    `client` param, kept so no caller needed to change), this reads the
    permanent local archive (storage/archive_store.py) rather than making a
    live Slack call -- so unlike before, it keeps working regardless of
    Slack's retention window, as long as the period in question has
    already been archived (see bot/archiver.py)."""
    target = float(ts)
    before = [m for m in _archived_messages_for_channel(channel) if float(m["ts"]) < target]
    return [_context_msg(m) for m in before[-count:]]


def _fetch_context_after(channel: str, ts: str, client, count: int, max_pages: int = 25) -> list:
    """The `count` archived messages immediately after `ts`. Same note as
    _fetch_context_before() -- `client`/`max_pages` are no longer used (no
    more pagination needed against a live API), kept only so no caller
    needed to change."""
    target = float(ts)
    after = [m for m in _archived_messages_for_channel(channel) if float(m["ts"]) > target]
    return [_context_msg(m) for m in after[:count]]


def ensure_initial_context(entry_id: str, client) -> dict:
    """Backfills context_before/context_after for an entry that's never had
    them fetched (any historical entry, plus any live entry whose context
    genuinely came back empty). No-ops -- and doesn't re-hit Slack -- once
    that's been tried for this entry, so opening the same card twice only
    ever costs one Slack round trip. Returns the (possibly updated) entry."""
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry:
        return None

    updates = {}

    if not entry.get("context_before") and not entry.get("context_before_checked"):
        updates["context_before"] = _fetch_context_before(
            entry["channel"], entry["message_ts"], client, config.CONTEXT_WINDOW,
        )
        updates["context_before_checked"] = True

    if not entry.get("context_after") and not entry.get("context_after_checked"):
        updates["context_after"] = _fetch_context_after(
            entry["channel"], entry["message_ts"], client, config.CONTEXT_WINDOW,
        )
        updates["context_after_checked"] = True

    if updates:
        json_store.update_snipe(entry_id, **updates)
        entry.update(updates)

    return entry


def load_more_context(entry_id: str, direction: str, client, count: int = 2) -> list:
    """Fetches `count` more messages beyond what's currently stored, in
    `direction` ("before" or "after"), persists them, and returns just the
    newly-added messages (already in the right order to prepend/append)."""
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry:
        return []

    if direction == "before":
        existing = entry.get("context_before", [])
        anchor_ts = existing[0]["ts"] if existing else entry["message_ts"]
        more = _fetch_context_before(entry["channel"], anchor_ts, client, count)
        json_store.update_snipe(
            entry_id,
            context_before=more + existing,
            context_before_checked=True,
        )
        return more

    if direction == "after":
        existing = entry.get("context_after", [])
        anchor_ts = existing[-1]["ts"] if existing else entry["message_ts"]
        more = _fetch_context_after(entry["channel"], anchor_ts, client, count)
        json_store.update_snipe(
            entry_id,
            context_after=existing + more,
            context_after_checked=True,
        )
        return more

    return []


# ---------- log sync ----------

def _sheet_row(entry: dict, client) -> list:
    if _looks_like_slack_id(entry["sniper"]):
        user_names.ensure_cached(
            entry["sniper"],
            client,
        )

    for u in entry["sniped"]:
        if _looks_like_slack_id(u):
            user_names.ensure_cached(
                u,
                client,
            )

    dt = datetime.fromisoformat(
        entry["date_time"]
    )

    link = _message_permalink(
        client,
        entry["channel"],
        entry["message_ts"],
    )

    return [
        dt.strftime("%Y-%m-%d"),
        dt.strftime("%H:%M:%S"),
        entry["sniper"],
        ", ".join(entry["sniped"]),
        "Yes" if entry["group_snipe"] else "No",
        "Yes" if entry.get("notable") else "No",
        entry.get("kind", ""),
        entry.get("photo_path") or "",
        link,
        entry["id"],
    ]


def _sync_entry_to_log(entry: dict, client):
    """Write/update this entry's row in the true log."""
    from storage import xlsx_store

    try:
        if entry.get("ignored"):
            xlsx_store.delete_true_log_row(
                entry["id"]
            )
        else:
            xlsx_store.upsert_true_log_row(
                entry["id"],
                _sheet_row(entry, client),
            )

    except Exception as e:
        print(
            f"[log] failed to sync entry {entry['id']}: {e}"
        )


def _check_do_not_snipe(client, entry):
    channel = entry["channel"]
    ts = entry["message_ts"]

    violators = [
        u
        for u in entry["sniped"]
        if json_store.is_dns(u)
    ]

    for target in violators:
        if json_store.has_dns_warning(
            channel,
            ts,
            target,
        ):
            continue

        try:
            reply = client.chat_postMessage(
                channel=config.SNIPES_CHANNEL_ID,
                thread_ts=ts,
                text=(
                    f"that's a sNOPE, please do not snipe "
                    f"<@{target}>"
                ),
            )

            client.chat_postMessage(
                channel=entry["sniper"],
                text=(
                    f"Hey — <@{target}> is on the do-not-snipe "
                    f"list. Please delete your recent snipe "
                    f"of them in #snipes."
                ),
            )

            json_store.add_dns_warning(
                channel,
                ts,
                config.SNIPES_CHANNEL_ID,
                reply["ts"],
                entry["sniper"],
                target,
            )

        except Exception as e:
            print(
                f"[dns] failed to send warning: {e}"
            )


# ---------- new-photo path ----------

def _build_entry(
    event: dict,
    files: list,
    kind: str,
    sniped: list,
) -> dict:
    """files is the full ordered list of image/video attachments on this
    message (see _all_media_files()) -- the first becomes "file" (the
    entry's main photo, downloaded to disk by _download_photo()), any rest
    become "extra_files" (slimmed, lazy-loaded via the webapp the same way
    context images already are -- never downloaded to disk)."""
    ts = event["ts"]
    dt = datetime.fromtimestamp(
        float(ts)
    )
    file_obj = files[0] if files else None

    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "sniper": event["user"],
        "sniped": sniped,
        "notable": False,
        "star_users": [],
        "ignored": False,
        "photo_path": None,
        "channel": event["channel"],
        "message_ts": ts,
        "photo_text": event.get("text", ""),
        # The archive (not the in-memory deque) is the source of truth for
        # "before" context -- app.py archives this very message before
        # handle_message() ever runs, so it's always available here, and
        # unlike the deque it isn't wiped out every time the bot process
        # restarts.
        "context_before": _context_from_archive(event["channel"], event["ts"])[0],
        "context_after": [],
        "context_ready": False,
        "file": file_obj,
        "extra_files": [_slim_file(f) for f in files[1:]],
        "date_time": dt.isoformat(),
        "day_of_week": dt.strftime("%A"),
        "group_snipe": len(sniped) > 1,
        "status": kind,
        "manually_resolved": False,
    }


def _finalize_new_photo(event, files, client):
    """Record a new photo and put its state reaction on Slack. files is the
    full ordered list of image/video attachments on this message (see
    _all_media_files()) -- only the first ever gets downloaded to disk."""
    existing = json_store.find_snipe_by_message(
        event["channel"],
        event["ts"],
    )

    if existing:
        return existing

    kind, sniped = _classify(
        event.get("text", ""),
        event["user"],
    )

    entry = _build_entry(
        event,
        files,
        kind,
        sniped,
    )

    json_store.add_snipe(entry)

    _sync_reaction_state(
        client,
        entry["channel"],
        entry["message_ts"],
        kind,
    )

    try:
        entry["photo_path"] = _download_photo(
            files[0],
            entry["id"],
        )

        json_store.update_snipe(
            entry["id"],
            photo_path=entry["photo_path"],
        )

    except Exception:
        entry["photo_path"] = None

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )

    elif kind in PENDING_KINDS:
        _start_context_capture(entry)

    return entry


def record_historical_photo(message: dict, client):
    """Idempotently add a historical Slack photo."""
    channel = message["channel"]
    ts = message["ts"]

    existing = json_store.find_snipe_by_message(
        channel,
        ts,
    )

    if existing:
        # The caption/file get refreshed from whatever Slack (now the
        # archive) currently has either way -- same as reevaluate_edit()
        # does for a LIVE edit. This matters even for a manually_resolved
        # entry: an edit made while the bot was offline is only ever seen
        # here, via this refresh on the next archive sync/backfill pass,
        # never as a live message_changed event -- so if this skipped
        # manually_resolved entries, their cached caption would go stale
        # forever. Reclassification itself is still never touched here;
        # that's recount_all()'s job, and it already leaves manual
        # decisions alone.
        files = _all_media_files(message)
        file_obj = files[0] if files else None
        extra_files = [_slim_file(f) for f in files[1:]]

        json_store.update_snipe(
            existing["id"],
            photo_text=message.get("text", ""),
            file=file_obj,
            extra_files=extra_files,
        )

        existing["photo_text"] = message.get(
            "text",
            "",
        )

        existing["file"] = file_obj
        existing["extra_files"] = extra_files

        return existing, False

    files = _all_media_files(message)

    if not files:
        return None, False

    kind, sniped = _classify(
        message.get("text", ""),
        message["user"],
    )

    event = {
        "channel": channel,
        "ts": ts,
        "user": message["user"],
        "text": message.get("text", ""),
    }

    entry = _build_entry(
        event,
        files,
        kind,
        sniped,
    )

    before, after = _context_from_archive(channel, ts)
    entry["context_before"] = before
    entry["context_after"] = after
    entry["context_ready"] = True

    try:
        entry["photo_path"] = _download_photo(
            files[0],
            entry["id"],
        )
    except Exception:
        entry["photo_path"] = None

    json_store.add_snipe(entry)

    _sync_reaction_state(
        client,
        channel,
        ts,
        kind,
    )

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )

    return entry, True


# ---------- context capture ----------

def _start_context_capture(entry: dict):
    channel = entry["channel"]

    waiters = _awaiting_after_context.setdefault(
        channel,
        [],
    )

    waiters.append(
        {
            "entry_id": entry["id"],
            "needed": config.CONTEXT_WINDOW,
        }
    )

    timer = threading.Timer(
        config.CONTEXT_CAPTURE_DELAY_SECONDS,
        _timeout_context_capture,
        args=(entry["id"],),
    )

    timer.daemon = True

    _context_timers[entry["id"]] = timer

    timer.start()


def _timeout_context_capture(entry_id: str):
    _context_timers.pop(
        entry_id,
        None,
    )

    entry = json_store.find_snipe_by_id(
        entry_id
    )

    if (
        not entry
        or entry.get("kind") not in PENDING_KINDS
        or entry.get("context_ready")
    ):
        return

    json_store.update_snipe(
        entry_id,
        context_ready=True,
    )

    _notify_reviewer_if_enabled(
        entry_id
    )


def _cancel_context_capture(entry_id: str):
    """Cancel pending context capture."""
    timer = _context_timers.pop(
        entry_id,
        None,
    )

    if timer:
        timer.cancel()

    for channel, waiters in list(
        _awaiting_after_context.items()
    ):
        remaining = [
            w
            for w in waiters
            if w["entry_id"] != entry_id
        ]

        if remaining:
            _awaiting_after_context[channel] = remaining
        else:
            _awaiting_after_context.pop(
                channel,
                None,
            )


def _record_after_context(channel: str, event: dict):
    waiters = _awaiting_after_context.get(
        channel
    )

    if not waiters:
        return

    still_waiting = []

    for w in waiters:
        entry = json_store.find_snipe_by_id(
            w["entry_id"]
        )

        if (
            not entry
            or entry.get("kind") not in PENDING_KINDS
        ):
            continue

        context_after = entry.get(
            "context_after",
            [],
        )

        context_after.append(_context_msg(event))

        json_store.update_snipe(
            entry["id"],
            context_after=context_after,
        )

        w["needed"] -= 1

        if w["needed"] > 0:
            still_waiting.append(w)

        else:
            timer = _context_timers.pop(
                entry["id"],
                None,
            )

            if timer:
                timer.cancel()

            json_store.update_snipe(
                entry["id"],
                context_ready=True,
            )

            _notify_reviewer_if_enabled(
                entry["id"]
            )

    if still_waiting:
        _awaiting_after_context[channel] = still_waiting
    else:
        _awaiting_after_context.pop(
            channel,
            None,
        )


def _notify_reviewer_if_enabled(entry_id: str):
    from bot import review

    review.maybe_notify_reviewer(
        entry_id
    )


# ---------- shared resolution path ----------

def resolve_entry(
    entry_id: str,
    kind: str,
    client,
    sniped: list = None,
):
    """Resolve a pending entry."""
    entry = json_store.find_snipe_by_id(
        entry_id
    )

    if (
        not entry
        or entry.get("kind") not in PENDING_KINDS
    ):
        return None

    if kind not in (
        "snipe",
        "snope",
        "not_a_snipe",
    ):
        return None

    if kind == "snipe" and sniped is None:
        sniped = (
            entry.get("sniped")
            or _extract_mentions(
                entry.get("photo_text", ""),
                exclude_user=entry["sniper"],
            )
        )
    else:
        sniped = sniped or []

    _cancel_context_capture(
        entry_id
    )

    json_store.update_snipe(
        entry_id,
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        status=kind,
        manually_resolved=True,
    )

    entry.update(
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        status=kind,
        manually_resolved=True,
    )

    if (
        kind not in PENDING_KINDS
        and kind != "not_a_snipe"
        and not entry.get("photo_path")
    ):
        _try_download(entry)

    _sync_reaction_state(
        client,
        entry["channel"],
        entry["message_ts"],
        kind,
    )

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )

    return entry


def mark_manual_kind(entry_id: str, kind: str, client, sniped: list = None):
    """Apply an explicit Zeke classification regardless of current state.

    `sniped` is an optional explicit override (e.g. from the webapp's user
    picker) -- pass None to keep the old auto-derived behavior (reuse the
    entry's existing sniped list, or fall back to extracting @mentions from
    the caption)."""
    if kind not in (
        "snipe",
        "snope",
        "likely_snipe",
        "not_a_snipe",
        "unidentified",
        "special",
    ):
        return None

    entry = json_store.find_snipe_by_id(
        entry_id
    )

    if not entry:
        return None

    if sniped is not None:
        sniped = list(sniped)

    elif kind == "snipe":
        sniped = (
            entry.get("sniped")
            or _extract_mentions(
                entry.get("photo_text", ""),
                exclude_user=entry["sniper"],
            )
        )

    elif kind == "likely_snipe":
        sniped = entry.get("sniped", [])

    else:
        sniped = []

    _cancel_context_capture(
        entry_id
    )

    json_store.update_snipe(
        entry_id,
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        status=kind,
        manually_resolved=True,
        ignored=False,
    )

    entry.update(
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        status=kind,
        manually_resolved=True,
        ignored=False,
    )

    if not entry.get("derived_from"):
        _sync_reaction_state(
            client,
            entry["channel"],
            entry["message_ts"],
            kind,
        )

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )

    return entry


# ---------- entry points called from app.py ----------

def handle_message(event: dict, client):
    channel = event.get("channel")
    user = event.get("user")
    ts = event.get("ts")

    if not user or not ts:
        return

    _record_after_context(
        channel,
        event,
    )

    media_files = _all_media_files(event)

    if media_files:
        _finalize_new_photo(
            event,
            media_files,
            client,
        )


def resolve_via_thread_reply(
    channel: str,
    parent_ts: str,
    resolution_text: str,
    client,
) -> bool:
    """
    Zeke replies in-thread on a photo with #snipe (...).
    """
    entry = json_store.find_snipe_by_message(
        channel,
        parent_ts,
    )

    if not entry:
        return False

    if entry.get("manually_resolved"):
        return False

    text_lower = resolution_text.lower()
    old_kind = entry.get("kind")

    if (
        old_kind in PENDING_KINDS
        and "nas" in text_lower
        and not _extract_mentions(resolution_text)
    ):
        _cancel_context_capture(
            entry["id"]
        )

        json_store.update_snipe(
            entry["id"],
            kind="not_a_snipe",
            status="not_a_snipe",
            manually_resolved=True,
        )

        entry["kind"] = "not_a_snipe"

        _sync_reaction_state(
            client,
            channel,
            parent_ts,
            "not_a_snipe",
        )

        _sync_entry_to_log(
            entry,
            client,
        )

        return True

    combined_text = (
        f"{entry.get('photo_text') or ''} "
        f"{resolution_text}"
    )

    kind, sniped = _classify(
        combined_text,
        entry["sniper"],
    )

    json_store.update_snipe(
        entry["id"],
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        status=kind,
        manually_resolved=True,
    )

    entry.update(
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        manually_resolved=True,
    )

    if old_kind in PENDING_KINDS:
        _cancel_context_capture(
            entry["id"]
        )

        if (
            kind not in PENDING_KINDS
            and not entry.get("photo_path")
        ):
            _try_download(entry)

    _sync_reaction_state(
        client,
        channel,
        parent_ts,
        kind,
    )

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )

    return True


def reevaluate_edit(
    channel: str,
    edited_ts: str,
    new_text: str,
    client,
):
    """
    Called on message_changed.
    """
    entry = json_store.find_snipe_by_message(
        channel,
        edited_ts,
    )

    if not entry:
        return

    if entry.get("manually_resolved"):
        json_store.update_snipe(
            entry["id"],
            photo_text=new_text,
        )

        entry["photo_text"] = new_text

        _sync_entry_to_log(
            entry,
            client,
        )

        return

    old_kind = entry.get("kind")

    kind, sniped = _classify(
        new_text,
        entry["sniper"],
    )

    if (
        kind == old_kind
        and sniped == entry.get("sniped")
    ):
        return

    json_store.update_snipe(
        entry["id"],
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        photo_text=new_text,
        status=kind,
    )

    entry.update(
        kind=kind,
        sniped=sniped,
        group_snipe=len(sniped) > 1,
        photo_text=new_text,
    )

    if old_kind in PENDING_KINDS:
        _cancel_context_capture(
            entry["id"]
        )

        if kind in PENDING_KINDS:
            entry["context_ready"] = True

            json_store.update_snipe(
                entry["id"],
                context_ready=True,
            )

        elif not entry.get("photo_path"):
            _try_download(entry)

    _sync_reaction_state(
        client,
        channel,
        edited_ts,
        kind,
    )

    _sync_entry_to_log(
        entry,
        client,
    )

    if kind == "snipe":
        _check_do_not_snipe(
            client,
            entry,
        )


# ---------- incorrect-snipe correction ----------

# ---------- notable (independent of classification) ----------

def set_notable(entry_id: str, notable: bool, client) -> dict:
    """Manually set the notable flag -- same effect as the star reaction
    reaching its threshold (config.NOTABLE_MIN_OTHER_STARS / Zeke starring
    it), but settable directly from the webapp, on any entry regardless of
    kind. Doesn't touch star_users -- if someone later stars/unstars it in
    Slack, bot/reactions.py's own notable computation will just recompute
    from star_users as normal and can overwrite this."""
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry:
        return None

    json_store.update_snipe(entry_id, notable=notable)
    entry["notable"] = notable

    _sync_entry_to_log(entry, client)

    return entry


def mark_incorrect_snipe(entry_id: str, client):
    """Apply Zeke's incorrect-snipe classification regardless of current state."""
    entry = json_store.find_snipe_by_id(
        entry_id
    )

    if not entry:
        return None

    _cancel_context_capture(
        entry_id
    )

    json_store.update_snipe(
        entry_id,
        kind="incorrect_snipe",
        status="incorrect_snipe",
        sniped=[],
        group_snipe=False,
        manually_resolved=True,
        ignored=False,
    )

    entry.update(
        kind="incorrect_snipe",
        status="incorrect_snipe",
        sniped=[],
        group_snipe=False,
        manually_resolved=True,
        ignored=False,
    )

    if not entry.get("derived_from"):
        _sync_reaction_state(
            client,
            entry["channel"],
            entry["message_ts"],
            "incorrect_snipe",
        )

    _sync_entry_to_log(
        entry,
        client,
    )

    return entry


# ---------- special category (too-niche-for-code manual entries) ----------

def create_special_snipe(parent_id: str, kind: str, sniper: str, sniped: list, client):
    """Creates a brand-new, independent entry derived from a "special"
    (fingerprint-reacted) photo -- for cases too niche for the detector to
    ever handle on its own: two unrelated snipes bundled into one photo, a
    snope of something that isn't a person, etc. One special photo can
    spawn any number of these; the parent "special" entry itself is left
    completely untouched (it isn't a snipe/snope/etc itself and is never
    counted in any stat), so it keeps showing on the Special tab, growing a
    list of everything that's been pulled out of it, until it's explicitly
    dismissed.

    sniper/sniped can be real Slack user ids OR arbitrary free-text strings
    (e.g. "deer") -- anything that isn't a real account per
    _looks_like_slack_id() is just displayed as-is everywhere (no
    <@mention> rendering, no DM, no users.info lookup, no do-not-snipe
    check attempted for it)."""
    if kind not in ("snipe", "snope", "not_a_snipe", "incorrect_snipe"):
        return None

    parent = json_store.find_snipe_by_id(parent_id)
    if not parent:
        return None

    sniper = (sniper or "").strip()
    if not sniper:
        return None

    if kind in ("snipe", "snope"):
        sniped = [s.strip() for s in (sniped or []) if s and s.strip()]
        if not sniped:
            return None
    else:
        # not_a_snipe / incorrect_snipe never carry a victim list, same as
        # everywhere else those kinds are ever set.
        sniped = []

    dt = datetime.fromisoformat(parent["date_time"])

    entry = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "sniper": sniper,
        "sniped": sniped,
        "notable": False,
        "star_users": [],
        "ignored": False,
        "photo_path": parent.get("photo_path"),
        "channel": parent["channel"],
        "message_ts": parent["message_ts"],
        "photo_text": parent.get("photo_text", ""),
        "context_before": [],
        "context_after": [],
        "context_ready": True,
        "file": parent.get("file"),
        "extra_files": parent.get("extra_files", []),
        "date_time": parent["date_time"],
        "day_of_week": dt.strftime("%A"),
        "group_snipe": len(sniped) > 1,
        "status": kind,
        "manually_resolved": True,
        "derived_from": parent_id,
    }

    json_store.add_snipe(entry)
    _sync_entry_to_log(entry, client)

    if kind == "snipe":
        _check_do_not_snipe(client, entry)

    return entry


def dismiss_special(entry_id: str, client):
    """Closes out a "special" entry with no derived snipe -- Zeke looked at
    it and decided there's nothing worth logging from it. Reclassifies it
    as not_a_snipe (same as any other "there's nothing here" resolution),
    which drops it out of the Special tab's open queue."""
    return mark_manual_kind(entry_id, "not_a_snipe", client)


# ---------- recount ----------

def recount_all(client, progress=print) -> int:
    """Re-apply the current detector to every stored photo.

    Manual decisions are immutable. Every other entry is classified from the
    current rules, and every photo gets its Slack reaction normalized.
    """
    changed = 0
    processed = 0

    for entry in json_store.load_snipes():

        # Fill in context retroactively from the archive for anything that
        # doesn't have any yet -- covers every entry that predates
        # _context_from_archive() existing, any historical photo recorded
        # before its neighbors had been archived, and special/derived
        # entries alike. Harmless no-op for anything genuinely isolated
        # (nothing nearby in the archive either).
        if not entry.get("context_before") and not entry.get("context_after"):
            before, after = _context_from_archive(entry["channel"], entry["message_ts"])
            if before or after:
                entry["context_before"] = before
                entry["context_after"] = after
                json_store.update_snipe(
                    entry["id"],
                    context_before=before,
                    context_after=after,
                )

        # Legacy fingerprint/"ignored" entries now mean NAS.
        if entry.get("ignored"):
            old_kind = entry.get("kind")

            entry["kind"] = "not_a_snipe"
            entry["status"] = "not_a_snipe"
            entry["ignored"] = False
            entry["sniped"] = []
            entry["group_snipe"] = False
            entry["manually_resolved"] = True

            json_store.update_snipe(
                entry["id"],
                kind="not_a_snipe",
                status="not_a_snipe",
                ignored=False,
                sniped=[],
                group_snipe=False,
                manually_resolved=True,
            )

            _sync_reaction_state(
                client,
                entry["channel"],
                entry["message_ts"],
                "not_a_snipe",
            )

            _sync_entry_to_log(
                entry,
                client,
            )

            progress(
                f"[recount] {entry['id']}: "
                f"{old_kind} / ignored -> not_a_snipe"
            )

            changed += 1
            processed += 1
            continue

        # Legacy NAS/incorrect entries are inherently manual.
        if (
            entry.get("kind")
            in ("not_a_snipe", "incorrect_snipe")
            and not entry.get("manually_resolved")
        ):
            entry["manually_resolved"] = True

            json_store.update_snipe(
                entry["id"],
                manually_resolved=True,
            )

        # Explicit historical manual states are protected forever. Derived
        # Special-tab entries (see create_special_snipe()) share their
        # Slack message with their parent "special" entry -- the parent's
        # fingerprint reaction is the only one that should ever show there,
        # so a derived entry's own kind never touches the Slack reaction.
        if entry.get("manually_resolved"):
            if not entry.get("derived_from"):
                _sync_reaction_state(
                    client,
                    entry["channel"],
                    entry["message_ts"],
                    entry["kind"],
                )

            processed += 1
            continue

        old_kind = entry.get("kind")
        old_sniped = entry.get("sniped", [])

        kind, sniped = _classify(
            entry.get("photo_text", ""),
            entry["sniper"],
        )

        if (
            kind != old_kind
            or sniped != old_sniped
        ):
            json_store.update_snipe(
                entry["id"],
                kind=kind,
                sniped=sniped,
                group_snipe=len(sniped) > 1,
                status=kind,
            )

            entry.update(
                kind=kind,
                sniped=sniped,
                group_snipe=len(sniped) > 1,
            )

            changed += 1

            progress(
                f"[recount] {entry['id']}: "
                f"{old_kind} -> {kind}"
            )

        _sync_reaction_state(
            client,
            entry["channel"],
            entry["message_ts"],
            kind,
        )

        if (
            kind not in PENDING_KINDS
            and kind != "not_a_snipe"
            and not entry.get("photo_path")
        ):
            _try_download(entry)

        if (
            kind != old_kind
            or sniped != old_sniped
        ):
            _sync_entry_to_log(
                entry,
                client,
            )

        if kind == "snipe":
            _check_do_not_snipe(
                client,
                entry,
            )

        processed += 1

    progress(
        f"[recount] complete: processed "
        f"{processed}, changed {changed}"
    )

    return changed


def _try_download(entry: dict):
    try:
        path = _download_photo(
            entry["file"],
            entry["id"],
        )

        entry["photo_path"] = path

        json_store.update_snipe(
            entry["id"],
            photo_path=path,
        )

    except Exception:
        pass