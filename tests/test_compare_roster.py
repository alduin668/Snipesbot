"""Roster (no whole-workspace names) and the Compare tab."""
import pytest

from snipesbot.models import Kind
from snipesbot.services import roster
from snipesbot.web import create_app
from conftest import ALICE, BOB, CAROL, CHANNEL
from test_web import add, H


@pytest.fixture
def client(env, no_download):
    app = create_app(env)
    app.testing = True
    return app.test_client()


def ids(client, view="true"):
    return {u["id"] for u in client.get(f"/api/matrix/{view}").get_json()["users"]}


def test_matrices_ignore_workspace_wide_directory_names(env, client):
    env.store.names.set_many({f"UOTHER{i:04d}": f"Stranger {i}" for i in range(300)})
    env.store.names.set_many({ALICE: "Alice", CAROL: "Carol"})
    add(env, 1, "", kind=Kind.SNIPE)                         # CAROL -> ALICE
    got = ids(client)
    assert got == {ALICE, CAROL}
    assert {u["id"] for u in client.get("/api/adjustments").get_json()["users"]} == {ALICE, CAROL}


def test_roster_has_mentioned_posters_members_and_dns(env):
    from conftest import photo_msg
    from snipesbot.services import archiver
    archiver.archive_message(env, photo_msg("1788000000.000100", CAROL, f"<@{BOB}> snipe"))
    env.store.dns.add("UDNS00001")
    env.store.settings.set(roster.MEMBERS_KEY, ["UMEMBER01"])
    got = roster.participants(env)
    assert {CAROL, BOB, "UDNS00001", "UMEMBER01"} <= got
    assert "UOTHER0001" not in got


def test_manual_edit_keeps_person_on_roster(env, client):
    client.post("/api/matrix/manual/cell", json={"sniper": "UEDITED01", "victim": BOB, "value": 2}, headers=H)
    assert "UEDITED01" in ids(client)


def test_compare_finds_overcount_and_missed_and_remedy_fixes_true(env, client):
    add(env, 1, "", kind=Kind.SNIPE)                         # bot: CAROL -> ALICE = 1
    add(env, 2, "", kind=Kind.SNIPE)                         # bot: CAROL -> ALICE = 2
    # human says CAROL->ALICE is 1, and ALICE->BOB is 3 (bot has none)
    client.post("/api/compare/human/cell", json={"sniper": CAROL, "victim": ALICE, "value": 1}, headers=H)
    client.post("/api/compare/human/cell", json={"sniper": ALICE, "victim": BOB, "value": 3}, headers=H)
    d = client.get("/api/compare").get_json()
    by = {(x["sniper"], x["victim"]): x for x in d["discrepancies"]}
    over, missed = by[(CAROL, ALICE)], by[(ALICE, BOB)]
    assert (over["bot"], over["human"], over["diff"], over["remedy"]) == (2, 1, -1, -1)
    assert len(over["suspects"]) == 2                        # the bot's own snipes for that pair
    assert (missed["bot"], missed["human"], missed["diff"]) == (0, 3, 3)
    assert d["summary"]["bot_missed"] == 3 and d["summary"]["bot_overcounted"] == 1

    client.post("/api/compare/remedy/cell", json={"sniper": CAROL, "victim": ALICE}, headers=H)
    assert env.store.scoring.matrix() == {CAROL: {ALICE: -1}}
    r = client.post("/api/compare/remedy/all", headers=H).get_json()
    assert r["ok"] and client.get("/api/matrix/true").get_json()["cells"] == {CAROL: {ALICE: 1}, ALICE: {BOB: 3}}
    again = client.get("/api/compare").get_json()
    assert all(x["resolved"] for x in again["discrepancies"] if x["diff"])


def test_human_adjustment_and_score_gap_and_remedy(env, client):
    add(env, 1, "", kind=Kind.SNIPE)
    client.post("/api/compare/human/cell", json={"sniper": CAROL, "victim": ALICE, "value": 1}, headers=H)
    client.post("/api/compare/human/adjustment", json={"user_id": CAROL, "adjustment": 2.5}, headers=H)
    p = {x["id"]: x for x in client.get("/api/compare").get_json()["persons"]}[CAROL]
    assert p["human"]["score"] == 3.5 and p["bot"]["score"] == 1 and p["gap"]["adjustment"] == 2.5
    client.post("/api/compare/remedy/adjustment", json={"user_id": CAROL}, headers=H)
    assert env.store.scoring.adjustments() == {CAROL: 2.5}


def test_import_matches_names_and_rejects_unknown(env, client):
    env.store.names.set_many({ALICE: "Alice", BOB: "Bob", CAROL: "Carol"})
    add(env, 1, "", kind=Kind.SNIPE)                         # puts Carol + Alice on the roster
    env.store.dns.add(BOB)
    ok = client.post("/api/compare/human/import", json={"text": "\tAlice\tBob\nCarol\t4\t1\n"}, headers=H)
    assert ok.status_code == 200
    assert env.store.human.matrix() == {CAROL: {ALICE: 4, BOB: 1}}
    bad = client.post("/api/compare/human/import", json={"text": "\tNobody\nCarol\t1\n"}, headers=H)
    assert bad.status_code == 400 and "Nobody" in bad.get_json()["error"]
    assert env.store.human.matrix() == {CAROL: {ALICE: 4, BOB: 1}}      # nothing saved on failure


def test_remedy_all_skips_do_not_snipe_victims(env, client):
    add(env, 1, "", kind=Kind.SNIPE)
    env.store.dns.add(ALICE)                                 # bot scores nothing for CAROL -> ALICE
    client.post("/api/compare/human/cell", json={"sniper": CAROL, "victim": ALICE, "value": 1}, headers=H)
    r = client.post("/api/compare/remedy/all", headers=H).get_json()
    assert r["dns_skipped"] == 1 and env.store.scoring.matrix() == {}
    d = client.get("/api/compare").get_json()["discrepancies"][0]
    assert d["dns"] is True
