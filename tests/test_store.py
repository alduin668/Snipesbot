import sqlite3
import threading

import pytest

from snipesbot.db import Database
from snipesbot.models import Entry, FileRef, Kind, new_id
from snipesbot.store import Busy, Store
from conftest import CHANNEL, photo_msg, text_msg


def make_entry(ts="1788000000.000100", **kw):
    defaults = dict(id=new_id(), kind=Kind.SNIPE, sniper="UALICE001", sniped=("UBOB00001",), channel=CHANNEL,
                    message_ts=ts, caption="hi", file=FileRef("F1", "a.jpg", "image/jpeg"), created_at=1.0)
    defaults.update(kw)
    return Entry(**defaults)


def test_entry_round_trip(env):
    e = env.store.entries.insert(make_entry(extra_files=(FileRef("F2", "b.mp4", "video/mp4"),),
                                            star_users=("UZEKE0001",), notable_override=True))
    got = env.store.entries.get(e.id)
    assert got == e and got.extra_files[0].is_video and got.notable is True


def test_one_entry_per_message_but_derived_entries_may_share_it(env):
    s = env.store.entries
    parent = s.insert(make_entry(kind=Kind.SPECIAL, sniped=()))
    with pytest.raises(sqlite3.IntegrityError):
        s.insert(make_entry(kind=Kind.SPECIAL))
    s.insert(make_entry(derived_from=parent.id))
    s.insert(make_entry(derived_from=parent.id))
    assert s.get_by_message(CHANNEL, parent.message_ts).id == parent.id
    assert len(s.derived(parent.id)) == 2


def test_update_rejects_unknown_columns(env):
    e = env.store.entries.insert(make_entry())
    with pytest.raises(KeyError):
        env.store.entries.update(e.id, id="x")


def test_find_filters_and_order(env):
    s = env.store.entries
    s.insert(make_entry("1788000001.000100", kind=Kind.SNOPE, sniped=(), caption="100% legit_"))
    s.insert(make_entry("1788000002.000100", kind=Kind.UNIDENTIFIED, sniped=(), manually_resolved=True))
    s.insert(make_entry("1788000003.000100"))
    assert [e.message_ts for e in s.find()] == ["1788000003.000100", "1788000002.000100", "1788000001.000100"]
    assert [e.kind for e in s.find(kinds=[Kind.SNOPE])] == [Kind.SNOPE]
    assert len(s.find(manual=True)) == 1
    assert len(s.pending()) == 1
    assert len(s.find(text="100% legit_")) == 1      # LIKE wildcards are escaped
    assert len(s.find(text="%")) == 1
    assert [e.message_ts for e in s.find(limit=1, offset=1)] == ["1788000002.000100"]


def test_soft_deleted_entries_are_hidden_but_still_block_rediscovery(env):
    s = env.store.entries
    e = s.insert(make_entry())
    s.update(e.id, deleted_at=5.0)
    assert s.find() == [] and s.count() == 0
    assert s.get_by_message(CHANNEL, e.message_ts) is not None


def test_transaction_rolls_back_on_error(env):
    db = env.store.db
    with pytest.raises(RuntimeError):
        with db.tx():
            env.store.settings.set("a", 1)
            raise RuntimeError("boom")
    assert env.store.settings.get("a") is None


def test_nested_transactions_join_the_outer_one(env):
    with pytest.raises(RuntimeError):
        with env.store.db.tx():
            with env.store.db.tx():
                env.store.settings.set("a", 1)
            raise RuntimeError
    assert env.store.settings.get("a") is None


