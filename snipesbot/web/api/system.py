"""Live log, status, export and the Maintenance tab."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request

from ...services import exports, maintenance, sync as sync_service
from ...store import Busy
from ..deps import env
from ..serializers import entry_view

bp = Blueprint("system", __name__)


@bp.get("/status")
def status():
    e = env()
    last = e.store.settings.get(sync_service.WATERMARK_KEY)
    return jsonify({
        "entries": e.store.entries.count(),
        "last_sync": datetime.fromtimestamp(last, e.cfg.tz).isoformat(timespec="seconds") if last else None,
    })


@bp.get("/log")
def log():
    """Newest entries first, for the Live Log tab."""
    e = env()
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 1000))))
    except ValueError:
        limit = 1000
    names = e.store.names.all()
    rows = e.store.entries.find(limit=limit)
    return jsonify({"rows": [entry_view(e, x, names) for x in rows], "total": e.store.entries.count()})


@bp.get("/export.xlsx")
def export_xlsx():
    return Response(
        exports.build_xlsx(env()),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="snipes_log.xlsx"'},
    )


@bp.get("/maintenance/tasks")
def tasks():
    return jsonify({k: {"label": t.label, "description": t.description, "dangerous": t.dangerous,
                        "params": [{"name": p.name, "label": p.label, "kind": p.kind, "help": p.help} for p in t.params]}
                    for k, t in maintenance.TASKS.items()})


@bp.post("/maintenance/<key>/run")
def run_task(key):
    runner = current_app.config["RUNNER"]
    try:
        run_id = runner.start(key, request.get_json(silent=True) or {})
    except KeyError:
        return jsonify({"error": "unknown task"}), 404
    except Busy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"run_id": run_id})


@bp.get("/maintenance/runs/<run_id>")
def run_status(run_id):
    run = current_app.config["RUNNER"].get(run_id)
    return jsonify(run) if run else (jsonify({"error": "unknown run"}), 404)
