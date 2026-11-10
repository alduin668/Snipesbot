"""Compare tab: the human's matrix vs the bot, and applying the fixes."""
from __future__ import annotations

import csv
import io

from flask import Blueprint, jsonify, request

from ...analysis.compare import compare
from ...analysis.stats import Stats
from ...services import people, roster
from ..deps import env

bp = Blueprint("compare", __name__)


def _human_users(stats: Stats) -> list:
    s = env().store
    h, adj = s.human.matrix(), s.human.adjustments()
    extra = set(h) | {v for r in h.values() for v in r} | set(adj)
    users = roster.roster_ids(env(), stats.coded_matrix()["users"], stats.true_matrix()["users"], extra)
    people.ensure_names(env(), users, limit=40)
    names = env().store.names.all()
    return sorted(users, key=lambda u: (people.display(u, names) or u).lower())


@bp.get("/compare")
def get_compare():
    stats = Stats(env())
    s = env().store
    order = _human_users(stats)
    names = s.names.all()
    h, adj = s.human.matrix(), s.human.adjustments()
    out = compare(stats, h, adj, names)
    out["users"] = [{"id": u, "display": people.display(u, names)} for u in order]
    out["human_cells"], out["human_adjustments"] = h, adj
    out["meta"] = s.settings.get(s.human.META_KEY) or {"as_of": "", "note": ""}
    return jsonify(out)


def _int(body, key="value"):
    try:
        return int(body.get(key, 0) or 0), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "value must be a whole number"}), 400)


@bp.post("/compare/human/cell")
def set_human_cell():
    body = request.get_json(silent=True) or {}
    sniper, victim = (body.get("sniper") or "").strip(), (body.get("victim") or "").strip()
    value, err = _int(body)
    if err:
        return err
    if not sniper or not victim or sniper == victim:
        return jsonify({"error": "sniper and victim (and they must differ) are required"}), 400
    env().store.human.set_cell(sniper, victim, value)
    return jsonify({"ok": True})


@bp.post("/compare/human/adjustment")
def set_human_adjustment():
    body = request.get_json(silent=True) or {}
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        value = float(body.get("adjustment", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "adjustment must be a number"}), 400
    env().store.human.set_adjustment(user_id, value)
    return jsonify({"ok": True})


@bp.post("/compare/human/meta")
def set_meta():
    body = request.get_json(silent=True) or {}
    s = env().store
    s.settings.set(s.human.META_KEY, {"as_of": str(body.get("as_of") or "")[:40], "note": str(body.get("note") or "")[:2000]})
    return jsonify({"ok": True})


@bp.post("/compare/human/import")
def import_matrix():
    """Paste a matrix copied from a spreadsheet: first row = victim names, first
    column = sniper names (tab, comma or semicolon separated). Names are matched
    to the roster; anything unmatched is reported and nothing is saved."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip("\n")
    if not text.strip():
        return jsonify({"error": "nothing to import"}), 400
    first = text.splitlines()[0]
    delim = "\t" if "\t" in first else (";" if first.count(";") > first.count(",") else ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    stats = Stats(env())
    names = env().store.names.all()
    by_name: dict = {}
    for u in _human_users(stats):
        by_name.setdefault((people.display(u, names) or "").strip().lower(), u)
        by_name.setdefault((names.get(u) or "").strip().lower(), u)
    lookup = lambda n: by_name.get(n.strip().lstrip("@").lower())      # noqa: E731
    header = rows[0][1:]
    cols = [lookup(h) for h in header]
    unmatched = sorted({h.strip() for h, c in zip(header, cols) if h.strip() and not c})
    cells: dict = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        sniper = lookup(r[0])
        if not sniper:
            unmatched.append(r[0].strip())
            continue
        for victim, raw in zip(cols, r[1:]):
            raw = raw.strip()
            if not raw or not victim or victim == sniper:
                continue
            try:
                n = int(float(raw))
            except ValueError:
                continue
            if n:
                cells.setdefault(sniper, {})[victim] = n
    if unmatched:
        return jsonify({"error": "Couldn't match these names to anyone on the roster: " + ", ".join(sorted(set(unmatched)))}), 400
    env().store.human.replace_matrix(cells)
    return jsonify({"ok": True, "cells": sum(len(r) for r in cells.values())})


# ---- remedies: write the fix into the Manual matrix / Adjustments --------------------------------------

def _apply_cell(stats: Stats, sniper: str, victim: str) -> int:
    h = env().store.human.matrix().get(sniper, {}).get(victim, 0)
    coded = stats.coded_matrix()["cells"].get(sniper, {}).get(victim, 0)
    env().store.scoring.set_cell(sniper, victim, h - coded)
    return h - coded


@bp.post("/compare/remedy/cell")
def remedy_cell():
    body = request.get_json(silent=True) or {}
    sniper, victim = (body.get("sniper") or "").strip(), (body.get("victim") or "").strip()
    if not sniper or not victim or sniper == victim:
        return jsonify({"error": "sniper and victim (and they must differ) are required"}), 400
    return jsonify({"ok": True, "manual": _apply_cell(Stats(env()), sniper, victim)})


@bp.post("/compare/remedy/adjustment")
def remedy_adjustment():
    user_id = ((request.get_json(silent=True) or {}).get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    env().store.scoring.set_adjustment(user_id, env().store.human.adjustments().get(user_id, 0))
    return jsonify({"ok": True})


@bp.post("/compare/remedy/all")
def remedy_all():
    """Make True match the human everywhere, except cells whose victim is on the
    do-not-snipe list (those intentionally score nothing; the human decides by hand)."""
    stats = Stats(env())
    s = env().store
    h, manual, adj_now = s.human.matrix(), s.scoring.matrix(), s.scoring.adjustments()
    coded = stats.coded_matrix()["cells"]
    users = set(h) | {v for r in h.values() for v in r} | set(manual) | {v for r in manual.values() for v in r}
    users |= set(coded) | {v for r in coded.values() for v in r}
    fixed = skipped = 0
    for a in users:
        for b in users:
            if a == b:
                continue
            want, have = h.get(a, {}).get(b, 0), coded.get(a, {}).get(b, 0)
            if b in stats.dns and want != have:
                skipped += 1
                continue
            if manual.get(a, {}).get(b, 0) != want - have:
                s.scoring.set_cell(a, b, want - have)
                fixed += 1
    human_adj, adj_fixed = s.human.adjustments(), 0
    for u in set(human_adj) | set(adj_now):
        if (adj_now.get(u, 0) or 0) != human_adj.get(u, 0):
            s.scoring.set_adjustment(u, human_adj.get(u, 0))
            adj_fixed += 1
    return jsonify({"ok": True, "cells_fixed": fixed, "adjustments_fixed": adj_fixed, "dns_skipped": skipped})
