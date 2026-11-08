"""
Leaderboard and statistics.

Only confirmed SNIPE entries are events. Scoring is layered:

  1. coded matrix   what the bot/humans classified (from entries)
  2. manual matrix  hand corrections per sniper->victim cell (can be negative)
  3. true matrix    coded + manual, cell by cell; Snipes/Sniped come from this
  4. score          Snipes - Sniped + a separate per-person adjustment

`Stats` takes one snapshot of the data and answers everything from it, so a
page that needs eight statistics reads the database once, not eight times.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from ..env import Env
from ..models import Entry, Kind

TIME_BUCKETS = (("morning", 5, 12), ("afternoon", 12, 17), ("evening", 17, 21))  # else "night"
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def time_bucket(hour: int) -> str:
    for name, start, end in TIME_BUCKETS:
        if start <= hour < end:
            return name
    return "night"


class Stats:
    def __init__(self, env: Env, now: Optional[datetime] = None):
        self.env = env
        self.tz = env.cfg.tz
        self.now = now or datetime.now(self.tz)
        active = env.store.entries.all_active()
        self.snipes = [e for e in active if e.kind is Kind.SNIPE]
        self.pending_count = sum(1 for e in active if e.pending)
        self.manual = env.store.scoring.matrix()
        self.adjustments = env.store.scoring.adjustments()

    # -- matrices ------------------------------------------------------------
    def coded_matrix(self) -> dict:
        cells = defaultdict(lambda: defaultdict(int))
        users = set()
        for s in self.snipes:
            users.add(s.sniper)
            for victim in s.sniped:
                cells[s.sniper][victim] += 1
                users.add(victim)
        return self._matrix(users, {s: dict(v) for s, v in cells.items()})

    def true_matrix(self) -> dict:
        coded = self.coded_matrix()
        manual_users = set(self.manual) | {v for row in self.manual.values() for v in row}
        users = set(coded["users"]) | manual_users
        cells: dict = {}
        for sniper in users:
            for victim in users:
                if sniper == victim:
                    continue
                count = coded["cells"].get(sniper, {}).get(victim, 0) + self.manual.get(sniper, {}).get(victim, 0)
                if count:
                    cells.setdefault(sniper, {})[victim] = count
        return self._matrix(users, cells)

    @staticmethod
    def _matrix(users: set, cells: dict) -> dict:
        rows, cols = defaultdict(int), defaultdict(int)
        for sniper, row in cells.items():
            for victim, n in row.items():
                rows[sniper] += n
                cols[victim] += n
        return {"users": sorted(users), "cells": cells, "row_totals": dict(rows), "col_totals": dict(cols)}

    def manual_totals(self) -> tuple:
        rows = {s: sum(r.values()) for s, r in self.manual.items()}
        cols: dict = defaultdict(int)
        for row in self.manual.values():
            for victim, n in row.items():
                cols[victim] += n
        return rows, dict(cols)

    # -- leaderboard ---------------------------------------------------------
    def leaderboard(self) -> list:
        """Competition ("1224") ranking: ties share a rank and the next rank skips."""
        m = self.true_matrix()
        users = set(m["row_totals"]) | set(m["col_totals"]) | set(self.adjustments)
        rows = []
        for u in users:
            snipes, sniped = m["row_totals"].get(u, 0), m["col_totals"].get(u, 0)
            adj = self.adjustments.get(u, 0) or 0
            rows.append({"user": u, "snipes": snipes, "sniped": sniped, "adjustment": adj,
                         "score": snipes - sniped + adj})
        rows.sort(key=lambda r: (-r["score"], -r["snipes"], r["user"]))
        rank, prev = 0, None
        for i, r in enumerate(rows):
            if r["score"] != prev:
                rank, prev = i + 1, r["score"]
            r["rank"] = rank
        return rows

    # -- other stats -----------------------------------------------------------
    def rivalries(self, top_n: int = 5) -> list:
        pairs: dict = defaultdict(int)
        for s in self.snipes:
            for target in s.sniped:
                pairs[tuple(sorted((s.sniper, target)))] += 1
        return sorted(pairs.items(), key=lambda kv: -kv[1])[:top_n]

    def streaks(self) -> dict:
        days: dict = defaultdict(set)
        for s in self.snipes:
            days[s.sniper].add(s.local_time(self.tz).date())
        out = {}
        for user, ds in days.items():
            ordered = sorted(ds)
            best = cur = 1
            for prev, nxt in zip(ordered, ordered[1:]):
                cur = cur + 1 if (nxt - prev).days == 1 else 1
                best = max(best, cur)
            out[user] = best
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def untouchable(self) -> dict:
        last: dict = {}
        for s in self.snipes:
            for target in s.sniped:
                last[target] = max(last.get(target, s.posted_at), s.posted_at)
        now = self.now.timestamp()
        return dict(sorted(((u, int((now - t) // 86400)) for u, t in last.items()), key=lambda kv: -kv[1]))

    def retaliation_speed(self, window_hours: float = 48) -> dict:
        events = sorted(self.snipes, key=lambda s: s.posted_at)
        deltas: dict = defaultdict(list)
        for i, s in enumerate(events):
            for victim in s.sniped:
                for later in events[i + 1:]:
                    hours = (later.posted_at - s.posted_at) / 3600
                    if hours > window_hours:
                        break
                    if later.sniper == victim and s.sniper in later.sniped:
                        deltas[victim].append(hours)
                        break
        return {u: sum(h) / len(h) for u, h in deltas.items() if h}

    def peak_windows(self) -> tuple:
        by_day, by_bucket = defaultdict(int), defaultdict(int)
        for s in self.snipes:
            t = s.local_time(self.tz)
            by_day[DAYS[t.weekday()]] += 1
            by_bucket[time_bucket(t.hour)] += 1
        return dict(by_day), dict(by_bucket)

    def notable_ratio(self, minimum: int = 3) -> dict:
        total, notable = defaultdict(int), defaultdict(int)
        for s in self.snipes:
            total[s.sniper] += 1
            notable[s.sniper] += int(s.notable)
        ratios = {u: notable[u] / total[u] for u in total if total[u] >= minimum}
        return dict(sorted(ratios.items(), key=lambda kv: -kv[1]))

    def group_snipe_frequency(self) -> tuple:
        as_sniper, as_victim = defaultdict(int), defaultdict(int)
        for s in self.snipes:
            if s.group_snipe:
                as_sniper[s.sniper] += 1
                for v in s.sniped:
                    as_victim[v] += 1
        rank = lambda d: dict(sorted(d.items(), key=lambda kv: -kv[1]))  # noqa: E731
        return rank(as_sniper), rank(as_victim)


def day_name(entry: Entry, tz) -> str:
    return DAYS[entry.local_time(tz).weekday()]
