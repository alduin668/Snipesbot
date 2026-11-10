"""
The current logic of every analysis, in plain words, for the Analysis tab.
Keep this in step with analysis/stats.py and classifier.py: it is what a person
reads to understand why a number is what it is.
"""
from __future__ import annotations

CLASSIFICATION = [
    "A photo is judged from its own caption only.",
    "1. A snope word wins, with or without an @mention  → Snope.",
    "2. @mention + a snipe word  → Snipe (counts toward the score).",
    "3. Only @mention(s), nothing else  → Likely snipe (waits in the Review Queue).",
    "4. @mention mixed with ordinary text, or no @mention  → Unidentified (needs a human).",
    "Keywords match whole words, case-insensitively; a negated keyword (“not a snipe”, “no snipe”) doesn't count.",
    "Anything a person decides is locked: syncs and edits never change it.",
]

SCORING = [
    "Only confirmed Snipe entries are events. Each victim of a snipe is one cell: sniper → victim, +1.",
    "Do-not-snipe rule: a snipe of someone on the do-not-snipe list is still logged (streaks, activity, Live Log) "
    "but scores nothing for the sniper or the victim, and is left out of every matrix. In a group snipe the other victims still score.",
    "Coded matrix = what the bot counted. Manual matrix = hand corrections per cell (can be negative). "
    "True matrix = Coded + Manual, cell by cell.",
    "Snipes = a person's True row total; Sniped = their True column total.",
    "Score = Snipes − Sniped + that person's Adjustment (the adjustment may be fractional or negative).",
    "Roster: only people @ed in the snipes channel, people in it, and people on the do-not-snipe list "
    "(plus anyone who already has a manual number), never the whole Slack workspace.",
]

#: One entry per analysis shown on the Analysis tab: key matches the API field.
ANALYSES = [
    {"key": "leaderboard", "title": "Leaderboard", "order": "Score high → low",
     "logic": "Score = Snipes − Sniped + Adjustment, from the True matrix. Sorted by score, then by Snipes, then by name. "
              "Ties share a rank and the next rank is skipped (1, 2, 2, 4)."},
    {"key": "rivalries", "title": "Rivalries", "order": "Most snipes between the pair, high → low",
     "logic": "For each pair of people, count every scoring snipe between them in either direction "
              "(A→B and B→A together). The top 5 pairs are shown."},
    {"key": "no_points", "title": "No-points snipes", "order": "Newest first (recent list); by-sniper high → low",
     "logic": "Snipes that hit someone on the do-not-snipe list. Logged but unscored. "
              "Shows the total, the 20 most recent, and a count per sniper."},
    {"key": "streaks", "title": "Sniping streaks", "order": "Longest streak (days) high → low",
     "logic": "For each sniper, the longest run of consecutive calendar days (in the configured timezone) "
              "on which they posted at least one confirmed snipe. Includes no-points snipes."},
    {"key": "untouchable", "title": "Untouchable", "order": "Most days since last sniped, high → low",
     "logic": "For each person who has been sniped (scoring snipes only), the whole days since the most recent "
              "time they were sniped. Bigger = harder to catch."},
    {"key": "retaliation_speed", "title": "Retaliation speed", "order": "Slowest average first (hours, high → low)",
     "logic": "When A snipes B, look forward up to 48 hours for B sniping A back. The first such reply counts. "
              "A person's number is the average hours it took them to retaliate. Only people who retaliated appear."},
    {"key": "peak_by_day", "title": "Peak days", "order": "Most snipes, high → low",
     "logic": "Confirmed snipes grouped by weekday (local time)."},
    {"key": "peak_by_bucket", "title": "Peak time of day", "order": "Most snipes, high → low",
     "logic": "Confirmed snipes grouped by local time: morning 05–12, afternoon 12–17, evening 17–21, otherwise night."},
    {"key": "notable_ratio", "title": "Notable ratio", "order": "Highest share, high → low",
     "logic": "Share of a sniper's confirmed snipes that were marked notable (⭐ by Zeke, 3+ stars from others, "
              "or set by hand). Only snipers with at least 3 snipes are listed."},
    {"key": "group_snipe_as_sniper", "title": "Group snipes: as sniper", "order": "Most, high → low",
     "logic": "How many snipes with two or more victims each person posted."},
    {"key": "group_snipe_as_victim", "title": "Group snipes: as victim", "order": "Most, high → low",
     "logic": "How many times each person was one of several victims in a group snipe."},
    {"key": "queue_size", "title": "Review queue size", "order": "—",
     "logic": "Photos needing a decision: unidentified, likely snipes, and snipes with nobody recorded as sniped."},
]


def describe() -> dict:
    return {"classification": CLASSIFICATION, "scoring": SCORING, "analyses": ANALYSES}
