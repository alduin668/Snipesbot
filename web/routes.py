"""
HTTP routes for the live-log/admin webapp. Registered onto the Flask app
by web/app.py. Split into groups:

  - Live Log tab     -- read-only view of the XLSX true log
  - Review Queue     -- resolve unidentified/likely_snipe photos from a browser
  - Analysis tab     -- formatted view of analysis/stats.py
  - Matrix tab       -- Coded/Manual/True sniper x victim matrices
  - Adjustments tab  -- manual per-person score adjustments
  - Scripts tab      -- run scripts/*.py as subprocesses (see scripts_runner.py)
"""
import os
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request, send_from_directory
from slack_sdk import WebClient
import requests

import config
from analysis import stats as analysis
from bot import detection
from bot.detection import MENTION_RE
from storage import archive_store, json_store, user_names, xlsx_store
from web import scripts_runner

bp = Blueprint("snipesbot", __name__)

# Own client, needed to actually resolve entries (swap reactions, sync the
# log) and fetch thread replies from HTTP requests rather than a Bolt
# request/event context.
_slack_client = WebClient(token=config.SLACK_BOT_TOKEN)


@bp.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Scripts tab
# ---------------------------------------------------------------------------

@bp.route("/api/scripts")
def api_scripts():
    return jsonify({
        k: {"label": v["label"], "description": v["description"], "needs_since": v["needs_since"]}
        for k, v in scripts_runner.SCRIPTS.items()
    })


@bp.route("/api/scripts/<name>/run", methods=["POST"])
def api_scripts_run(name):
    if name not in scripts_runner.SCRIPTS:
        return jsonify({"error": "unknown script"}), 404
    try:
        run_id = scripts_runner.start_run(name, since=(request.json or {}).get("since"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"run_id": run_id})


@bp.route("/api/scripts/runs/<run_id>")
def api_scripts_run_status(run_id):
    run = scripts_runner.get_run(run_id)
    if not run:
        return jsonify({"error": "unknown run"}), 404
    return jsonify(run)


# ---------------------------------------------------------------------------
# Review queue tab
# ---------------------------------------------------------------------------

