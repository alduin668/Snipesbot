"""Entry listing, detail, edits, context, files, and the Special tab."""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request, send_file

from ...analysis.stats import day_name, time_bucket
from ...models import PENDING_KINDS, Kind
from ...services import archiver, context as context_service, entries as svc
from ..deps import env
from ..serializers import entry_view

bp = Blueprint("entries", __name__)


def _csv(name: str) -> set:
    return {v for v in (request.args.get(name) or "").split(",") if v}


def _flag(name: str):
    v = request.args.get(name)
    return None if v in (None, "") else v in ("1", "true", "yes")


def _view(e):
    return entry_view(env(), e, env().store.names.all())


@bp.get("/entries")
def list_entries():
    """One listing endpoint for the Review Queue, Manual Decisions and Search tabs.

    Filters: pending, kind (csv), manual, notable, sniper, sniped, day, time, q.
    Paging: limit (omit for all) and offset. Review is oldest-first by default.
    """
    e = env()
    pending = _flag("pending")
    kinds = _csv("kind")
    try:
        kind_filter = {Kind(k) for k in kinds} if kinds else None
    except ValueError:
        return jsonify({"error": "unknown kind"}), 400
    if pending:
        kind_filter = (kind_filter or set(PENDING_KINDS)) & PENDING_KINDS

    order = request.args.get("order") or ("asc" if pending else "desc")
    found = e.store.entries.find(
        kinds=kind_filter, manual=_flag("manual"), sniper=request.args.get("sniper") or None,
        text=(request.args.get("q") or "").strip() or None, newest_first=(order != "asc"),
    )
    sniped, day, bucket, notable = request.args.get("sniped"), request.args.get("day"), request.args.get("time"), _flag("notable")
    tz = e.cfg.tz
    found = [x for x in found
             if (not sniped or sniped in x.sniped)
             and (not day or day_name(x, tz) == day)
             and (not bucket or time_bucket(x.local_time(tz).hour) == bucket)
             and (notable is None or x.notable == notable)]

    total = len(found)
    try:
        limit = int(request.args["limit"]) if request.args.get("limit") else None
        offset = max(0, int(request.args.get("offset") or 0))
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400
    page = found[offset: offset + limit] if limit else found[offset:]
    names = e.store.names.all()
    return jsonify({"entries": [entry_view(e, x, names) for x in page], "total": total})


def _get_or_404(entry_id):
    entry = env().store.entries.get(entry_id)
    return entry if entry and not entry.deleted else None


@bp.get("/entries/<entry_id>")
def get_entry(entry_id):
    entry = _get_or_404(entry_id)
    return jsonify(_view(entry)) if entry else (jsonify({"error": "not found"}), 404)


@bp.post("/entries/<entry_id>/kind")
def set_kind(entry_id):
    body = request.get_json(silent=True) or {}
    try:
        kind = Kind(body.get("kind"))
    except ValueError:
        return jsonify({"error": "invalid kind"}), 400
    if _get_or_404(entry_id) is None:
        return jsonify({"error": "not found"}), 404
    sniped = body.get("sniped") if kind in (Kind.SNIPE, Kind.LIKELY_SNIPE) else None
    try:
        entry = svc.set_kind(env(), entry_id, kind, sniped=sniped)
    except svc.InvalidTransition as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_view(entry))


@bp.post("/entries/<entry_id>/notable")
def set_notable(entry_id):
    body = request.get_json(silent=True) or {}
    entry = svc.set_notable(env(), entry_id, bool(body.get("notable")))
    return jsonify(_view(entry)) if entry else (jsonify({"error": "not found"}), 404)


def _count(name: str, default: int) -> int:
    try:
        return max(0, min(50, int(request.args.get(name, default))))
    except ValueError:
        return default


@bp.get("/entries/<entry_id>/context")
def entry_context(entry_id):
    entry = _get_or_404(entry_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    n = env().cfg.context_window
    return jsonify(context_service.around(env(), entry, _count("before", n), _count("after", n)))


@bp.get("/entries/<entry_id>/replies")
def entry_replies(entry_id):
    entry = _get_or_404(entry_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    return jsonify({"replies": context_service.replies(env(), entry)})


@bp.get("/files/<file_id>")
def serve_file(file_id):
    if not re.fullmatch(r"[A-Za-z0-9]+", file_id) or not env().store.archive.knows_file(file_id):
        return jsonify({"error": "unknown file"}), 404
    path = archiver.fetch_file(env(), file_id)
    if not path:
        return jsonify({"error": "file is not available"}), 404
    info = env().store.archive.file_info(file_id) or {}
    return send_file(path, mimetype=info.get("mimetype") or None, max_age=86400)


# ---- Special tab -------------------------------------------------------------

@bp.get("/special")
def special_list():
    e = env()
    names = e.store.names.all()
    out = []
    for parent in e.store.entries.special_open():
        view = entry_view(e, parent, names)
        view["derived_entries"] = [entry_view(e, c, names) for c in e.store.entries.derived(parent.id)]
        out.append(view)
    return jsonify({"entries": out})


@bp.post("/special/<entry_id>/create")
def special_create(entry_id):
    body = request.get_json(silent=True) or {}
    sniped = [s for s in (body.get("sniped") or []) if isinstance(s, str)]
    try:
        kind = Kind(body.get("kind"))
    except ValueError:
        return jsonify({"error": "invalid kind"}), 400
    if not (body.get("sniper") or "").strip():
        return jsonify({"error": "sniper is required"}), 400
    if kind in (Kind.SNIPE, Kind.SNOPE) and not [s for s in sniped if s.strip()]:
        return jsonify({"error": "at least one sniped name is required for a snipe or snope"}), 400
    child = svc.create_special(env(), entry_id, kind, body["sniper"], sniped)
    if child is None:
        return jsonify({"error": "could not create -- check the parent entry still exists"}), 409
    return jsonify(_view(child))


@bp.post("/special/<entry_id>/dismiss")
def special_dismiss(entry_id):
    if _get_or_404(entry_id) is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(_view(svc.dismiss_special(env(), entry_id)))
