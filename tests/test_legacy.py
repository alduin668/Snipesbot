import json

import pytest

from snipesbot.models import Kind
from snipesbot.services import legacy
from conftest import ALICE, BOB, CAROL, CHANNEL, ZEKE


def legacy_entry(i, kind="snipe", **kw):
    ts = f"{1788000000 + i}.000100"
    base = {"id": f"id{i}", "kind": kind, "status": kind, "sniper": CAROL, "sniped": [ALICE], "group_snipe": False,
            "notable": False, "star_users": [], "ignored": False, "photo_path": f"data/photos/id{i}.jpg",
            "channel": CHANNEL, "message_ts": ts, "photo_text": f"<@{ALICE}> snipe",
            "context_before": [{"user": "x"}], "context_after": [], "context_ready": True,
            "file": {"id": f"F{i}", "name": f"p{i}.jpg", "mimetype": "image/jpeg", "url_private": "https://stale"},
            "extra_files": [{"id": f"G{i}", "name": "v.mp4", "is_video": True}], "manually_resolved": False,
            "derived_from": None, "date_time": "2026-08-29T10:00:00", "day_of_week": "Saturday"}
    base.update(kw)
    return base


@pytest.fixture
def old_dir(tmp_path):
    d = tmp_path / "old"
    (d / "photos").mkdir(parents=True)
    (d / "archive" / "files").mkdir(parents=True)
    (d / "photos" / "id1.jpg").write_bytes(b"photo-only-copy")             # never made it into the archive
    (d / "archive" / "files" / "F2.jpg").write_bytes(b"archived-copy")
    entries = [
        legacy_entry(1),
        legacy_entry(2, kind="not_a_snipe", sniped=[]),
        legacy_entry(3, kind="snipe", sniped=[]),                           # malformed snipe
        legacy_entry(4, kind="likely_snipe", ignored=True),                 # legacy fingerprint "ignored"
        legacy_entry(5, notable=True, star_users=[ALICE]),                  # notable set by hand
        legacy_entry(6, kind="special", manually_resolved=True, sniped=[]),
        legacy_entry(7, kind="snipe", derived_from="id6", sniper="deer", sniped=["deer"]),
    ]
    (d / "snipes.json").write_text(json.dumps(entries))
    (d / "do_not_snipe.json").write_text(json.dumps([ALICE]))
    (d / "dns_warnings.json").write_text(json.dumps([{"channel": CHANNEL, "snipe_ts": "1.1", "target": ALICE,
                                                      "sniper": CAROL, "warning_channel": CHANNEL, "warning_ts": "2.2"}]))
    (d / "settings.json").write_text(json.dumps({"dm_review_enabled": True, "last_catchup_ts": 1788000999.0}))
    (d / "adjustments.json").write_text(json.dumps({ALICE: 1.5}))
    (d / "manual_matrix.json").write_text(json.dumps({ALICE: {BOB: 2}}))
    (d / "user_names.json").write_text(json.dumps({ALICE: "Alice"}))
    (d / "archive" / "messages.json").write_text(json.dumps({
        "1788000001.000100": {"ts": "1788000001.000100", "user": CAROL, "text": "hello", "channel": CHANNEL}}))
    (d / "archive" / "files_index.json").write_text(json.dumps({
        "F2": {"path": "data/archive/files/F2.jpg", "name": "p2.jpg", "mimetype": "image/jpeg"}}))
    return d


def test_import_converts_entries_and_fixes_the_known_data_problems(env, old_dir):
    report = legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    get = env.store.entries.get
    assert report.entries == 7 and report.demoted_malformed == 1 and report.converted_ignored == 1

    assert get("id1").kind is Kind.SNIPE and get("id1").sniped == (ALICE,) and get("id1").reacted_kind is Kind.SNIPE
    assert get("id2").manually_resolved                                      # NAS was always a human call
    assert (get("id3").kind, get("id3").manually_resolved) == (Kind.UNIDENTIFIED, True)
    assert (get("id4").kind, get("id4").sniped, get("id4").manually_resolved) == (Kind.NOT_A_SNIPE, (), True)
    assert get("id5").notable and get("id5").notable_override is True       # a hand-set flag is kept
    assert get("id7").derived_from == "id6" and get("id7").sniper == "deer" and get("id7").reacted_kind is None
    assert all(e.source == "historical" for e in env.store.entries.find())  # so no stale review DMs


def test_stale_slack_urls_and_cached_context_are_dropped(env, old_dir):
    legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    e = env.store.entries.get("id1")
    assert e.file.to_dict() == {"id": "F1", "name": "p1.jpg", "mimetype": "image/jpeg"}
    assert e.extra_files[0].is_video


def test_photos_are_adopted_into_the_archive_so_nothing_is_lost(env, old_dir):
    legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    assert env.store.archive.file_path("F1").read_bytes() == b"photo-only-copy"      # from photos/
    assert env.store.archive.file_path("F2").read_bytes() == b"archived-copy"        # from the archive
    assert env.store.archive.get_message(CHANNEL, "1788000001.000100")["text"] == "hello"


def test_the_small_stores_come_across(env, old_dir):
    legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    s = env.store
    assert s.dns.list() == [ALICE] and s.names.get(ALICE) == "Alice"
    assert s.scoring.adjustments() == {ALICE: 1.5} and s.scoring.matrix() == {ALICE: {BOB: 2}}
    assert s.settings.get("dm_review_enabled") is True and s.settings.get("last_sync_ts") == 1788000999.0
    assert not s.dns.reserve_warning(CHANNEL, "1.1", ALICE, CAROL)                    # warning carried over


def test_import_is_idempotent_and_leaves_the_old_files_alone(env, old_dir):
    before = (old_dir / "snipes.json").read_text()
    legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    again = legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    assert again.entries == 0 and again.skipped == 7 and env.store.entries.count() == 7
    assert (old_dir / "snipes.json").read_text() == before


def test_importing_in_place_keeps_archive_files_where_they_are(env, cfg, slack, tmp_path):
    files = cfg.archive_files_dir
    files.mkdir(parents=True)
    (files / "F9.jpg").write_bytes(b"in-place")
    (cfg.data_dir / "archive" / "files_index.json").write_text(json.dumps(
        {"F9": {"path": str(files / "F9.jpg"), "name": "n.jpg", "mimetype": "image/jpeg"}}))
    legacy.import_legacy(env, cfg.data_dir, progress=lambda _l: None)
    assert env.store.archive.file_path("F9") == files / "F9.jpg" and sorted(p.name for p in files.iterdir()) == ["F9.jpg"]


def test_recover_manual_decisions_from_zekes_reactions(env, slack, old_dir):
    legacy.import_legacy(env, old_dir, progress=lambda _l: None)
    slack.reactions[(CHANNEL, "1788000001.000100")] = [("large_purple_circle", ZEKE)]     # id1: Zeke chose snope
    slack.reactions[(CHANNEL, "1788000005.000100")] = [("large_green_circle", "UOTHER0001")]  # id5: not Zeke -> untouched
    assert legacy.recover_manual_decisions(env, progress=lambda _l: None) == 1
    assert env.store.entries.get("id1").kind is Kind.SNOPE and env.store.entries.get("id1").manually_resolved
    assert not env.store.entries.get("id5").manually_resolved
