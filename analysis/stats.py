"""
Leaderboard and statistics for SnipesBot.

Confirmed green snipes are the only events that count as landed/sniped.
Scoring is built in layers:

  1. "Coded" matrix (snipe_matrix()) -- purely what the bot classified from
     snipes.json. Never edited by hand.
  2. "Manual" matrix (storage/json_store.py's manual_matrix.json) -- Zeke's
     own corrections/additions on top of that, cell by cell. Can be
     negative (the bot over-counted something) or positive (the bot never
     saw it at all).
  3. "True" matrix (true_matrix()) -- the cell-wise sum of the two. This is
     what Snipes/Sniped actually come from for scoring.
  4. leaderboard()'s Score = True's Snipes - Sniped + a separate, per-person
     manual Adjustment (adjustments.json).

A red "incorrect_snipe" reaction is not a snipe and does not affect the
Snipes/Sniped counts; it no longer applies an automatic penalty either --
if it should cost someone points, that's now done via a manual adjustment.
"""
from collections import defaultdict
from datetime import datetime

import config
from storage import json_store, user_names

TIME_BUCKETS = [
    (5, 12, "morning"),
    (12, 17, "afternoon"),
    (17, 21, "evening"),
    (21, 29, "late-night"),  # wraps past midnight up to 5am
]


def _confirmed_snipes():
    return [
        s for s in json_store.load_snipes()
        if s.get("kind") == "snipe" and not s.get("ignored")
    ]


def _incorrect_snipes():
    return [
        s for s in json_store.load_snipes()
        if s.get("kind") == "incorrect_snipe" and not s.get("ignored")
    ]


def snipe_matrix():
    """The "coded" sniper x victim count matrix -- purely what the bot
    itself classified, built directly from confirmed snipes.json entries.
    One increment per (sniper, victim) pair, so a group snipe with N
    victims adds one count to N different cells in the sniper's row. Never
    edited by hand -- see true_matrix() for the version that layers Zeke's
    manual corrections on top, which is what leaderboard() actually scores
    from. Returns:

        {
          "users": [...every user who appears as a sniper or a victim...],
          "cells": {sniper: {victim: count}},
          "row_totals": {sniper: total times they sniped anyone},
          "col_totals": {victim: total times they were sniped},
        }
    """
    cells = defaultdict(lambda: defaultdict(int))
    row_totals = defaultdict(int)
    col_totals = defaultdict(int)
    users = set()

    for s in _confirmed_snipes():
        sniper = s["sniper"]
        users.add(sniper)
        for victim in s.get("sniped", []):
            cells[sniper][victim] += 1
            row_totals[sniper] += 1
            col_totals[victim] += 1
            users.add(victim)

    return {
        "users": sorted(users),
        "cells": {s: dict(v) for s, v in cells.items()},
        "row_totals": dict(row_totals),
        "col_totals": dict(col_totals),
    }


def true_matrix():
    """The "true" sniper x victim matrix that scoring is actually based on:
    the bot's own coded matrix (snipe_matrix(), above) plus whatever Zeke
    has manually layered on top of it (storage/json_store.py's
    manual_matrix.json) -- cell-wise sum of the two. A manual cell can be
    negative (correcting something the bot over-counted) as well as
    positive (logging a snipe the bot never saw). Same shape as
    snipe_matrix()."""
    coded = snipe_matrix()
    manual = json_store.get_manual_matrix()

    manual_users = set(manual) | {victim for row in manual.values() for victim in row}
    users = set(coded["users"]) | manual_users

    cells = defaultdict(lambda: defaultdict(int))
    row_totals, col_totals = defaultdict(int), defaultdict(int)

    for sniper in users:
        for victim in users:
            if sniper == victim:
                continue
            count = coded["cells"].get(sniper, {}).get(victim, 0) + manual.get(sniper, {}).get(victim, 0)
            if count:
                cells[sniper][victim] = count
                row_totals[sniper] += count
                col_totals[victim] += count

    return {
        "users": sorted(users),
        "cells": {s: dict(v) for s, v in cells.items()},
        "row_totals": dict(row_totals),
        "col_totals": dict(col_totals),
    }


def leaderboard():
    """Snipes - Sniped + a manual Adjustment (see storage/json_store.py's
    adjustments.json), per user. Snipes/Sniped are the row/column totals
    from true_matrix() -- i.e. the bot's coded matrix plus whatever Zeke has
    manually corrected or added on top of it -- and the adjustment is a
    further, independent per-person nudge on top of that.

    Rank is competition ("1224") style: ties share a rank, and the rank
    after a tie skips ahead by however many people were tied, e.g. two
    people tied for 3rd are both rank 3, and whoever's next is rank 5."""
    matrix = true_matrix()
    row_totals, col_totals = matrix["row_totals"], matrix["col_totals"]
    adjustments = json_store.get_adjustments()

    users = set(row_totals) | set(col_totals) | set(adjustments)
    rows = []
    for u in users:
        snipes = row_totals.get(u, 0)
        sniped = col_totals.get(u, 0)
        adjustment = adjustments.get(u, 0) or 0
        rows.append({
            "user": u,
            "snipes": snipes,
            "sniped": sniped,
            "adjustment": adjustment,
            "score": snipes - sniped + adjustment,
        })

    rows.sort(key=lambda r: (-r["score"], -r["snipes"], r["user"]))

    rank, prev_score = 0, None
    for i, r in enumerate(rows):
        if r["score"] != prev_score:
            rank = i + 1
            prev_score = r["score"]
        r["rank"] = rank

    return rows


