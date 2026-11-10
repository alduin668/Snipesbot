"""
Match the names in a pasted spreadsheet to Slack people, so nobody has to fix
every name by hand.

How a pasted name is resolved, in order:
  1. a remembered alias (you resolved it once before; "" means "ignore this name")
  2. an exact match on a roster person's full name
  3. an exact match on a roster person's first name, if only ONE roster person has it
  4. an exact match on anyone in the workspace directory, if only ONE person has it
Anything else is "ambiguous" (several people fit) or "unmatched" (none fit) and
comes back with a short list of closest guesses. Names that carry no numbers at
all are never asked about.

parse() also understands sheet layout: a total column ("Sniped", "Total", ...)
and a totals row ("Sniper", "Total", ...) are skipped, and used to warn when the
sheet's own totals don't add up.
"""
from __future__ import annotations

import csv
import difflib
import io
import re

ALIASES_KEY = "name_aliases"
SKIP = {"", "sniped", "snipes", "sniper", "snipers", "total", "totals", "sum", "grand total",
        "score", "adjustment", "adjustments", "adj"}
TOTAL_WORDS = {"sniped", "snipes", "total", "totals", "sum"}


def norm(s) -> str:
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _num(raw) -> int:
    raw = (raw or "").strip().replace(",", "")
    try:
        return int(float(raw)) if raw else 0
    except ValueError:
        return 0


def parse(text: str) -> dict:
    """Pasted grid -> {"cells": {(sniper_key, victim_key): n}, "labels": {key: raw name},
    "order": [row keys], "keys": [every key], "warnings": [...]}. Keys are norm()ed names."""
    text = (text or "").strip("\n")
    if not text.strip():
        raise ValueError("nothing to import")
    first = text.splitlines()[0]
    delim = "\t" if "\t" in first else (";" if first.count(";") > first.count(",") else ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    header = rows[0]
    cols = [(i, h.strip()) for i, h in enumerate(header) if i and norm(h) not in SKIP]
    total_cols = [i for i, h in enumerate(header) if i and norm(h) in TOTAL_WORDS]
    labels = {norm(h): h for _, h in cols}
    cells, order, warnings, totals_row = {}, [], [], None

    for r in rows[1:]:
        if not r:
            continue
        label = r[0].strip()
        if norm(label) in SKIP:
            if label and totals_row is None:
                totals_row = r                              # the column-totals row
            continue
        rk = norm(label)
        labels.setdefault(rk, label)
        order.append(rk)
        row_sum = 0
        for i, name in cols:
            n = _num(r[i]) if i < len(r) else 0
            ck = norm(name)
            if n and ck != rk:
                cells[(rk, ck)] = cells.get((rk, ck), 0) + n
                row_sum += n
        for i in total_cols:
            if i < len(r) and r[i].strip() and _num(r[i]) != row_sum:
                warnings.append(f"{label}: the sheet's total says {_num(r[i])} but the row adds up to {row_sum}")
                break

    if totals_row:
        sums: dict = {}
        for (_, v), n in cells.items():
            sums[v] = sums.get(v, 0) + n
        for i, name in cols:
            if i < len(totals_row) and totals_row[i].strip() and _num(totals_row[i]) != sums.get(norm(name), 0):
                warnings.append(f"{name}: the sheet says sniped {_num(totals_row[i])} times but the column adds up to {sums.get(norm(name), 0)}")

    keys = list(dict.fromkeys(order + [norm(h) for _, h in cols]))
    return {"cells": cells, "labels": labels, "order": list(dict.fromkeys(order)), "keys": keys,
            "warnings": warnings[:12] + ([f"...and {len(warnings) - 12} more"] if len(warnings) > 12 else [])}


def _index(names: dict):
    full, first = {}, {}
    for uid, name in (names or {}).items():
        n = norm(name)
        if n:
            full.setdefault(n, set()).add(uid)
            first.setdefault(n.split()[0], set()).add(uid)
    return full, first


class Matcher:
    """roster / directory: user id -> name. aliases: {name: user id, or "" to ignore}."""

    def __init__(self, roster: dict, directory: dict, aliases: dict):
        self.aliases = {norm(k): v for k, v in (aliases or {}).items()}
        self.r_full, self.r_first = _index(roster)
        self.d_full, self.d_first = _index(directory)

    def match(self, key: str) -> dict:
        if key in SKIP:
            return {"status": "skip", "user_id": "", "candidates": []}
        if key in self.aliases:
            return {"status": "alias", "user_id": self.aliases[key], "candidates": []}
        for index in (self.r_full, self.r_first, self.d_full):
            ids = index.get(key, ())
            if len(ids) == 1:
                return {"status": "auto", "user_id": next(iter(ids)), "candidates": []}
            if len(ids) > 1:
                return {"status": "ambiguous", "user_id": "", "candidates": sorted(ids)[:8]}
        return {"status": "unmatched", "user_id": "", "candidates": self.suggest(key)}

    def suggest(self, key: str, limit: int = 6) -> list:
        out: list = []

        def add(ids):
            for i in ids:
                if i not in out:
                    out.append(i)

        for full, first in ((self.r_full, self.r_first), (self.d_full, self.d_first)):
            if len(key) >= 3:
                add(i for k, ids in first.items() if k.startswith(key) or (len(k) >= 3 and key.startswith(k)) for i in sorted(ids)[:3])
            for k in difflib.get_close_matches(key, list(full) + list(first), n=4, cutoff=0.7):
                add(full.get(k) or first.get(k) or ())
            if len(out) >= limit:
                break
        return out[:limit]
