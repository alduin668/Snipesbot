"""Human-matrix import with automatic name matching (Compare tab -> "Paste / notes")."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...analysis.stats import Stats
from ...services import namematch, people
from ..deps import env
from .compare import _human_users

bp = Blueprint("compare_import", __name__)


def _setup():
    s = env().store
    order = _human_users(Stats(env()))
    names = s.names.all()
    roster = {u: names.get(u) for u in order if names.get(u)}
    matcher = namematch.Matcher(roster, dict(names), s.settings.get(namematch.ALIASES_KEY) or {})
    return s, names, order, matcher


def _parse(body):
    try:
        return namematch.parse(body.get("text") or ""), None
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)


@bp.post("/compare/human/import/preview")
def preview():
    """Parse the paste and say who each name resolves to. Saves nothing."""
    parsed, err = _parse(request.get_json(silent=True) or {})
    if err:
        return err
    s, names, order, matcher = _setup()
    on_roster = set(order)
    used: dict = {}
    for (sk, vk), n in parsed["cells"].items():
        used.setdefault(sk, {"snipes": 0, "sniped": 0})["snipes"] += n
        used.setdefault(vk, {"snipes": 0, "sniped": 0})["sniped"] += n

    out = []
    for k in parsed["keys"]:
        if k not in used:
            continue
        m = matcher.match(k)
        out.append({
            "key": k, "raw": parsed["labels"].get(k, k), "status": m["status"], "user_id": m["user_id"],
            "user_name": people.display(m["user_id"], names) if m["user_id"] else "",
            "candidates": [{"id": c, "name": names.get(c) or c, "on_roster": c in on_roster} for c in m["candidates"]],
            **used[k],
        })
    rank = {"unmatched": 0, "ambiguous": 1}
    out.sort(key=lambda n: (rank.get(n["status"], 2), n["raw"].lower()))
    return jsonify({
        "names": out,
        "roster": [{"id": u, "name": people.display(u, names) or u} for u in order],
        "needs": sum(1 for n in out if n["status"] in rank),
        "empty_ignored": sum(1 for k in parsed["keys"] if k not in used),
        "cells": len(parsed["cells"]), "total": sum(parsed["cells"].values()),
        "warnings": parsed["warnings"],
    })


@bp.post("/compare/human/import/apply")
def apply():
    """Replace the human matrix. body: {text, mapping: {name key: user id, or "" to ignore}}.
    Choices that differ from the automatic match are remembered for next time."""
    body = request.get_json(silent=True) or {}
    parsed, err = _parse(body)
    if err:
        return err
    mapping = {k: v for k, v in (body.get("mapping") or {}).items() if isinstance(v, str)}
    s, names, order, matcher = _setup()
    used = {k for pair in parsed["cells"] for k in pair}

    resolved, missing = {}, []
    for k in parsed["keys"]:
        if k not in used:
            continue
        if k in mapping:
            resolved[k] = mapping[k]
            continue
        m = matcher.match(k)
        if m["status"] in ("auto", "alias", "skip"):
            resolved[k] = m["user_id"]
        else:
            missing.append(parsed["labels"].get(k, k))
    if missing:
        return jsonify({"error": "Still need a choice for: " + ", ".join(missing)}), 400

    cells, dropped = {}, 0
    for (sk, vk), n in parsed["cells"].items():
        a, b = resolved.get(sk), resolved.get(vk)
        if not a or not b or a == b:
            dropped += n
            continue
        row = cells.setdefault(a, {})
        row[b] = row.get(b, 0) + n
    s.human.replace_matrix(cells)

    aliases = dict(s.settings.get(namematch.ALIASES_KEY) or {})
    saved = 0
    for k, uid in mapping.items():
        if k not in used:
            continue
        m = matcher.match(k)
        if m["status"] in ("skip",) or (m["status"] in ("auto", "alias") and m["user_id"] == uid):
            continue
        aliases[k] = uid
        saved += 1
    s.settings.set(namematch.ALIASES_KEY, aliases)
    return jsonify({"ok": True, "cells": sum(len(r) for r in cells.values()), "ignored_snipes": dropped, "aliases_saved": saved})