def rivalries(top_n=5):
    pair_counts = defaultdict(int)
    for s in _confirmed_snipes():
        for target in s.get("sniped", []):
            pair = tuple(sorted([s["sniper"], target]))
            pair_counts[pair] += 1
    ranked = sorted(pair_counts.items(), key=lambda kv: -kv[1])
    return ranked[:top_n]


def sniping_streaks():
    days_by_user = defaultdict(set)
    for s in _confirmed_snipes():
        day = datetime.fromisoformat(s["date_time"]).date()
        days_by_user[s["sniper"]].add(day)

    streaks = {}
    for user, days in days_by_user.items():
        sorted_days = sorted(days)
        best = cur = 1
        for i in range(1, len(sorted_days)):
            if (sorted_days[i] - sorted_days[i - 1]).days == 1:
                cur += 1
                best = max(best, cur)
            else:
                cur = 1
        streaks[user] = best if sorted_days else 0
    return dict(sorted(streaks.items(), key=lambda kv: -kv[1]))


def untouchable_award():
    last_sniped = {}
    for s in _confirmed_snipes():
        dt = datetime.fromisoformat(s["date_time"])
        for target in s.get("sniped", []):
            if target not in last_sniped or dt > last_sniped[target]:
                last_sniped[target] = dt

    now = datetime.utcnow()
    safe_days = {u: (now - dt).days for u, dt in last_sniped.items()}
    return dict(sorted(safe_days.items(), key=lambda kv: -kv[1]))


def retaliation_speed(window_hours=48):
    events = sorted(_confirmed_snipes(), key=lambda s: s["date_time"])
    deltas = defaultdict(list)

    for i, s in enumerate(events):
        dt_i = datetime.fromisoformat(s["date_time"])
        for victim in s.get("sniped", []):
            for later in events[i + 1:]:
                dt_later = datetime.fromisoformat(later["date_time"])
                hours = (dt_later - dt_i).total_seconds() / 3600
                if hours > window_hours:
                    break
                if later["sniper"] == victim and s["sniper"] in later.get("sniped", []):
                    deltas[victim].append(hours)
                    break

    return {u: sum(hs) / len(hs) for u, hs in deltas.items() if hs}


def peak_windows():
    by_day = defaultdict(int)
    by_bucket = defaultdict(int)
    for s in _confirmed_snipes():
        dt = datetime.fromisoformat(s["date_time"])
        by_day[dt.strftime("%A")] += 1
        hour = dt.hour
        bucket = next(
            (name for start, end, name in TIME_BUCKETS if start <= hour < end),
            "late-night",
        )
        by_bucket[bucket] += 1
    return dict(by_day), dict(by_bucket)


def most_notable_ratio():
    counts, notable_counts = defaultdict(int), defaultdict(int)
    for s in _confirmed_snipes():
        counts[s["sniper"]] += 1
        if s.get("notable"):
            notable_counts[s["sniper"]] += 1
    ratios = {
        u: notable_counts[u] / counts[u]
        for u in counts if counts[u] >= 3
    }
    return dict(sorted(ratios.items(), key=lambda kv: -kv[1]))


def group_snipe_frequency():
    as_sniper, as_victim = defaultdict(int), defaultdict(int)
    for s in _confirmed_snipes():
        if not s.get("group_snipe"):
            continue
        as_sniper[s["sniper"]] += 1
        for v in s.get("sniped", []):
            as_victim[v] += 1
    return (
        dict(sorted(as_sniper.items(), key=lambda kv: -kv[1])),
        dict(sorted(as_victim.items(), key=lambda kv: -kv[1])),
    )


def unidentified_queue_size():
    return len(json_store.load_unidentified())


def _named(value):
    """Swaps raw Slack user IDs for "Name (ID)" display strings, for the
    webapp's formatted Analysis tab. Accepts a single id, a list/tuple of
    ids (e.g. a rivalry pair), or a dict keyed by id (e.g. streaks) --
    whichever shape the caller already has."""
    names = user_names.get_cached_names()
    if isinstance(value, dict):
        return {user_names.format_side_by_side(k, names): v for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [user_names.format_side_by_side(v, names) for v in value]
    return user_names.format_side_by_side(value, names)


if __name__ == "__main__":
    print("Leaderboard:", leaderboard())
    print("Rivalries:", rivalries())
    print("Streaks:", sniping_streaks())
    print("Untouchable:", untouchable_award())
    print("Retaliation speed (hrs):", retaliation_speed())
    print("Peak windows:", peak_windows())
    print("Notable ratio:", most_notable_ratio())
    print("Group snipe frequency (sniper, victim):", group_snipe_frequency())
    print("Unidentified queue size:", unidentified_queue_size())