@bp.route("/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(os.path.abspath(config.PHOTOS_DIR), filename)


def _resolve_mentions(text, names):
    """Slack stores inline @mentions in raw message text as <@USERID>; Slack
    itself renders those into names, but this webapp is just static HTML so
    they'd otherwise show up as a literal user ID. Swap each one for the same
    "Name (ID)" format used everywhere else, using the same name cache."""
    if not text:
        return text or ""
    return MENTION_RE.sub(lambda m: user_names.format_side_by_side(m.group(1), names), text)


def _thread_replies(client, channel, ts, names):
    """Real thread replies on the photo message -- distinct from
    context_before/context_after, which are just neighboring channel
    messages. Read from the permanent local archive (storage/
    archive_store.py) rather than a live Slack call, so this keeps working
    regardless of Slack's retention window, as long as the thread has
    already been archived -- live replies are archived the instant they
    arrive (see app.py), and historical ones get swept up by any archive
    sync/backfill pass (bot/archiver.py's archive_thread_replies())."""
    raw = [
        m for m in archive_store.all_messages()
        if m.get("channel") == channel and m.get("thread_ts") == ts and m.get("ts") != ts
    ]
    if not raw:
        return []

    raw.sort(key=lambda m: float(m.get("ts", 0)))

    for m in raw:
        if m.get("user"):
            user_names.ensure_cached(m["user"], client)
    names = user_names.get_cached_names()  # refresh in case a reply author was new

    return [_fmt_msg(m, names) for m in raw]


def _fmt_msg(m, names):
    file_info = m.get("file") or {}
    return {
        "user": user_names.format_side_by_side(m.get("user"), names),
        "text": _resolve_mentions(m.get("text") or "", names),
        "ts": m.get("ts"),
        # Context-message images/videos are never auto-loaded -- the webapp
        # shows a "[IMAGE]"/"[VIDEO]" placeholder and only fetches the bytes
        # (via /api/slack-file/<id>, see below) if the user actually clicks
        # it.
        "file_id": file_info.get("id"),
        "file_name": file_info.get("name"),
        "file_is_video": bool(file_info.get("is_video")),
        "extra_files": [
            {"id": f.get("id"), "name": f.get("name"), "is_video": bool(f.get("is_video"))}
            for f in (m.get("extra_files") or [])
        ],
    }


def _guessed_sniped(entry):
    """The sniped-user ids to pre-fill the webapp's chip box with. For
    snipe/likely_snipe entries this is just whatever the detector already
    stored. "unidentified" entries never got a sniped list at classify time
    (that's exactly why they're unidentified) -- but the caption may still
    contain @mentions that just weren't paired with a snipe keyword, so pull
    those straight from the caption, same as likely_snipe effectively does."""
    sniped = entry.get("sniped") or []
    if sniped:
        return sniped
    if entry.get("kind") == "unidentified":
        return detection._extract_mentions(entry.get("photo_text") or "", exclude_user=entry.get("sniper"))
    return []


def _entry_for_review(entry, names):
    """Shapes any entry (pending or already-decided) for the webapp cards --
    used by the Review Queue, Manual Decisions, Search, and the Special tab."""
    photo_url = f"/photos/{os.path.basename(entry['photo_path'])}" if entry.get("photo_path") else None
    is_video = (entry.get("file") or {}).get("mimetype", "").startswith("video/")
    guessed = _guessed_sniped(entry)
    return {
        "id": entry["id"],
        "kind": entry["kind"],
        "manually_resolved": bool(entry.get("manually_resolved")),
        "notable": bool(entry.get("notable")),
        "sniper": user_names.format_side_by_side(entry.get("sniper"), names),
        "guessed_sniped_ids": guessed,
        "guessed_sniped_display": [user_names.format_side_by_side(u, names) for u in guessed],
        "photo_text": _resolve_mentions(entry.get("photo_text") or "", names),
        "photo_url": photo_url,
        "is_video": is_video,
        # Any additional photos/videos the same message carried beyond the
        # primary one -- lazy-loaded via /api/slack-file/<id>, same as
        # context-message attachments (see _fmt_msg below).
        "extra_files": [
            {"id": f.get("id"), "name": f.get("name"), "is_video": bool(f.get("is_video"))}
            for f in (entry.get("extra_files") or [])
        ],
        "context_before": [_fmt_msg(m, names) for m in entry.get("context_before", [])],
        "context_after": [_fmt_msg(m, names) for m in entry.get("context_after", [])],
        "context_before_checked": bool(entry.get("context_before_checked")),
        "context_after_checked": bool(entry.get("context_after_checked")),
        "date_time": entry.get("date_time"),
    }


@bp.route("/api/users")
def api_users():
    """Every user we have a cached name for, for the review queue's "who got
    sniped" autocomplete. Sorted by name so the dropdown reads naturally."""
    names = user_names.get_cached_names()
    users = [
        {"id": uid, "name": name or "", "display": user_names.format_side_by_side(uid, names)}
        for uid, name in names.items()
    ]
    users.sort(key=lambda u: u["name"].lower())
    return jsonify({"users": users})


@bp.route("/api/review-queue/<entry_id>/replies")
def api_review_replies(entry_id):
    """Fetched on demand -- one entry at a time, either when a single-mode
    card is shown/prefetched or when a batch-mode card is rendered. Doing
    this inline for every entry in /api/review-queue meant one sequential
    Slack call per queue entry before the page could render at all; with a
    queue in the hundreds that's the whole slowdown."""
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    names = user_names.get_cached_names()
    replies = _thread_replies(_slack_client, entry["channel"], entry["message_ts"], names)
    return jsonify({"replies": replies})


@bp.route("/api/review-queue")
def api_review_queue():
    names = user_names.get_cached_names()
    entries = sorted(json_store.load_unidentified(), key=lambda s: s.get("message_ts", ""))
    return jsonify({"entries": [_entry_for_review(e, names) for e in entries]})


@bp.route("/api/review-queue/<entry_id>/resolve", methods=["POST"])
def api_resolve(entry_id):
    body = request.json or {}
    kind = body.get("kind")
    if kind not in ("snipe", "snope", "not_a_snipe"):
        return jsonify({"error": "invalid kind"}), 400
    sniped = body.get("sniped") if kind == "snipe" else []
    if kind == "snipe" and not sniped:
        return jsonify({"error": "at least one sniped user is required to confirm a snipe"}), 400
    entry = detection.resolve_entry(entry_id, kind, _slack_client, sniped=sniped)
    if not entry:
        return jsonify({"error": "already resolved or not found"}), 409
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry actions -- everything a Slack reaction can do (snipe/snope/likely
# snipe/NAS/unidentified/incorrect-snipe), but as webapp buttons, and works
# on ANY entry regardless of its current state -- not just pending ones. Used
# by the Review Queue, Manual Decisions tab, and the Live Log's edit modal.
# ---------------------------------------------------------------------------

_SET_KIND_VALUES = ("snipe", "snope", "likely_snipe", "not_a_snipe", "unidentified", "incorrect_snipe", "special")


@bp.route("/api/entries/<entry_id>")
def api_get_entry(entry_id):
    entry = json_store.find_snipe_by_id(entry_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    names = user_names.get_cached_names()
    return jsonify(_entry_for_review(entry, names))


@bp.route("/api/entries/<entry_id>/set-kind", methods=["POST"])
def api_set_kind(entry_id):
    body = request.json or {}
    kind = body.get("kind")
    if kind not in _SET_KIND_VALUES:
        return jsonify({"error": "invalid kind"}), 400

    if kind == "incorrect_snipe":
        # No restriction here -- Zeke can mark anything as a missnipe from
        # the webapp regardless of its current kind (unlike the red Slack
        # reaction, which is deliberately narrower since a reaction can't
        # ask "are you sure").
        entry = detection.mark_incorrect_snipe(entry_id, _slack_client)
    else:
        sniped = body.get("sniped") if kind in ("snipe", "likely_snipe") else None
        # A "snipe" with nobody actually sniped is malformed data (this is
        # how a chunk of the log ended up that way -- confirming without
        # filling in the sniped-user box). The webapp already blocks this
        # client-side; this is the server-side backstop.
        if kind == "snipe" and not sniped:
            return jsonify({"error": "at least one sniped user is required to confirm a snipe"}), 400
        entry = detection.mark_manual_kind(entry_id, kind, _slack_client, sniped=sniped)

    if not entry:
        return jsonify({"error": "not found"}), 404

    names = user_names.get_cached_names()
    return jsonify(_entry_for_review(entry, names))


@bp.route("/api/entries/<entry_id>/set-notable", methods=["POST"])
def api_set_notable(entry_id):
    """Notable is independent of classification -- Zeke can flag any photo
    as notable regardless of whether it's a snipe, a snope, or still
    pending, same as the star reaction already lets him do in Slack."""
    body = request.json or {}
    notable = bool(body.get("notable"))
    entry = detection.set_notable(entry_id, notable, _slack_client)
    if not entry:
        return jsonify({"error": "not found"}), 404
    names = user_names.get_cached_names()
    return jsonify(_entry_for_review(entry, names))


@bp.route("/api/entries/<entry_id>/context")
def api_entry_context(entry_id):
    """Lazily backfills context_before/context_after the first time this
    entry's card is opened (see detection.ensure_initial_context), then
    returns the current context either way."""
    entry = detection.ensure_initial_context(entry_id, _slack_client)
    if not entry:
        return jsonify({"error": "not found"}), 404
    names = user_names.get_cached_names()
    return jsonify({
        "context_before": [_fmt_msg(m, names) for m in entry.get("context_before", [])],
        "context_after": [_fmt_msg(m, names) for m in entry.get("context_after", [])],
    })


@bp.route("/api/entries/<entry_id>/context/more", methods=["POST"])
def api_entry_context_more(entry_id):
    body = request.json or {}
    direction = body.get("direction")
    if direction not in ("before", "after"):
        return jsonify({"error": "direction must be 'before' or 'after'"}), 400
    count = int(body.get("count") or 2)
    names = user_names.get_cached_names()
    more = detection.load_more_context(entry_id, direction, _slack_client, count=count)
    return jsonify({"messages": [_fmt_msg(m, names) for m in more]})


@bp.route("/api/slack-file/<file_id>")
def api_slack_file(file_id):
    """Serves a context-message's (or entry's extra) image/video so it can
    be lazy-loaded from the webapp. Every file bot/archiver.py has ever
    seen is permanently archived locally by now (see
    storage/archive_store.py) -- checked here FIRST, since that's immune to
    Slack's 90-day retention limit entirely. Only falls back to a live
    files.info + download if somehow not archived yet (e.g. an entry from
    before archiving existed), which is subject to that limit same as
    before."""
    archived_info = archive_store.file_info(file_id)
    if archived_info and os.path.exists(archived_info["path"]):
        return send_from_directory(
            os.path.dirname(os.path.abspath(archived_info["path"])),
            os.path.basename(archived_info["path"]),
            mimetype=archived_info.get("mimetype") or None,
        )

    try:
        info = _slack_client.files_info(file=file_id).get("file", {})
    except Exception as e:
        return jsonify({"error": f"slack lookup failed: {e}"}), 502

    url = info.get("url_private_download") or info.get("url_private")
    if not url:
        return jsonify({"error": "no downloadable url for this file"}), 404

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 502

    # Archive it now so this is the last time this file ever needs a live
    # Slack fetch.
    archive_store.save_file(file_id, info.get("name", ""), info.get("mimetype", ""), resp.content)

    return Response(resp.content, mimetype=info.get("mimetype", "image/jpeg"))


# ---------------------------------------------------------------------------
# Manual Decisions tab -- browse/revise every entry Zeke has ever manually
# resolved (as opposed to Review Queue, which only shows still-pending ones)
# ---------------------------------------------------------------------------

@bp.route("/api/manual-decisions")
def api_manual_decisions():
    names = user_names.get_cached_names()
    entries = [e for e in json_store.load_snipes() if e.get("manually_resolved")]
    entries.sort(key=lambda e: e.get("message_ts", ""), reverse=True)
    return jsonify({"entries": [_entry_for_review(e, names) for e in entries]})


# ---------------------------------------------------------------------------
# Special tab -- for cases too niche for the detector to ever handle on its
# own (two unrelated snipes bundled into one photo, a snope of something
# that isn't even a person, etc). A photo gets flagged "special" by
# reacting :fingerprint: to it in Slack (or the webapp's "Flag special"
# button, same as any other set-kind action) -- it then shows up here,
# where Zeke builds whatever actual snipe/snope/etc record(s) the situation
# calls for, each with its own sniper and sniped user(s). Those can be real
# Slack users OR any free-text string (e.g. "deer") -- see
# bot/detection.py's create_special_snipe() for exactly how that's handled.
# ---------------------------------------------------------------------------

_SPECIAL_KIND_VALUES = ("snipe", "snope", "not_a_snipe", "incorrect_snipe")


def _entry_for_special(entry, names):
    base = _entry_for_review(entry, names)
    base["derived"] = [_entry_for_review(child, names) for child in json_store.find_derived_snipes(entry["id"])]
    return base


@bp.route("/api/special")
def api_special():
    names = user_names.get_cached_names()
    entries = sorted(json_store.load_special(), key=lambda e: e.get("message_ts", ""))
    return jsonify({"entries": [_entry_for_special(e, names) for e in entries]})


@bp.route("/api/special/<entry_id>/create", methods=["POST"])
def api_special_create(entry_id):
    body = request.json or {}
    kind = body.get("kind")
    if kind not in _SPECIAL_KIND_VALUES:
        return jsonify({"error": "invalid kind"}), 400

    sniper = (body.get("sniper") or "").strip()
    sniped = [s.strip() for s in (body.get("sniped") or []) if isinstance(s, str) and s.strip()]
    if not sniper:
        return jsonify({"error": "sniper is required"}), 400
    if kind in ("snipe", "snope") and not sniped:
        return jsonify({"error": "at least one sniped name is required for a snipe or snope"}), 400

    entry = detection.create_special_snipe(entry_id, kind, sniper, sniped, _slack_client)
    if not entry:
        return jsonify({"error": "could not create -- check the parent entry still exists"}), 409

    names = user_names.get_cached_names()
    return jsonify(_entry_for_review(entry, names))


@bp.route("/api/special/<entry_id>/dismiss", methods=["POST"])
def api_special_dismiss(entry_id):
    entry = detection.dismiss_special(entry_id, _slack_client)
    if not entry:
        return jsonify({"error": "not found"}), 404
    names = user_names.get_cached_names()
    return jsonify(_entry_for_review(entry, names))


# ---------------------------------------------------------------------------
# Search tab -- filters across every entry regardless of kind or pending/
# manual status, i.e. everything in snipes.json in one place.
# ---------------------------------------------------------------------------

_TIME_BUCKETS = (
    ("morning", 5, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 21),
    # anything else (21:00-04:59) is "night"
)


def _time_bucket(date_time_iso):
    if not date_time_iso:
        return None
    try:
        hour = datetime.fromisoformat(date_time_iso).hour
    except (ValueError, TypeError):
        return None
    for name, start, end in _TIME_BUCKETS:
        if start <= hour < end:
            return name
    return "night"


def _bool_param(value):
    """None means "don't filter on this"; anything else is truthy/falsy."""
    if value is None or value == "":
        return None
    return value in ("1", "true", "True", "yes")


@bp.route("/api/search")
def api_search():
    kinds = {k for k in (request.args.get("kind") or "").split(",") if k}
    sniper = request.args.get("sniper") or None
    sniped = request.args.get("sniped") or None
    day = request.args.get("day") or None
    time_bucket = request.args.get("time") or None
    q = (request.args.get("q") or "").strip().lower()
    notable = _bool_param(request.args.get("notable"))
    manually_resolved = _bool_param(request.args.get("manually_resolved"))

    def matches(e):
        if kinds and e.get("kind") not in kinds:
            return False
        if notable is not None and bool(e.get("notable")) != notable:
            return False
        if manually_resolved is not None and bool(e.get("manually_resolved")) != manually_resolved:
            return False
        if sniper and e.get("sniper") != sniper:
            return False
        if sniped and sniped not in (e.get("sniped") or []):
            return False
        if day and e.get("day_of_week") != day:
            return False
        if time_bucket and _time_bucket(e.get("date_time")) != time_bucket:
            return False
        if q and q not in (e.get("photo_text") or "").lower():
            return False
        return True

    filtered = [e for e in json_store.load_snipes() if matches(e)]
    filtered.sort(key=lambda e: e.get("message_ts", ""), reverse=True)

    names = user_names.get_cached_names()
    return jsonify({
        "entries": [_entry_for_review(e, names) for e in filtered],
        "total": len(filtered),
    })


# ---------------------------------------------------------------------------
# Do Not Snipe tab
# ---------------------------------------------------------------------------

@bp.route("/api/dns")
def api_dns_list():
    names = user_names.get_cached_names()
    users = [
        {"id": uid, "display": user_names.format_side_by_side(uid, names)}
        for uid in json_store.list_dns()
    ]
    return jsonify({"users": users})


@bp.route("/api/dns", methods=["POST"])
def api_dns_add():
    body = request.json or {}
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    json_store.add_dns(user_id)
    user_names.ensure_cached(user_id, _slack_client)
    return jsonify({"ok": True})


@bp.route("/api/dns/<user_id>", methods=["DELETE"])
def api_dns_remove(user_id):
    json_store.remove_dns(user_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Matrix tab -- three views of the sniper x victim count matrix behind the
# leaderboard's Snipes/Sniped columns (mirrors the spreadsheet Zeke used to
# keep this in by hand):
#
#   Coded  -- read-only, exactly what the bot classified from snipes.json.
#   Manual -- Zeke's own corrections/additions on top of that, editable
#             directly, cell by cell (storage/json_store.py's
#             manual_matrix.json). Never touched by recount/backfill.
#   True   -- Coded + Manual, cell-wise. Also editable: editing a True cell
#             just computes the delta from the coded value and writes THAT
#             through to the manual store, so Coded always stays pure and
#             Manual always stays in sync with whatever True shows.
#
# All three share one roster (everyone with a cached display name, plus
# anyone who appears anywhere in the coded or manual matrices or the
# adjustments file), in one fixed order, so the three grids -- and the
# Adjustments tab -- always line up row-for-row.
# ---------------------------------------------------------------------------

def _matrix_roster():
    """(ordered_user_ids, names, coded_matrix, manual_matrix, true_matrix).
    Order is by True row total (biggest snipers first), then by name, so
    the ordering is stable and consistent across all three matrix views."""
    names = user_names.get_cached_names()
    coded = analysis.snipe_matrix()
    manual = json_store.get_manual_matrix()
    true = analysis.true_matrix()

    manual_users = set(manual) | {v for row in manual.values() for v in row}
    users = set(names) | set(coded["users"]) | manual_users | set(json_store.get_adjustments())
    order = sorted(
        users,
        key=lambda u: (-true["row_totals"].get(u, 0), (names.get(u) or u).lower()),
    )
    return order, names, coded, manual, true


def _matrix_response(order, names, row_totals, col_totals, cells, editable):
    return jsonify({
        "users": [{"id": u, "display": user_names.format_side_by_side(u, names)} for u in order],
        "row_totals": {u: row_totals.get(u, 0) for u in order},
        "col_totals": {u: col_totals.get(u, 0) for u in order},
        "cells": cells,
        "editable": editable,
    })


@bp.route("/api/matrix/coded")
def api_matrix_coded():
    order, names, coded, manual, true = _matrix_roster()
    return _matrix_response(order, names, coded["row_totals"], coded["col_totals"], coded["cells"], editable=False)


@bp.route("/api/matrix/manual")
def api_matrix_manual():
    order, names, coded, manual, true = _matrix_roster()
    row_totals = {s: sum(row.values()) for s, row in manual.items()}
    col_totals = defaultdict(int)
    for row in manual.values():
        for victim, count in row.items():
            col_totals[victim] += count
    return _matrix_response(order, names, row_totals, dict(col_totals), manual, editable=True)


@bp.route("/api/matrix/manual/cell", methods=["POST"])
def api_matrix_manual_set():
    body = request.json or {}
    sniper, victim = (body.get("sniper") or "").strip(), (body.get("victim") or "").strip()
    if not sniper or not victim or sniper == victim:
        return jsonify({"error": "sniper and victim (and they must differ) are required"}), 400
    try:
        value = int(body.get("value", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "value must be a whole number"}), 400
    json_store.set_manual_cell(sniper, victim, value)
    return jsonify({"ok": True})


@bp.route("/api/matrix/true")
def api_matrix_true():
    order, names, coded, manual, true = _matrix_roster()
    return _matrix_response(order, names, true["row_totals"], true["col_totals"], true["cells"], editable=True)


@bp.route("/api/matrix/true/cell", methods=["POST"])
def api_matrix_true_set():
    """Editing a True cell writes through to the manual matrix: the delta
    between what was typed and what the coded matrix already has there is
    what actually gets stored, so Coded is never touched and Manual stays
    the single source of truth for "what Zeke changed."""
    body = request.json or {}
    sniper, victim = (body.get("sniper") or "").strip(), (body.get("victim") or "").strip()
    if not sniper or not victim or sniper == victim:
        return jsonify({"error": "sniper and victim (and they must differ) are required"}), 400
    try:
        value = int(body.get("value", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "value must be a whole number"}), 400
    coded_count = analysis.snipe_matrix()["cells"].get(sniper, {}).get(victim, 0)
    json_store.set_manual_cell(sniper, victim, value - coded_count)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Adjustments tab -- manual, per-person score adjustments (storage/
# json_store.py's adjustments.json). These are added directly to a user's
# score in analysis/stats.py's leaderboard() -- entirely independent of the
# matrices above.
# ---------------------------------------------------------------------------

@bp.route("/api/adjustments")
def api_adjustments_list():
    order, names, coded, manual, true = _matrix_roster()
    adjustments = json_store.get_adjustments()
    return jsonify({
        "users": [
            {"id": u, "display": user_names.format_side_by_side(u, names), "adjustment": adjustments.get(u, 0)}
            for u in order
        ]
    })


@bp.route("/api/adjustments", methods=["POST"])
def api_adjustments_set():
    body = request.json or {}
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        value = float(body.get("adjustment", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "adjustment must be a number"}), 400
    json_store.set_adjustment(user_id, value)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Analysis tab -- formatted view of analysis/stats.py's functions
# ---------------------------------------------------------------------------

@bp.route("/api/analysis")
def api_analysis():
    leaderboard = [dict(r, user=analysis._named(r["user"])) for r in analysis.leaderboard()]
    by_day, by_bucket = analysis.peak_windows()
    as_sniper, as_victim = analysis.group_snipe_frequency()
    return jsonify({
        "leaderboard": leaderboard,
        "rivalries": [{"pair": analysis._named(list(pair)), "count": count}
                       for pair, count in analysis.rivalries()],
        "streaks": analysis._named(analysis.sniping_streaks()),
        "untouchable": analysis._named(analysis.untouchable_award()),
        "retaliation_speed": analysis._named(analysis.retaliation_speed()),
        "peak_by_day": by_day,
        "peak_by_bucket": by_bucket,
        "notable_ratio": analysis._named(analysis.most_notable_ratio()),
        "group_snipe_as_sniper": analysis._named(as_sniper),
        "group_snipe_as_victim": analysis._named(as_victim),
        "queue_size": analysis.unidentified_queue_size(),
    })


# ---------------------------------------------------------------------------
# Live log tab
# ---------------------------------------------------------------------------

def _enrich_row(row: list, names: dict) -> list:
    row = list(row)
    if row[2]:
        row[2] = user_names.format_side_by_side(row[2], names)
    if row[3]:
        row[3] = ", ".join(
            user_names.format_side_by_side(uid.strip(), names)
            for uid in row[3].split(",") if uid.strip()
        )
    return row


@bp.route("/api/snipes")
def api_snipes():
    rows = xlsx_store.read_true_log_rows()
    rows.sort(key=lambda r: (r[0] or "", r[1] or ""), reverse=True)
    names = user_names.get_cached_names()
    rows = [_enrich_row(r, names) for r in rows]
    return jsonify({"rows": rows})