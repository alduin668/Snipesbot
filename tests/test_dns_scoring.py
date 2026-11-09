from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from snipesbot.analysis.stats import Stats
from snipesbot.models import Entry, FileRef, Kind, new_id
from snipesbot.services import exports
from snipesbot.web import create_app
from conftest import ALICE, BOB, CAROL, CHANNEL

NY = ZoneInfo("America/New_York")


def ts(day, hour=12):
    return f"{datetime(2026, 9, day, hour, tzinfo=NY).timestamp():.6f}"


def snipe(env, sniper, victims, when, **kw):
    return env.store.entries.insert(Entry(
        id=new_id(), kind=Kind.SNIPE, sniper=sniper, sniped=tuple(victims), channel=CHANNEL, message_ts=when,
        file=FileRef("F" + when[:7], "a.jpg", "image/jpeg"), created_at=1.0, **kw))


def board(env):
    return {r["user"]: (r["snipes"], r["sniped"], r["score"]) for r in Stats(env).leaderboard()}


def test_a_snipe_of_a_do_not_snipe_person_gives_nobody_points(env):
    snipe(env, ALICE, [BOB], ts(1))
    assert board(env) == {ALICE: (1, 0, 1), BOB: (0, 1, -1)}
    env.store.dns.add(BOB)
    assert board(env) == {}                                    # neither the sniper nor the target appears
    assert Stats(env).true_matrix()["cells"] == {} and Stats(env).coded_matrix()["users"] == []


def test_in_a_group_snipe_only_the_protected_victim_is_excluded(env):
    snipe(env, ALICE, [BOB, CAROL], ts(1))
    env.store.dns.add(BOB)
    assert board(env) == {ALICE: (1, 0, 1), CAROL: (0, 1, -1)}


def test_it_still_counts_as_a_snipe_for_activity_just_not_for_points(env):
    for day in (1, 2, 3):
        snipe(env, ALICE, [BOB], ts(day))
    env.store.dns.add(BOB)
    s = Stats(env)
    assert s.leaderboard() == []
    assert s.streaks() == {ALICE: 3}                           # three days in a row of sniping
    assert s.peak_windows()[0] == {"Tuesday": 1, "Wednesday": 1, "Thursday": 1}
    assert s.rivalries() == [] and s.untouchable() == {} and s.retaliation_speed() == {}   # scoring relationships: none
    hits = s.no_points_snipes()
    assert len(hits) == 3 and all(v == (BOB,) for _s, v in hits)


def test_manual_matrix_corrections_still_apply_to_protected_people(env):
    snipe(env, ALICE, [BOB], ts(1))
    env.store.dns.add(BOB)
    env.store.scoring.set_cell(ALICE, BOB, 2)                   # a deliberate hand correction is respected
    assert board(env)[ALICE][0] == 2


def test_removing_someone_from_the_list_makes_their_snipes_score_again(env):
    snipe(env, ALICE, [BOB], ts(1))
    env.store.dns.add(BOB)
    assert board(env) == {}
    env.store.dns.remove(BOB)
    assert board(env) == {ALICE: (1, 0, 1), BOB: (0, 1, -1)}


# ---- visibility ---------------------------------------------------------------------------------------------

@pytest.fixture
def client(env, no_download):
    app = create_app(env)
    app.testing = True
    return app.test_client()


def test_entries_say_when_they_earn_no_points(env, client):
    env.store.names.set_many({ALICE: "Alice", BOB: "Bob", CAROL: "Carol"})
    snipe(env, ALICE, [BOB, CAROL], ts(1))
    env.store.dns.add(BOB)
    e = client.get("/api/entries").get_json()["entries"][0]
    assert e["kind"] == "snipe" and e["no_points_ids"] == [BOB] and e["no_points_display"] == ["Bob"]


def test_analysis_lists_no_points_snipes(env, client):
    env.store.names.set_many({ALICE: "Alice", BOB: "Bob"})
    snipe(env, ALICE, [BOB], ts(1))
    snipe(env, ALICE, [BOB], ts(2))
    env.store.dns.add(BOB)
    block = client.get("/api/analysis").get_json()["no_points"]
    assert block["total"] == 2 and block["by_sniper"] == {"Alice": 2}
    assert block["recent"][0] == {"date": "2026-09-02", "sniper": "Alice", "victims": ["Bob"]}


