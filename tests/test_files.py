import json

import pytest

from snipesbot.services import entries as svc, files, legacy, maintenance
from conftest import CAROL, CHANNEL, photo_msg
from test_legacy import legacy_entry


def record(env, i=1, caption="x"):
    return svc.record_photo(env, photo_msg(f"{1788000000 + i * 100}.000100", CAROL, caption))[0]


# ---- health ------------------------------------------------------------------------------

def test_file_health_counts_found_and_missing(env):
    a, b = record(env, 1), record(env, 2)
    env.store.archive.save_file(a.file.id, "a.jpg", "image/jpeg", b"x")
    h = files.file_health(env)
    assert (h.entries_with_files, h.available, h.missing_count) == (2, 1, 1)
    assert h.missing[0][1].id == b.file.id and h.files_on_disk == 1 and h.files_registered == 1


def test_deleted_entries_are_not_counted_as_missing(env):
    e = record(env)
    svc.handle_deleted(env, CHANNEL, e.message_ts)
    assert files.file_health(env).entries_with_files == 0


# ---- relink ------------------------------------------------------------------------------

def test_relink_finds_files_by_slack_file_id_or_entry_id_anywhere_under_a_folder(env, tmp_path):
    a, b, c = record(env, 1), record(env, 2), record(env, 3)
    loose = tmp_path / "somewhere" / "deep"
    loose.mkdir(parents=True)
    (loose / f"{a.file.id}.jpg").write_bytes(b"by-file-id")            # archive-style name
    (loose / f"{b.id}.jpeg").write_bytes(b"by-entry-id")               # old photos/-style name
    (loose / "unrelated.txt").write_text("ignore me")
    report = files.relink_files(env, tmp_path, progress=lambda _l: None)
    assert (report.linked, report.still_missing) == (2, 1)
    assert env.store.archive.file_path(a.file.id).read_bytes() == b"by-file-id"
    assert env.store.archive.file_path(b.file.id).read_bytes() == b"by-entry-id"
    assert not env.store.archive.has_file(c.file.id)
    # running it again is harmless
    assert files.relink_files(env, tmp_path, progress=lambda _l: None).linked == 0


def test_relink_rejects_a_path_that_is_not_a_folder(env, tmp_path):
    with pytest.raises(FileNotFoundError):
        files.relink_files(env, tmp_path / "nope")


def test_relinked_photos_are_served_by_the_webapp(env, tmp_path):
    from snipesbot.web import create_app
    e = record(env)
    (tmp_path / f"{e.file.id}.jpg").write_bytes(b"found-it")
    client = create_app(env).test_client()
    assert client.get(f"/api/files/{e.file.id}").status_code == 404        # nothing is linked yet
    files.relink_files(env, tmp_path, progress=lambda _l: None)
    assert client.get(f"/api/files/{e.file.id}").data == b"found-it"


# ---- the importer must not fail silently ------------------------------------------------------

def test_import_reports_files_it_could_not_find(env, tmp_path):
    d = tmp_path / "old"
    (d / "archive").mkdir(parents=True)
    (d / "snipes.json").write_text(json.dumps([legacy_entry(1), legacy_entry(2)]))
    (d / "archive" / "files_index.json").write_text(json.dumps(
        {"F1": {"path": "/opt/slackbot/snipesbot/data/archive/files/F1.jpg", "name": "p.jpg", "mimetype": "image/jpeg"}}))
    lines = []
    report = legacy.import_legacy(env, d, progress=lines.append)
    assert report.missing_photos == 2 and report.missing_files == ["/opt/slackbot/snipesbot/data/archive/files/F1.jpg"]
    text = "\n".join(lines)
    assert "NOT FOUND" in text and "WARNING" in text and "relink-files" in text
    assert "/opt/slackbot/snipesbot/data/archive/files/F1.jpg" in text            # shows what it was looking for


def test_import_is_quiet_when_every_file_was_found(env, tmp_path):
    d = tmp_path / "old"
    (d / "photos").mkdir(parents=True)
    (d / "photos" / "id1.jpg").write_bytes(b"x")
    (d / "snipes.json").write_text(json.dumps([legacy_entry(1)]))
    lines = []
    legacy.import_legacy(env, d, progress=lines.append)
    assert "NOT FOUND" not in "\n".join(lines) and "WARNING" not in "\n".join(lines)


# ---- diagnose works with no Slack at all -------------------------------------------------------------

def test_diagnose_reports_locations_and_photo_health_even_when_slack_is_down(env, slack):
    record(env, 1)
    slack.fail = {"auth_test"}
    out = []
    maintenance.diagnose(env, {}, out.append)
    text = "\n".join(out)
    assert str(env.cfg.data_dir.resolve()) in text and "snipesbot.db" in text
    assert "photos         : 0 of 1 found" in text and "relink-files" in text
    assert "auth.test FAILED" in text


def test_diagnose_is_clean_when_everything_is_in_place(env, slack):
    e = record(env, 1)
    env.store.archive.save_file(e.file.id, "a.jpg", "image/jpeg", b"x")
    out = []
    maintenance.diagnose(env, {}, out.append)
    text = "\n".join(out)
    assert "1 of 1 found" in text and "missing" not in text and "auth.test OK" in text


def test_the_cli_relinks_files(env, tmp_path, monkeypatch, capsys):
    from snipesbot import __main__ as cli
    e = record(env)
    (tmp_path / "loose").mkdir()
    (tmp_path / "loose" / f"{e.file.id}.png").write_bytes(b"x")
    monkeypatch.setattr(cli, "_env", lambda: env)
    assert cli.main(["relink-files", str(tmp_path)]) == 0
    assert env.store.archive.has_file(e.file.id)
    assert cli.main(["relink-files", str(tmp_path / "missing-dir")]) == 1
