"""
Caption classification: pure functions, no I/O.

Every photo is classified from its own caption only. Rules, in order:

  1. A snope keyword wins (with or without an @mention).
  2. @mention + snipe keyword            -> SNIPE
  3. only @mention(s), nothing else      -> LIKELY_SNIPE
  4. @mention mixed with ordinary text   -> UNIDENTIFIED
  5. no @mention                         -> UNIDENTIFIED

Keywords match whole words, case-insensitively ("sniper" is not "snipe"),
and a keyword directly preceded by a negation ("not a snipe", "no snipe")
doesn't count, so those captions fall through to a human instead of being
auto-confirmed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional, Sequence

from .models import Kind

# <@U123> and the older <@U123|name> form.
MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")

_NEGATION_BEFORE_RE = re.compile(
    r"\b(?:not|no|never|isn'?t|wasn'?t|nope)\s+(?:a\s+|an\s+|the\s+|really\s+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    kind: Kind
    sniped: tuple = ()


@lru_cache(maxsize=32)
def _keyword_re(words: tuple) -> re.Pattern:
    alternation = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def _normalise(text: Optional[str]) -> str:
    text = (text or "").replace("\u2019", "'")
    return MENTION_RE.sub(" ", text)  # an id like U02SNIPE1 must never match a keyword


def has_keyword(text: Optional[str], words: Sequence[str]) -> bool:
    """True if any keyword appears as a whole word that isn't negated."""
    clean = _normalise(text)
    for match in _keyword_re(tuple(words)).finditer(clean):
        if not _NEGATION_BEFORE_RE.search(clean[: match.start()]):
            return True
    return False


def extract_mentions(text: Optional[str], exclude: Optional[str] = None) -> list:
    """Distinct mentioned user ids in order of appearance."""
    seen, out = set(), []
    for uid in MENTION_RE.findall(text or ""):
        if uid != exclude and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def is_bare_mention(text: Optional[str]) -> bool:
    """True if nothing is left once @mentions are stripped out."""
    return MENTION_RE.sub("", text or "").strip() == ""


def classify(text: Optional[str], sniper: str, *,
             snipe_words: Iterable[str], snope_words: Iterable[str]) -> Classification:
    snipe_words, snope_words = tuple(snipe_words), tuple(snope_words)
    mentions = tuple(extract_mentions(text, exclude=sniper))

    if has_keyword(text, snope_words):
        return Classification(Kind.SNOPE, mentions)
    if not mentions:
        return Classification(Kind.UNIDENTIFIED)
    if has_keyword(text, snipe_words):
        return Classification(Kind.SNIPE, mentions)
    if is_bare_mention(text):
        return Classification(Kind.LIKELY_SNIPE, mentions)
    return Classification(Kind.UNIDENTIFIED)


def guessed_sniped(caption: Optional[str], sniper: str,
                   context_texts: Iterable[str] = ()) -> list:
    """Hint for a human reviewer: mentions in the caption, falling back to
    mentions in neighbouring messages. Never used to classify anything."""
    found = extract_mentions(caption, exclude=sniper)
    if found:
        return found
    return extract_mentions(" ".join(context_texts), exclude=sniper)


_NAS_RE = re.compile(r"\bnas\b", re.IGNORECASE)


def says_nas(text: Optional[str]) -> bool:
    """A thread reply that means 'not a snipe' ("#snipe nas"), as a whole word."""
    return bool(_NAS_RE.search(text or ""))
