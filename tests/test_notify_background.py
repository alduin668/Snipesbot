from snipesbot.models import Kind
from snipesbot.services import background, notify
from snipesbot.services import entries as svc
from conftest import ALICE, CAROL, ZEKE, photo_msg, text_msg

import time


def fresh(offset=0.0):
    """A timestamp 'just now' -- review DMs ignore photos older than an hour."""
    return f"{time.time() + offset:.6f}"


def live_unidentified(env, ts=None):
    ts = ts or fresh(-5)
    entry, _ = svc.record_photo(env, photo_msg(ts, CAROL, "who is this"), source="live")
    return entry


def dms(slack):
    return [p for p in slack.posted if p["channel"] == ZEKE and p.get("blocks")]


def test_nothing_is_sent_while_review_mode_is_off(env, slack):
    e = live_unidentified(env)
    assert notify.run_once(env, e.created_at + 999) == 0 and dms(slack) == []


def test_dm_waits_for_the_delay_or_enough_later_messages(env, slack):
    notify.set_enabled(env, True)
    e = live_unidentified(env)
    assert notify.run_once(env, e.created_at + 10) == 0                       # too soon
    assert notify.run_once(env, e.created_at + 100) == 1                      # delay elapsed
    assert notify.run_once(env, e.created_at + 200) == 0                      # never twice
    assert len(dms(slack)) == 1


def test_dm_goes_out_early_once_enough_messages_followed(env, slack):
    notify.set_enabled(env, True)
    e = live_unidentified(env)
    base = float(e.message_ts)
    env.store.archive.save_messages([text_msg(f"{base + 1:.6f}", ALICE, "a"), text_msg(f"{base + 2:.6f}", ALICE, "b")])
    assert notify.run_once(env, e.created_at + 5) == 1


def test_a_restart_does_not_lose_or_repeat_pending_notifications(env, slack, cfg):
    """The old version kept per-photo timers in memory; a restart dropped them all."""
    from snipesbot.env import Env
    notify.set_enabled(env, True)
    e = live_unidentified(env)
    restarted = Env.create(cfg, slack=slack)                                   # same database, fresh process
    assert notify.run_once(restarted, e.created_at + 100) == 1
    assert notify.run_once(restarted, e.created_at + 200) == 0


def test_historical_resolved_and_stale_photos_never_trigger_a_dm(env, slack):
    notify.set_enabled(env, True)
    svc.record_photo(env, photo_msg(fresh(-30), CAROL, "x"), source="historical")
    resolved = live_unidentified(env, fresh(-20))
    svc.apply_state(env, resolved.id, Kind.NOT_A_SNIPE)
    old = live_unidentified(env, fresh(-10))
    far_future = old.created_at + 10 * 3600                                     # older than the max age
    assert notify.run_once(env, far_future) == 0


def test_review_card_shows_context_photo_and_guess(env, slack):
    from snipesbot.services import review_cards
    base = time.time()
    env.store.archive.save_messages([text_msg(f"{base - 10:.6f}", ALICE, "before"), text_msg(f"{base + 10:.6f}", ALICE, f"after <@{ALICE}>")])
    e = live_unidentified(env)
    blocks = review_cards.build_blocks(env, e)
    text = str(blocks)
    assert "before" in text and "after" in text and any(b["type"] == "image" for b in blocks)
    assert "https://team.slack.com/archives/" in text
    assert {a["action_id"] for a in blocks[-2]["elements"]} == {"review_mark_snipe", "review_mark_snope", "review_mark_nas", "review_skip"}


# ---- background loop -----------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_loop(env, ticks, monkeypatch):
    calls = []
    monkeypatch.setattr(background, "_sync_quietly", lambda e: calls.append("sync"))
    clock = Clock()
    background.run_forever(env, sleep=clock.sleep, clock=clock.now, max_ticks=ticks)
    return calls


def test_loop_syncs_at_startup_and_then_on_the_interval(env, monkeypatch):
    interval = env.cfg.catchup_interval_seconds
    assert run_loop(env, 3, monkeypatch) == ["sync"]
    assert len(run_loop(env, interval // 15 + 5, monkeypatch)) == 2


def test_loop_syncs_immediately_after_reconnecting(env, slack, monkeypatch):
    seq = iter([True, False, False, True, True, True])
    monkeypatch.setattr(background, "_is_online", lambda e: next(seq, True))
    env.cfg = env.cfg.with_(connectivity_interval_seconds=15)
    calls = run_loop(env, 6, monkeypatch)
    assert calls == ["sync", "sync"]
    assert any("Lost connection" in p["text"] for p in slack.posted)