def test_concurrent_read_modify_write_loses_nothing(cfg):
    """The old JSON files could silently drop updates; SQLite transactions can't."""
    store = Store.open(cfg)
    store.settings.set("n", 0)

    def bump():
        s = Store.open(cfg)                       # its own connection, like another process
        for _ in range(25):
            with s.db.tx():
                s.settings.set("n", s.settings.get("n") + 1)
        s.db.close()

    threads = [threading.Thread(target=bump) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert store.settings.get("n") == 150


def test_schema_version_guard(tmp_path):
    p = tmp_path / "x.db"
    Database(p).close()
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(p)


# ---- archive -------------------------------------------------------------------

def test_context_is_a_range_query_that_skips_replies_bots_and_deleted(env):
    a = env.store.archive
    msgs = [text_msg(f"17880000{i:02d}.000100", "UALICE001", f"m{i}") for i in range(10)]
    msgs[3]["thread_ts"] = "1788000000.000100"           # a thread reply
    msgs[4]["subtype"] = "channel_join"
    a.save_messages(msgs)
    a.mark_deleted(CHANNEL, msgs[5]["ts"])
    before = [m["text"] for m in a.before(CHANNEL, msgs[8]["ts"], 3)]
    assert before == ["m2", "m6", "m7"]                  # 3,4,5 excluded; oldest first
    assert [m["text"] for m in a.after(CHANNEL, msgs[1]["ts"], 2)] == ["m2", "m6"]


def test_replies_are_grouped_by_thread(env):
    a = env.store.archive
    parent = text_msg("1788000000.000100", "UALICE001", "p")
    a.save_messages([parent, text_msg("1788000001.000100", "UBOB00001", "r1", thread_ts=parent["ts"]),
                     text_msg("1788000002.000100", "UBOB00001", "other")])
    assert [m["text"] for m in a.replies(CHANNEL, parent["ts"])] == ["r1"]


def test_resaving_a_message_picks_up_edits_and_clears_deleted(env):
    a = env.store.archive
    a.save_message(text_msg("1788000000.000100", "UALICE001", "old"))
    a.mark_deleted(CHANNEL, "1788000000.000100")
    a.save_message(text_msg("1788000000.000100", "UALICE001", "new"))
    assert a.get_message(CHANNEL, "1788000000.000100")["text"] == "new"
    assert len(a.after(CHANNEL, "1787999999.000100", 5)) == 1


def test_files_are_stored_once_and_guarded(env):
    a = env.store.archive
    p = a.save_file("F123", "pic.png", "image/png", b"data")
    assert p.read_bytes() == b"data" and p.suffix == ".png"
    assert a.save_file("F123", "pic.png", "image/png", b"other") == p      # idempotent
    assert a.knows_file("F123") and not a.knows_file("F999") and not a.knows_file("../etc/passwd")
    a.save_message(photo_msg("1788000000.000100", "UALICE001", file_id="F777"))
    assert a.knows_file("F777")                                            # referenced by a message
    a.delete_file("F123")
    assert not a.has_file("F123")


def test_media_messages_only_returns_media(env):
    a = env.store.archive
    a.save_messages([photo_msg("1788000000.000100", "UALICE001"), text_msg("1788000001.000100", "UALICE001", "hi")])
    assert [m["ts"] for m in a.media_messages(CHANNEL)] == ["1788000000.000100"]


# ---- locks / dns -----------------------------------------------------------------

def test_lock_is_exclusive_across_owners_and_expires(cfg):
    one, two = Store.open(cfg), Store.open(cfg)
    assert one.locks.acquire("sync", 60)
    assert not two.locks.acquire("sync", 60)
    with pytest.raises(Busy):
        with two.locks.held("sync"):
            pass
    one.locks.release("sync")
    assert two.locks.acquire("sync", 60)
    two.locks.release("sync")
    assert one.locks.acquire("sync", -1)         # already expired...
    assert two.locks.acquire("sync", 60)         # ...so another owner can take it


def test_a_warning_can_only_be_reserved_once(env):
    d = env.store.dns
    assert d.reserve_warning(CHANNEL, "1.1", "UBOB00001", "UALICE001")
    assert not d.reserve_warning(CHANNEL, "1.1", "UBOB00001", "UALICE001")
    d.release_warning(CHANNEL, "1.1", "UBOB00001")
    assert d.reserve_warning(CHANNEL, "1.1", "UBOB00001", "UALICE001")


def test_zero_adjustments_and_cells_are_not_stored(env):
    sc = env.store.scoring
    sc.set_adjustment("U1", 2.5)
    sc.set_adjustment("U1", 0)
    sc.set_cell("U1", "U2", 3)
    sc.set_cell("U1", "U2", 0)
    assert sc.adjustments() == {} and sc.matrix() == {}


def _bump_in_another_process(path, n):
    from snipesbot.store import Store
    store = Store(Database(path), path + ".files")
    for _ in range(n):
        with store.db.tx():
            store.settings.set("n", store.settings.get("n") + 1)


def test_separate_processes_can_write_at_once_without_losing_updates(tmp_path):
    """The bot and the webapp are different OS processes sharing one database."""
    import multiprocessing as mp
    path = str(tmp_path / "shared.db")
    store = Store(Database(path), path + ".files")
    store.settings.set("n", 0)
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_bump_in_another_process, args=(path, 30)) for _ in range(4)]
    [p.start() for p in procs]
    [p.join(60) for p in procs]
    assert all(p.exitcode == 0 for p in procs)
    assert store.settings.get("n") == 120