def test_export_has_a_scores_column(env):
    import io
    from openpyxl import load_workbook
    snipe(env, ALICE, [BOB], ts(1))
    snipe(env, ALICE, [BOB, CAROL], ts(2))
    snipe(env, ALICE, [CAROL], ts(3))
    snipe(env, ALICE, [], ts(4))
    env.store.dns.add(BOB)
    rows = list(load_workbook(io.BytesIO(exports.build_xlsx(env))).active.iter_rows(values_only=True))[1:]
    assert [r[7] for r in rows] == ["No (do-not-snipe)", "Partly (do-not-snipe target excluded)", "Yes", "No (nobody sniped yet)"]


def test_the_do_not_snipe_dm_tells_the_sniper_it_wont_score(env, slack):
    from snipesbot.services import entries as svc
    from conftest import photo_msg
    env.store.dns.add(ALICE)
    svc.record_photo(env, photo_msg("1788000000.000100", CAROL, f"<@{ALICE}> snipe"))
    dm = [p for p in slack.posted if p["channel"] == CAROL][0]["text"]
    assert "earns no points" in dm


# ---- the review queue API ---------------------------------------------------------------------------------------

def test_review_queue_includes_incomplete_snipes_and_labels_them(env, client):
    from snipesbot.services import entries as svc
    from conftest import photo_msg
    svc.record_photo(env, photo_msg("1788000000.000100", CAROL, "who"))                     # unidentified
    marked = svc.record_photo(env, photo_msg("1788000100.000100", CAROL, "marked by reaction"))[0]
    svc.apply_state(env, marked.id, Kind.SNIPE, allow_incomplete=True)                      # snipe, nobody sniped
    done = svc.record_photo(env, photo_msg("1788000200.000100", CAROL, f"<@{ALICE}> snipe"))[0]
    q = client.get("/api/entries?review=1").get_json()
    assert q["total"] == 2 and [e["incomplete"] for e in q["entries"]] == [False, True]
    assert q["entries"][1]["kind_label"].startswith("Snipe") and "who" in q["entries"][1]["kind_label"]
    assert done.id not in [e["id"] for e in q["entries"]]
    assert client.get("/api/entries?pending=1").get_json()["total"] == 1                   # pending alone excludes it
    assert client.get("/api/status").get_json()["needs_review"] == 2
    # fixing it through the web UI path takes it out of the queue
    r = client.post(f"/api/entries/{marked.id}/kind", json={"kind": "snipe", "sniped": [ALICE]},
                    headers={"X-Requested-With": "snipesbot"})
    assert r.get_json()["incomplete"] is False and client.get("/api/entries?review=1").get_json()["total"] == 1


def test_the_web_path_still_insists_on_someone_sniped(env, client):
    from snipesbot.services import entries as svc
    from conftest import photo_msg
    e = svc.record_photo(env, photo_msg("1788000000.000100", CAROL, "who"))[0]
    r = client.post(f"/api/entries/{e.id}/kind", json={"kind": "snipe", "sniped": []}, headers={"X-Requested-With": "snipesbot"})
    assert r.status_code == 400


def test_slack_review_command_and_buttons_work_on_incomplete_snipes(env):
    from snipesbot.services import entries as svc
    from conftest import photo_msg
    e = svc.record_photo(env, photo_msg("1788000000.000100", CAROL, "marked by reaction"))[0]
    svc.apply_state(env, e.id, Kind.SNIPE, allow_incomplete=True)
    assert [x.id for x in env.store.entries.needs_review()] == [e.id]
    fixed = svc.resolve_pending(env, e.id, Kind.SNIPE, sniped=[ALICE])
    assert fixed is not None and fixed.sniped == (ALICE,)
    assert svc.resolve_pending(env, e.id, Kind.SNIPE, sniped=[BOB]) is None                 # no longer in the queue
