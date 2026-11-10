"""Matrix, adjustments and the Analysis tab."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...analysis import logic
from ...analysis.stats import Stats
from ...services import people, roster
from ..deps import env

bp = Blueprint("scoring", __name__)


def _names_for(user_ids) -> dict:
    people.ensure_names(env(), user_ids, limit=40)
    return env().store.names.all()


def _roster(stats: Stats, *extra) -> list:
    """The snipes-channel roster only (people @ed there, people in it, do-not-snipe
    people, and anyone with a manual number) -- NOT the whole Slack workspace.
    Ordered by True row total then name so every matrix view lines up."""
    true = stats.true_matrix()
    users = roster.roster_ids(env(), stats.coded_matrix()["users"], true["users"], stats.adjustments, *extra)
    names = _names_for(users)
    return sorted(users, key=lambda u: (-true["row_totals"].get(u, 0), (names.get(u) or u).lower()))


def _matrix_json(stats, order, row_totals, col_totals, cells, editable):
    names = _names_for(order)
    return jsonify({
        "users": [{"id": u, "display": people.display(u, names)} for u in order],
        "row_totals": {u: row_totals.get(u, 0) for u in order},
        "col_totals": {u: col_totals.get(u, 0) for u in order},
        "cells": cells, "editable": editable,
        "adjustments": {u: stats.adjustments.get(u, 0) for u in order},
    })


@bp.get("/matrix/<view>")
def matrix(view):
    stats = Stats(env())
    order = _roster(stats)
    if view == "coded":
        m = stats.coded_matrix()
        return _matrix_json(stats, order, m["row_totals"], m["col_totals"], m["cells"], False)
    if view == "manual":
        rows, cols = stats.manual_totals()
        return _matrix_json(stats, order, rows, cols, stats.manual, True)
    if view == "true":
        m = stats.true_matrix()
        return _matrix_json(stats, order, m["row_totals"], m["col_totals"], m["cells"], True)
    return jsonify({"error": "unknown matrix"}), 404


def _cell_args():
    body = request.get_json(silent=True) or {}
    sniper, victim = (body.get("sniper") or "").strip(), (body.get("victim") or "").strip()
    if not sniper or not victim or sniper == victim:
        return None, (jsonify({"error": "sniper and victim (and they must differ) are required"}), 400)
    try:
        return (sniper, victim, int(body.get("value", 0) or 0)), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "value must be a whole number"}), 400)


@bp.post("/matrix/manual/cell")
def set_manual_cell():
    args, err = _cell_args()
    if err:
        return err
    env().store.scoring.set_cell(*args)
    return jsonify({"ok": True})


@bp.post("/matrix/true/cell")
def set_true_cell():
    """Editing a True cell stores only the difference from the coded count in the
    manual matrix, so Coded stays pure and Manual stays "what was changed by hand"."""
    args, err = _cell_args()
    if err:
        return err
    sniper, victim, value = args
    coded = Stats(env()).coded_matrix()["cells"].get(sniper, {}).get(victim, 0)
    env().store.scoring.set_cell(sniper, victim, value - coded)
    return jsonify({"ok": True})


@bp.get("/adjustments")
def adjustments():
    stats = Stats(env())
    names = env().store.names.all()
    return jsonify({"users": [
        {"id": u, "display": people.display(u, names), "adjustment": stats.adjustments.get(u, 0)}
        for u in _roster(stats)]})


@bp.post("/adjustments")
def set_adjustment():
    body = request.get_json(silent=True) or {}
    user_id = (body.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        value = float(body.get("adjustment", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "adjustment must be a number"}), 400
    env().store.scoring.set_adjustment(user_id, value)
    return jsonify({"ok": True})


@bp.get("/analysis")
def analysis():
    stats = Stats(env())
    people.ensure_names(env(), [s.sniper for s in stats.snipes] + [v for s in stats.snipes for v in s.sniped])
    names = env().store.names.all()
    named = lambda d: {people.display(k, names): v for k, v in d.items()}  # noqa: E731
    by_day, by_bucket = stats.peak_windows()
    as_sniper, as_victim = stats.group_snipe_frequency()
    no_points = stats.no_points_snipes()
    by_sniper: dict = {}
    for s, _victims in no_points:
        by_sniper[s.sniper] = by_sniper.get(s.sniper, 0) + 1
    return jsonify({
        "no_points": {
            "total": len(no_points),
            "by_sniper": named(dict(sorted(by_sniper.items(), key=lambda kv: -kv[1]))),
            "recent": [{"date": s.local_time(env().cfg.tz).strftime("%Y-%m-%d"),
                        "sniper": people.display(s.sniper, names),
                        "victims": [people.display(v, names) for v in victims]}
                       for s, victims in no_points[:20]],
        },
        "leaderboard": [dict(r, user=people.display(r["user"], names)) for r in stats.leaderboard()],
        "rivalries": [{"pair": [people.display(u, names) for u in pair], "count": n} for pair, n in stats.rivalries()],
        "streaks": named(stats.streaks()),
        "untouchable": named(stats.untouchable()),
        "retaliation_speed": named(stats.retaliation_speed()),
        "peak_by_day": by_day, "peak_by_bucket": by_bucket,
        "notable_ratio": named(stats.notable_ratio()),
        "group_snipe_as_sniper": named(as_sniper), "group_snipe_as_victim": named(as_victim),
        "queue_size": stats.pending_count,
    })


@bp.get("/analysis/logic")
def analysis_logic():
    """The plain-words logic behind every analysis (single source: analysis/logic.py)."""
    return jsonify(logic.describe())
