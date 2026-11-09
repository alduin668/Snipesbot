import pytest

from snipesbot.models import Kind
from snipesbot.services import entries as svc
from snipesbot.slack_app import reactions
from conftest import ALICE, BOB, BOT, CAROL, CHANNEL, ZEKE, photo_msg

TS = "1788000000.000100"


def record(env, caption):
    return svc.record_photo(env, photo_msg(TS, CAROL, caption))[0]


def react(env, name, user=ZEKE, added=True, ts=TS):
    ev = {"reaction": name, "user": user, "item": {"type": "message", "channel": CHANNEL, "ts": ts}}
    (reactions.on_reaction_added if added else reactions.on_reaction_removed)(env, ev)
    return env.store.entries.get_by_message(CHANNEL, ts)


@pytest.mark.parametrize("start, emoji, kind", [
    (f"<@{ALICE}>", "large_green_circle", Kind.SNIPE),
    (f"<@{ALICE}> snipe", "large_purple_circle", Kind.SNOPE),
    (f"<@{ALICE}> snipe", "black_circle", Kind.NOT_A_SNIPE),
    (f"<@{ALICE}> snipe", "red_circle", Kind.INCORRECT_SNIPE),
    (f"<@{ALICE}>", "large_orange_circle", Kind.SNIPE),       # orange from Zeke confirms a likely snipe
    ("untagged", "fingerprint", Kind.SPECIAL),
    ("untagged", "question", Kind.UNIDENTIFIED),
])
def test_zekes_reactions_are_manual_decisions(env, start, emoji, kind):
    record(env, start)
    e = react(env, emoji)
    assert e.kind is kind and e.manually_resolved


def test_question_mark_on_a_pending_photo_now_locks_it_as_unidentified(env):
    """This reaction was dead code before the rewrite: it called a function that rejected its own kind."""
    record(env, f"<@{ALICE}>")                    # likely_snipe, pending
    e = react(env, "question")
    assert e.kind is Kind.UNIDENTIFIED and e.manually_resolved and e.sniped == ()


def test_red_only_applies_to_a_confirmed_snipe(env):
    record(env, f"<@{ALICE}>")                    # likely, not confirmed
    assert react(env, "red_circle").kind is Kind.LIKELY_SNIPE


def test_nothing_overrides_a_missnipe_except_special(env):
    record(env, f"<@{ALICE}> snipe")
    react(env, "red_circle")
    for emoji in ("large_green_circle", "large_purple_circle", "black_circle"):
        assert react(env, emoji).kind is Kind.INCORRECT_SNIPE
    assert react(env, "fingerprint").kind is Kind.SPECIAL


def test_only_zeke_can_decide(env):
    record(env, f"<@{ALICE}>")
    assert react(env, "large_green_circle", user=BOB).kind is Kind.LIKELY_SNIPE


def test_removing_a_reaction_changes_nothing(env):
    record(env, f"<@{ALICE}> snipe")
    react(env, "black_circle")
    assert react(env, "black_circle", added=False).kind is Kind.NOT_A_SNIPE


def test_the_bots_own_reaction_echoes_are_ignored(env, slack):
    record(env, f"<@{ALICE}> snipe")
    slack.calls.clear()
    assert react(env, "large_purple_circle", user=BOT).kind is Kind.SNIPE
    assert slack.calls == [] or set(slack.calls) == {"auth_test"}


def test_stars_toggle_notable_both_ways(env):
    record(env, "x")
    assert react(env, "star", user=ZEKE).notable
    assert not react(env, "star", user=ZEKE, added=False).notable


def test_reactions_on_untracked_or_deleted_messages_are_ignored(env):
    assert react(env, "large_green_circle", ts="9.9") is None
    record(env, f"<@{ALICE}>")
    svc.handle_deleted(env, CHANNEL, TS)
    react(env, "large_green_circle")
    assert env.store.entries.get_by_message(CHANNEL, TS).kind is Kind.LIKELY_SNIPE


def test_a_snipe_reaction_with_nobody_to_snipe_becomes_an_incomplete_snipe_in_the_review_queue(env, slack):
    """Marking a photo green in Slack has nowhere to say who was sniped. The old bot made a
    'malformed snipe' that a script had to repair; now it stays a snipe and shows up for fixing."""
    entry = record(env, "untagged")
    e = react(env, "large_green_circle")
    assert e.kind is Kind.SNIPE and e.sniped == () and e.manually_resolved
    assert e.incomplete and e.needs_review
    assert [x.id for x in env.store.entries.needs_review()] == [entry.id]
    assert env.store.entries.count_needs_review() == 1
    assert [n for n, u in slack.reactions[(CHANNEL, TS)] if u == BOT] == ["large_green_circle"]   # still green on Slack


def test_fixing_an_incomplete_snipe_takes_it_out_of_the_queue(env):
    entry = record(env, "untagged")
    react(env, "large_green_circle")
    fixed = svc.resolve_pending(env, entry.id, Kind.SNIPE, sniped=[ALICE])
    assert fixed.sniped == (ALICE,) and not fixed.incomplete
    assert env.store.entries.needs_review() == []


def test_a_snipe_with_nobody_sniped_scores_nothing_until_fixed(env):
    from snipesbot.analysis.stats import Stats
    record(env, "untagged")
    react(env, "large_green_circle")
    assert Stats(env).leaderboard() == [] and Stats(env).pending_count == 1
