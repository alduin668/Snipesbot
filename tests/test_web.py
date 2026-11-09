import io
import time

import pytest
from openpyxl import load_workbook

from snipesbot.models import Kind
from snipesbot.services import entries as svc
from snipesbot.web import create_app
from conftest import ALICE, BOB, CAROL, CHANNEL, photo_msg, text_msg

H = {"X-Requested-With": "snipesbot"}


@pytest.fixture
def client(env, no_download):
    app = create_app(env)
    app.testing = True
    return app.test_client()


def add(env, i, caption="", user=CAROL, kind=None):
    from snipesbot.services import archiver
    msg = photo_msg(f"{1788000000 + i * 100}.000100", user, caption)
    archiver.archive_message(env, msg)
    entry, _ = svc.record_photo(env, msg)
    if kind:
        entry = svc.apply_state(env, entry.id, kind, sniped=[ALICE] if kind is Kind.SNIPE else ())
    return entry


# ---- auth / CSRF -----------------------------------------------------------------------

def test_password_protects_everything_and_csrf_header_protects_writes(env, no_download):
    env.cfg = env.cfg.with_(webapp_password="s3cret")
    c = create_app(env).test_client()
    assert c.get("/api/status").status_code == 401
    assert c.get("/").status_code == 401
    import base64
    good = {"Authorization": "Basic " + base64.b64encode(b"anyone:s3cret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"anyone:wrong").decode()}
    assert c.get("/api/status", headers=bad).status_code == 401
    assert c.get("/api/status", headers=good).status_code == 200
    assert c.post("/api/dns", json={"user_id": ALICE}, headers=good).status_code == 403          # no CSRF header
    assert c.post("/api/dns", json={"user_id": ALICE}, headers={**good, **H}).status_code == 200


def test_without_a_password_reads_work_but_writes_still_need_the_header(client):
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/dns", json={"user_id": ALICE}).status_code == 403


def test_index_and_static_assets_are_served(client):
    page = client.get("/").get_data(as_text=True)
    assert 'type="module"' in page and "js/main.js" in page
    assert client.get("/static/js/main.js").status_code == 200


# ---- listing / filtering / paging ----------------------------------------------------------

def test_list_filters_and_server_side_paging(env, client):
    for i in range(7):
        add(env, i, f"<@{ALICE}> snipe note{i}", user=CAROL if i % 2 else BOB)
    add(env, 20, "who", kind=None)                                         # unidentified
    got = client.get("/api/entries?limit=3&offset=0").get_json()
    assert got["total"] == 8 and len(got["entries"]) == 3
    assert got["entries"][0]["caption"] == "who"                           # newest first
    assert client.get("/api/entries?pending=1").get_json()["total"] == 1
    assert client.get(f"/api/entries?sniper={CAROL}").get_json()["total"] == 4      # 3 odd-numbered + "who"
    assert client.get(f"/api/entries?sniped={ALICE}").get_json()["total"] == 7
    assert client.get("/api/entries?q=note3").get_json()["total"] == 1
    assert client.get("/api/entries?kind=snipe,snope").get_json()["total"] == 7
    assert client.get("/api/entries?kind=nonsense").status_code == 400
    assert client.get("/api/entries?limit=abc").status_code == 400


def test_review_queue_is_oldest_first_and_excludes_resolved(env, client):
    a = add(env, 1, "first")
    add(env, 2, "second")
    got = client.get("/api/entries?pending=1").get_json()["entries"]
    assert [e["caption"] for e in got] == ["first", "second"]
    svc.apply_state(env, a.id, Kind.NOT_A_SNIPE)
    assert [e["caption"] for e in client.get("/api/entries?pending=1").get_json()["entries"]] == ["second"]
    assert client.get("/api/entries?manual=1").get_json()["total"] == 1


def test_time_and_day_filters_use_local_time(env, client):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = f"{datetime(2026, 9, 2, 23, 30, tzinfo=ZoneInfo('America/New_York')).timestamp():.6f}"      # Wed 11:30pm NY
    svc.record_photo(env, photo_msg(ts, CAROL, "late"))
    assert client.get("/api/entries?day=Wednesday&time=night").get_json()["total"] == 1
    assert client.get("/api/entries?day=Thursday").get_json()["total"] == 0


def test_entry_view_shape_and_guessed_sniped(env, client):
    add(env, 1, f"hmm <@{ALICE}> maybe")
    e = client.get("/api/entries").get_json()["entries"][0]
    assert e["guessed_ids"] == [ALICE] and e["sniped_ids"] == [] and e["photo_url"].startswith("/api/files/F")
    assert e["permalink"].startswith("https://team.slack.com/archives/") and e["kind_label"] == "Unidentified"
    assert e["date"] and e["time"] and "T" in e["date_time"]


def test_slack_text_is_returned_raw_for_the_client_to_escape(env, client):
    """Escaping is the client's job (dom.js); the API must not pre-escape or double-escape."""
    add(env, 1, "<img src=x onerror=alert(1)> & \"quotes\"")
    assert client.get("/api/entries").get_json()["entries"][0]["caption"] == "<img src=x onerror=alert(1)> & \"quotes\""


# ---- changes ---------------------------------------------------------------------------------

def test_set_kind_validates_and_applies(env, client, slack):
    e = add(env, 1, "who")
    assert client.post(f"/api/entries/{e.id}/kind", json={"kind": "snipe", "sniped": []}, headers=H).status_code == 400
    assert client.post(f"/api/entries/{e.id}/kind", json={"kind": "bogus"}, headers=H).status_code == 400
    ok = client.post(f"/api/entries/{e.id}/kind", json={"kind": "snipe", "sniped": [ALICE, BOB]}, headers=H)
    body = ok.get_json()
    assert ok.status_code == 200 and body["kind"] == "snipe" and body["sniped_ids"] == [ALICE, BOB] and body["manually_resolved"]
    assert client.post("/api/entries/nope/kind", json={"kind": "snope"}, headers=H).status_code == 404


def test_notable_toggle(env, client):
    e = add(env, 1, "x")
    assert client.post(f"/api/entries/{e.id}/notable", json={"notable": True}, headers=H).get_json()["notable"] is True
    assert client.post(f"/api/entries/{e.id}/notable", json={"notable": False}, headers=H).get_json()["notable"] is False


def test_context_endpoint_widens_on_request_and_replies(env, client):
    base = add(env, 5, "the photo")
    for i in range(1, 5):
        env.store.archive.save_message(text_msg(f"{1788000000 + 500 - i}.000100", ALICE, f"before{i}"))
        env.store.archive.save_message(text_msg(f"{1788000000 + 500 + i}.000100", ALICE, f"after{i}"))
    env.store.archive.save_message(text_msg("1788000501.500000", BOB, "a reply", thread_ts=base.message_ts))
    ctx = client.get(f"/api/entries/{base.id}/context").get_json()
    assert [m["text"] for m in ctx["before"]] == ["before2", "before1"] and len(ctx["after"]) == 2
    wide = client.get(f"/api/entries/{base.id}/context?before=4&after=3").get_json()
    assert len(wide["before"]) == 4 and len(wide["after"]) == 3
    assert wide["before"][0]["user"] == "Alice"
    replies = client.get(f"/api/entries/{base.id}/replies").get_json()["replies"]
    assert [r["text"] for r in replies] == ["a reply"]


def test_deleted_entries_disappear_from_the_api(env, client):
    e = add(env, 1, "x")
    svc.handle_deleted(env, CHANNEL, e.message_ts)
    assert client.get(f"/api/entries/{e.id}").status_code == 404
    assert client.get("/api/entries").get_json()["total"] == 0


# ---- files ---------------------------------------------------------------------------------------

def test_files_are_served_from_the_archive_and_unknown_ids_are_refused(env, client):
    e = add(env, 1, "x")
    r = client.get(e.file and f"/api/files/{e.file.id}")
    assert r.status_code == 200 and r.data.startswith(b"bytes-") and r.mimetype == "image/jpeg"
    assert client.get("/api/files/FNOTOURS").status_code == 404           # not something we archived
    assert client.get("/api/files/..%2Fsecrets").status_code == 404


# ---- special tab -------------------------------------------------------------------------------------

def test_special_tab_flow(env, client):
    e = add(env, 1, "deer?", kind=Kind.SPECIAL)
    listed = client.get("/api/special").get_json()["entries"]
    assert [x["id"] for x in listed] == [e.id] and listed[0]["derived_entries"] == []
    bad = client.post(f"/api/special/{e.id}/create", json={"kind": "snipe", "sniper": "deer", "sniped": []}, headers=H)
    assert bad.status_code == 400
    ok = client.post(f"/api/special/{e.id}/create", json={"kind": "snipe", "sniper": "deer", "sniped": [ALICE]}, headers=H)
    assert ok.status_code == 200 and ok.get_json()["sniper"] == "deer"
    assert len(client.get("/api/special").get_json()["entries"][0]["derived_entries"]) == 1
    assert client.post(f"/api/special/{e.id}/dismiss", headers=H).get_json()["kind"] == "not_a_snipe"
    assert client.get("/api/special").get_json()["entries"] == []


# ---- people / dns -------------------------------------------------------------------------------------

def test_users_and_dns(env, client):
    env.store.names.set(ALICE, "Alice")
    users = client.get("/api/users").get_json()["users"]
    assert users == [{"id": ALICE, "name": "Alice", "display": "Alice"}]
    client.post("/api/dns", json={"user_id": BOB}, headers=H)
    assert [u["id"] for u in client.get("/api/dns").get_json()["users"]] == [BOB]
    assert client.post("/api/dns", json={}, headers=H).status_code == 400
    client.delete(f"/api/dns/{BOB}", headers=H)
    assert client.get("/api/dns").get_json()["users"] == []


# ---- scoring ---------------------------------------------------------------------------------------------

def test_matrix_views_cells_and_true_cell_stores_only_the_difference(env, client):
    add(env, 1, "", kind=Kind.SNIPE)                                       # CAROL -> ALICE, coded
    coded = client.get("/api/matrix/coded").get_json()
    assert coded["editable"] is False and coded["cells"] == {CAROL: {ALICE: 1}}
    client.post("/api/matrix/true/cell", json={"sniper": CAROL, "victim": ALICE, "value": 4}, headers=H)
    assert env.store.scoring.matrix() == {CAROL: {ALICE: 3}}               # 4 true = 1 coded + 3 manual
    assert client.get("/api/matrix/true").get_json()["cells"] == {CAROL: {ALICE: 4}}
    assert client.get("/api/matrix/coded").get_json()["cells"] == {CAROL: {ALICE: 1}}   # coded stays pure
    client.post("/api/matrix/manual/cell", json={"sniper": BOB, "victim": ALICE, "value": 2}, headers=H)
    assert client.get("/api/matrix/manual").get_json()["row_totals"][BOB] == 2
    assert client.post("/api/matrix/manual/cell", json={"sniper": BOB, "victim": BOB, "value": 1}, headers=H).status_code == 400
    assert client.post("/api/matrix/manual/cell", json={"sniper": BOB, "victim": ALICE, "value": "x"}, headers=H).status_code == 400
    assert client.get("/api/matrix/bogus").status_code == 404


def test_adjustments_and_analysis(env, client):
    add(env, 1, "", kind=Kind.SNIPE)
    client.post("/api/adjustments", json={"user_id": CAROL, "adjustment": 2.5}, headers=H)
    assert client.post("/api/adjustments", json={"user_id": CAROL, "adjustment": "x"}, headers=H).status_code == 400
    adj = {u["id"]: u["adjustment"] for u in client.get("/api/adjustments").get_json()["users"]}
    assert adj[CAROL] == 2.5
    a = client.get("/api/analysis").get_json()
    assert a["leaderboard"][0]["score"] == 3.5 and a["queue_size"] == 0 and "peak_by_bucket" in a


# ---- log / export / maintenance --------------------------------------------------------------------------------

def test_log_status_and_xlsx_export(env, client):
    add(env, 1, f"<@{ALICE}> snipe")
    add(env, 2, "other")
    log = client.get("/api/log").get_json()
    assert log["total"] == 2 and log["rows"][0]["date"]
    assert client.get("/api/status").get_json()["entries"] == 2
    r = client.get("/api/export.xlsx")
    assert r.mimetype.endswith("spreadsheetml.sheet")
    ws = load_workbook(io.BytesIO(r.data)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:4] == ("Date", "Time", "Sniper", "Sniped") and rows[0][7] == "Scores" and len(rows) == 3
    assert any(r[6] == "snipe" and r[7] == "Yes" and r[8].startswith("https://team.slack.com/archives/") for r in rows[1:])


def wait_done(client, run_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/maintenance/runs/{run_id}").get_json()
        if s["done"]:
            return s
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_maintenance_registry_and_running_a_task(env, client, slack):
    tasks = client.get("/api/maintenance/tasks").get_json()
    assert set(tasks) == {"sync", "refresh_names", "recover_manuals", "diagnose", "selftest", "selftest_clear"}
    assert tasks["selftest"]["dangerous"] and [p["name"] for p in tasks["sync"]["params"]] == ["since", "full", "repair_reactions"]
    run = client.post("/api/maintenance/diagnose/run", json={}, headers=H).get_json()["run_id"]
    done = wait_done(client, run)
    assert done["ok"] and "auth.test OK" in done["output"]
    assert client.post("/api/maintenance/nope/run", json={}, headers=H).status_code == 404
    assert client.get("/api/maintenance/runs/nope").status_code == 404


def test_sync_task_from_the_webapp_reports_busy_when_another_process_holds_the_lock(env, client, cfg):
    from snipesbot.store import Store
    other = Store.open(cfg)
    other.locks.acquire("sync", 60)
    run = client.post("/api/maintenance/sync/run", json={}, headers=H).get_json()["run_id"]
    done = wait_done(client, run)
    assert done["ok"] is False and "already running" in done["output"]


def test_only_one_task_runs_at_a_time(env, client, slack, monkeypatch):
    import threading
    from snipesbot.services import maintenance
    gate = threading.Event()
    slow = maintenance.Task("slow", "Slow", "x", lambda e, p, progress: gate.wait(3))
    client.application.config["RUNNER"].tasks = {**maintenance.TASKS, "slow": slow}
    first = client.post("/api/maintenance/slow/run", json={}, headers=H)
    assert first.status_code == 200
    assert client.post("/api/maintenance/diagnose/run", json={}, headers=H).status_code == 409
    gate.set()
    wait_done(client, first.get_json()["run_id"])


# ---- activity: the UI can always tell what is running -----------------------------------------------------------

def test_status_reports_a_running_task_with_elapsed_time_and_then_clears(env, client, slack):
    import threading
    from snipesbot.services import maintenance
    gate = threading.Event()
    slow = maintenance.Task("slow", "Slow thing", "x", lambda e, p, progress: (progress("step one"), gate.wait(3)))
    client.application.config["RUNNER"].tasks = {**maintenance.TASKS, "slow": slow}
    assert client.get("/api/status").get_json()["activity"] == {"running": None, "external_sync": False}
    run_id = client.post("/api/maintenance/slow/run", json={}, headers=H).get_json()["run_id"]
    time.sleep(0.2)
    for url in ("/api/status", "/api/maintenance/status"):
        act = client.get(url).get_json()
        act = act.get("activity", act)
        running = act["running"]
        assert running["label"] == "Slow thing" and running["id"] == run_id and not running["done"]
        assert running["elapsed"] >= 0.1 and running["last_line"] == "step one"
    gate.set()
    done = wait_done(client, run_id)
    assert done["done"] and done["ok"] and done["label"] == "Slow thing" and done["elapsed"] > 0
    assert client.get("/api/status").get_json()["activity"]["running"] is None


def test_a_sync_running_in_another_process_is_visible_but_not_ours(env, client, cfg):
    from snipesbot.store import Store
    other = Store.open(cfg)                                            # e.g. the bot's scheduled pass
    other.locks.acquire("sync", 60)
    act = client.get("/api/maintenance/status").get_json()
    assert act == {"running": None, "external_sync": True}
    other.locks.release("sync")
    assert client.get("/api/maintenance/status").get_json()["external_sync"] is False
    env.store.locks.acquire("sync", 60)                                # our own lock is not "external"
    assert client.get("/api/maintenance/status").get_json()["external_sync"] is False
