"""Reaction events. Zeke's classification reactions are decisions; the table
below is the whole policy, one row per emoji."""
from __future__ import annotations

import logging
from typing import Optional

from ..env import Env
from ..models import PENDING_KINDS, Entry, Kind
from ..services import entries as svc

log = logging.getLogger("snipesbot")


def _not_incorrect(entry: Entry) -> bool:
    return entry.kind is not Kind.INCORRECT_SNIPE


def _set(kind: Kind):
    def apply(env: Env, entry: Entry) -> None:
        svc.apply_state(env, entry.id, kind, sniped=None if kind is Kind.SNIPE else (), allow_incomplete=True)
    return apply


def _confirm_likely(env: Env, entry: Entry) -> None:
    svc.resolve_pending(env, entry.id, Kind.SNIPE)


def _mark_unidentified(env: Env, entry: Entry) -> None:
    svc.apply_state(env, entry.id, Kind.UNIDENTIFIED, sniped=())


def _policy(env: Env) -> dict:
    """emoji -> (guard, action)"""
    e = env.cfg.emojis
    return {
        # Override from any state: flags the photo for the Special tab.
        e.special: (lambda x: x.kind is not Kind.SPECIAL, _set(Kind.SPECIAL)),
        # Deliberately narrow: only a confirmed snipe can become a missnipe.
        e.incorrect_snipe: (lambda x: x.kind is Kind.SNIPE, _set(Kind.INCORRECT_SNIPE)),
        e.not_a_snipe: (_not_incorrect, _set(Kind.NOT_A_SNIPE)),
        e.snipe: (_not_incorrect, _set(Kind.SNIPE)),
        e.snope: (_not_incorrect, _set(Kind.SNOPE)),
        # Orange is normally the bot's own "likely" marker; from Zeke it means
        # "yes, this is a snipe".
        e.likely_snipe: (lambda x: x.kind is Kind.LIKELY_SNIPE, _confirm_likely),
        # Zeke looked and it's genuinely unidentifiable: lock it as such.
        e.unidentified: (lambda x: x.kind in PENDING_KINDS, _mark_unidentified),
    }


def on_reaction_added(env: Env, event: dict) -> None:
    _handle(env, event, added=True)


def on_reaction_removed(env: Env, event: dict) -> None:
    _handle(env, event, added=False)


def _handle(env: Env, event: dict, *, added: bool) -> None:
    item = event.get("item") or {}
    channel, ts, user, name = item.get("channel"), item.get("ts"), event.get("user"), event.get("reaction")
    if item.get("type") != "message" or not (channel and ts and name):
        return
    if user and user == env.bot_user_id():
        return  # the bot's own reactions echo back as events

    entry = env.store.entries.get_by_message(channel, ts)
    if entry is None or entry.deleted:
        return

    if name == env.cfg.emojis.star:
        svc.record_star(env, channel, ts, user, added)
        return

    if added and user == env.cfg.zeke_user_id:
        rule: Optional[tuple] = _policy(env).get(name)
        if rule and rule[0](entry):
            try:
                rule[1](env, entry)
            except svc.InvalidTransition as exc:
                log.info("ignored :%s: on %s: %s", name, entry.id, exc)
