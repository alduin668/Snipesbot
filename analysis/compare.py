"""
Human truth vs. the bot.

Every so often a person reads the whole chat log and produces a matrix (who
sniped whom, how many times) and per-person adjustments. This module holds the
bot's numbers up against it, cell by cell, and says how to close each gap.

  human   the human's cell                (human_matrix)
  bot     the bot's cell, untouched       (Stats.coded_matrix)
  true    bot + manual corrections        (Stats.true_matrix) -- what scores today
  diff    human - bot                     >0 the bot missed snipes, <0 it over-counted
  remedy  the Manual-matrix value that makes True equal the human: human - bot
  gap     human - true                    what is still wrong after today's corrections

For every wrong cell it lists the entries worth looking at: if the bot
over-counted, its own confirmed snipes for that pair (one is probably mislabeled);
if it missed some, that sniper's non-snipe photos that mention the victim, and
their snipes with nobody recorded (probably mislabeled or incomplete).
"""
from __future__ import annotations

from ..classifier import MENTION_RE
from ..models import Kind
from ..services import people
from .stats import Stats

_MAX_SUSPECTS = 8


def _entry_brief(e, names, tz) -> dict:
    return {"id": e.id, "date": e.local_time(tz).strftime("%Y-%m-%d"), "kind": e.kind.label,
            "kind_key": e.kind.value, "sniper": people.display(e.sniper, names),
            "sniped": [people.display(v, names) for v in e.sniped],
            "caption": people.render_text(e.caption, names)[:160], "manual": e.manually_resolved}


def _suspects(stats: Stats, sniper: str, victim: str, diff: int, names) -> list:
    tz = stats.tz
    out = []
    if diff < 0:                                   # bot over-counted: look at its own snipes for this pair
        pool = [e for e in stats.snipes if e.sniper == sniper and victim in e.sniped]
    else:                                          # bot missed: look at what it did NOT count
        pool = []
        for e in stats.env.store.entries.all_active():
            if e.sniper != sniper or e.kind is Kind.SPECIAL:
                continue
            mentions = set(e.sniped) | set(MENTION_RE.findall(e.caption or ""))
            if e.kind is Kind.SNIPE and e.sniped:
                continue                           # already counted
            if victim in mentions or e.incomplete:
                pool.append(e)
    pool.sort(key=lambda e: -e.posted_at)
    return [_entry_brief(e, names, tz) for e in pool[:_MAX_SUSPECTS]]


def compare(stats: Stats, human_cells: dict, human_adj: dict, names) -> dict:
    coded = stats.coded_matrix()["cells"]
    true = stats.true_matrix()["cells"]
    manual = stats.manual
    users = set(coded) | {v for r in coded.values() for v in r}
    users |= set(true) | {v for r in true.values() for v in r}
    users |= set(human_cells) | {v for r in human_cells.values() for v in r}
    users |= set(human_adj) | set(stats.adjustments) | stats.dns

    cell = lambda m, s, v: (m.get(s) or {}).get(v, 0)          # noqa: E731
    discrepancies, matched = [], 0
    for s in sorted(users):
        for v in sorted(users):
            if s == v:
                continue
            h, b, t = cell(human_cells, s, v), cell(coded, s, v), cell(true, s, v)
            if h == b == t:
                matched += 1 if h else 0
                continue
            if h == b and h == t:
                continue
            diff, gap = h - b, h - t
            if diff == 0 and gap == 0:
                continue
            discrepancies.append({
                "sniper": s, "victim": v,
                "sniper_name": people.display(s, names), "victim_name": people.display(v, names),
                "human": h, "bot": b, "true": t, "manual": cell(manual, s, v),
                "diff": diff, "remedy": diff, "gap": gap, "resolved": gap == 0,
                "dns": v in stats.dns,
                "suspects": [] if gap == 0 else _suspects(stats, s, v, diff, names),
            })
    discrepancies.sort(key=lambda d: (d["resolved"], -abs(d["diff"]), d["sniper_name"].lower()))

    row = lambda m, u: sum((m.get(u) or {}).values())          # noqa: E731
    col = lambda m, u: sum(r.get(u, 0) for r in m.values())    # noqa: E731
    bot_adj = stats.adjustments
    persons = []
    for u in sorted(users, key=lambda u: people.display(u, names).lower()):
        hs, hd = row(human_cells, u), col(human_cells, u)
        bs, bd = row(coded, u), col(coded, u)
        ts, td = row(true, u), col(true, u)
        ha, ta = human_adj.get(u, 0) or 0, bot_adj.get(u, 0) or 0
        h_score, b_score, t_score = hs - hd + ha, bs - bd, ts - td + ta
        persons.append({
            "id": u, "name": people.display(u, names),
            "human": {"snipes": hs, "sniped": hd, "adjustment": ha, "score": h_score},
            "bot": {"snipes": bs, "sniped": bd, "adjustment": 0, "score": b_score},
            "true": {"snipes": ts, "sniped": td, "adjustment": ta, "score": t_score},
            "diff": {"snipes": hs - bs, "sniped": hd - bd, "score": h_score - b_score},
            "gap": {"snipes": hs - ts, "sniped": hd - td, "adjustment": ha - ta, "score": h_score - t_score},
        })
    persons = [p for p in persons if any(p[k]["snipes"] or p[k]["sniped"] or p[k]["adjustment"]
                                         for k in ("human", "bot", "true")) or p["id"] in stats.dns]
    open_cells = [d for d in discrepancies if not d["resolved"]]
    return {
        "discrepancies": discrepancies, "persons": persons,
        "summary": {
            "cells_wrong_vs_bot": sum(1 for d in discrepancies if d["diff"]),
            "cells_still_wrong": len(open_cells),
            "cells_matching": matched,
            "bot_missed": sum(d["diff"] for d in discrepancies if d["diff"] > 0),
            "bot_overcounted": -sum(d["diff"] for d in discrepancies if d["diff"] < 0),
            "people_score_gap": sum(1 for p in persons if p["gap"]["score"]),
            "human_total": sum(sum(r.values()) for r in human_cells.values()),
            "bot_total": sum(sum(r.values()) for r in coded.values()),
            "true_total": sum(sum(r.values()) for r in true.values()),
        },
    }
