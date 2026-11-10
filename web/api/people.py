"""Users and the do-not-snipe list."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...services import people
from ..deps import env

bp = Blueprint("people", __name__)


@bp.get("/users")
def users():
    """Every user with a cached name, for the 'who was sniped' autocomplete."""
    names = env().store.names.all()
    rows = [{"id": uid, "name": name or "", "display": people.display(uid, names)} for uid, name in names.items()]
    rows.sort(key=lambda u: u["name"].lower())
    return jsonify({"users": rows})


@bp.get("/dns")
def dns_list():
    names = env().store.names.all()
    return jsonify({"users": [{"id": u, "display": people.display(u, names)} for u in env().store.dns.list()]})


@bp.post("/dns")
def dns_add():
    user_id = ((request.get_json(silent=True) or {}).get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    env().store.dns.add(user_id)
    people.ensure_name(env(), user_id)
    return jsonify({"ok": True})


@bp.delete("/dns/<user_id>")
def dns_remove(user_id):
    env().store.dns.remove(user_id)
    return jsonify({"ok": True})
