import pytest

from snipesbot.models import Kind
from snipesbot.services import entries as svc, sync
from snipesbot.store import Busy
from conftest import ALICE, BOB, CAROL, CHANNEL, photo_msg, text_msg

NOW_TS = 1788000000.0


@pytest.fixture
def quiet(env, no_download):
    return lambda **kw: sync.run_sync(env, progress=lambda _l: None, **kw)


def seed_history(slack, n=3):
    for i in range(n):
        slack.history.append(photo_msg(f"{1788000000 + i * 60}.000100", CAROL, f"<@{ALICE}> snipe"))
    slack.history.append(text_msg("1788000500.000100", ALICE, "lol"))


def test_sync_archives_discovers_and_classifies(env, slack, quiet):
    seed_history(slack)
    stats = quiet()
    assert (stats.archived, stats.discovered) == (4, 3)
    assert {e.kind for e in env.store.entries.find()} == {Kind.SNIPE}
    assert all(e.source == "historical" for e in env.store.entries.find())
    assert env.store.archive.count() == 4


def test_second_sync_makes_no_per_entry_slack_calls(env, slack, quiet):
    """The old catch-up called reactions.get for every entry every six hours."""
    seed_history(slack, n=40)
    quiet()
    slack.calls.clear()
    stats = quiet(full=True)
    assert stats.discovered == 0 and stats.reclassified == 0
    assert slack.count("reactions_get") == 0 and slack.count("reactions_add") == 0
    assert set(slack.calls) <= {"conversations_history", "conversations_replies"}


def test_repair_reactions_is_the_explicit_verify_everything_mode(env, slack, quiet):
    seed_history(slack, n=5)
    quiet()
    slack.calls.clear()
    quiet(full=True, repair_reactions=True)
    assert slack.count("reactions_get") == 5          # one per photo, only when asked


def test_stale_reactions_are_repaired_and_only_those(env, slack, quiet):
    seed_history(slack, n=3)
    quiet()
    victim = env.store.entries.find()[0]
    env.store.entries.update(victim.id, reacted_kind=Kind.SNOPE)         # pretend Slack drifted
    slack.calls.clear()
    stats = quiet(full=True)
    assert stats.reactions_repaired == 1 and slack.count("reactions_get") == 1


def test_manual_decisions_are_never_changed_by_a_sync(env, slack, quiet):
    seed_history(slack)
    quiet()
    e = env.store.entries.find()[0]
    svc.apply_state(env, e.id, Kind.NOT_A_SNIPE)
    quiet(full=True)
    assert env.store.entries.get(e.id).kind is Kind.NOT_A_SNIPE


def test_an_edit_made_while_the_bot_was_offline_is_picked_up(env, slack, quiet):
    seed_history(slack, n=1)
    quiet()
    slack.history[0]["text"] = f"<@{ALICE}> snope"
    stats = quiet(full=True)
    assert stats.reclassified == 1
    assert env.store.entries.find()[0].kind is Kind.SNOPE


def test_the_watermark_only_moves_on_default_runs(env, slack, quiet):
    seed_history(slack, n=1)
    quiet(full=True)
    assert env.store.settings.get(sync.WATERMARK_KEY) is None
    quiet()
    assert env.store.settings.get(sync.WATERMARK_KEY) is not None


def test_a_failed_pass_does_not_advance_the_watermark(env, slack, quiet):
    slack.fail = {"conversations_history"}
    with pytest.raises(Exception):
        quiet()
    assert env.store.settings.get(sync.WATERMARK_KEY) is None


def test_thread_replies_are_archived(env, slack, quiet):
    parent = text_msg("1788000000.000100", ALICE, "p", reply_count=1)
    slack.history.append(parent)
    slack.replies[parent["ts"]] = [text_msg("1788000001.000100", BOB, "r", thread_ts=parent["ts"])]
    stats = quiet()
    assert stats.replies == 1 and len(env.store.archive.replies(CHANNEL, parent["ts"])) == 1


def test_only_one_sync_runs_at_a_time(env, slack, quiet, cfg):
    from snipesbot.store import Store
    other = Store.open(cfg)
    assert other.locks.acquire(sync.LOCK_NAME, 60)
    with pytest.raises(Busy):
        quiet()


def test_photo_deleted_in_slack_while_offline_is_noticed(env, slack, quiet, monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW_TS + 3600)
    seed_history(slack, n=3)
    quiet()
    gone = slack.history.pop(0)
    stats = quiet(full=True)
    assert stats.deleted == 1
    assert env.store.entries.get_by_message(CHANNEL, gone["ts"]).deleted
    assert env.store.entries.count() == 2


def test_a_mass_disappearance_is_treated_as_a_slack_problem_not_deletions(env, slack, quiet, monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW_TS + 3600)
    for i in range(15):
        slack.history.append(photo_msg(f"{1788000000 + i * 60}.000100", CAROL, f"<@{ALICE}> snipe"))
    slack.history.append(text_msg("1788009999.000100", ALICE, "still here"))
    quiet()
    slack.history[:] = [m for m in slack.history if not m.get("files")]      # all photos "vanish"
    stats = quiet(full=True)
    assert stats.deleted == 0 and stats.notes and env.store.entries.count() == 15


def test_an_empty_history_never_deletes_anything(env, slack, quiet):
    seed_history(slack)
    quiet()
    slack.history.clear()                           # e.g. the token lost a scope
    assert quiet(full=True).deleted == 0
    assert env.store.entries.count() == 3
