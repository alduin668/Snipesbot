from datetime import datetime
from zoneinfo import ZoneInfo

from snipesbot.analysis.stats import Stats, time_bucket
from snipesbot.models import Entry, FileRef, Kind, new_id
from conftest import ALICE, BOB, CAROL, CHANNEL

NY = ZoneInfo("America/New_York")


def ts_at(y, m, d, h, mi=0):
    return f"{datetime(y, m, d, h, mi, tzinfo=NY).timestamp():.6f}"


def snipe(env, sniper, victims, when, kind=Kind.SNIPE, **kw):
    return env.store.entries.insert(Entry(
        id=new_id(), kind=kind, sniper=sniper, sniped=tuple(victims), channel=CHANNEL, message_ts=when,
        file=FileRef("F" + when[:6], "a.jpg", "image/jpeg"), created_at=1.0, **kw))


def test_only_confirmed_snipes_score(env):
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 13), kind=Kind.SNOPE)
    snipe(env, ALICE, [], ts_at(2026, 9, 1, 14), kind=Kind.INCORRECT_SNIPE)
    snipe(env, ALICE, [], ts_at(2026, 9, 1, 15), kind=Kind.LIKELY_SNIPE)
    lb = {r["user"]: r for r in Stats(env).leaderboard()}
    assert (lb[ALICE]["snipes"], lb[BOB]["sniped"]) == (1, 1)


def test_deleted_entries_do_not_score(env):
    e = snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    env.store.entries.update(e.id, deleted_at=1.0)
    assert Stats(env).leaderboard() == []


def test_true_matrix_is_coded_plus_manual_and_scores_follow_it(env):
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    env.store.scoring.set_cell(ALICE, BOB, 2)
    env.store.scoring.set_cell(BOB, CAROL, 1)
    env.store.scoring.set_adjustment(CAROL, 0.5)
    s = Stats(env)
    assert s.coded_matrix()["cells"] == {ALICE: {BOB: 1}}
    assert s.true_matrix()["cells"] == {ALICE: {BOB: 3}, BOB: {CAROL: 1}}
    lb = {r["user"]: r for r in s.leaderboard()}
    assert lb[ALICE]["score"] == 3 and lb[BOB]["score"] == -2 and lb[CAROL]["score"] == -0.5


def test_a_negative_manual_cell_corrects_an_overcount(env):
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    env.store.scoring.set_cell(ALICE, BOB, -1)
    assert Stats(env).true_matrix()["cells"] == {}


def test_ranking_shares_ties_and_skips_the_next_rank(env):
    snipe(env, ALICE, [CAROL], ts_at(2026, 9, 1, 12))
    snipe(env, BOB, [CAROL], ts_at(2026, 9, 1, 13))
    ranks = {r["user"]: r["rank"] for r in Stats(env).leaderboard()}
    assert ranks == {ALICE: 1, BOB: 1, CAROL: 3}


def test_group_snipes_count_once_per_victim(env):
    snipe(env, ALICE, [BOB, CAROL], ts_at(2026, 9, 1, 12))
    s = Stats(env)
    assert s.true_matrix()["row_totals"][ALICE] == 2
    assert s.group_snipe_frequency()[0] == {ALICE: 1}


def test_streaks_and_peak_windows_use_local_time_not_utc(env):
    # 11 pm and 12:30 am New York are the same night in UTC, different local days.
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 23))
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 2, 0, 30))
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 5, 9))
    s = Stats(env)
    assert s.streaks()[ALICE] == 2
    by_day, by_bucket = s.peak_windows()
    assert by_day == {"Tuesday": 1, "Wednesday": 1, "Saturday": 1}
    assert by_bucket == {"night": 2, "morning": 1}


def test_time_buckets_cover_the_whole_day():
    assert [time_bucket(h) for h in (0, 4, 5, 11, 12, 16, 17, 20, 21, 23)] == [
        "night", "night", "morning", "morning", "afternoon", "afternoon", "evening", "evening", "night", "night"]


def test_untouchable_counts_whole_days_since_last_sniped(env):
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    s = Stats(env, now=datetime(2026, 9, 4, 13, tzinfo=NY))
    assert s.untouchable() == {BOB: 3}


def test_retaliation_speed_and_rivalries(env):
    snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 12))
    snipe(env, BOB, [ALICE], ts_at(2026, 9, 1, 14))
    s = Stats(env)
    assert s.retaliation_speed() == {BOB: 2.0}
    assert s.rivalries() == [(tuple(sorted((ALICE, BOB))), 2)]


def test_notable_ratio_needs_a_minimum_sample(env):
    for i in range(3):
        snipe(env, ALICE, [BOB], ts_at(2026, 9, 1, 10 + i), notable_auto=(i == 0))
    snipe(env, CAROL, [BOB], ts_at(2026, 9, 1, 15), notable_auto=True)
    assert Stats(env).notable_ratio() == {ALICE: 1 / 3}
