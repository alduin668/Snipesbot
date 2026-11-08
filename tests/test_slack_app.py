import json
import time

import pytest

from snipesbot.models import Kind
from snipesbot.services import entries as svc, notify
from snipesbot.slack_app import build_app, events, review_ui
from conftest import ALICE, BOB, BOT, CAROL, CHANNEL, ZEKE, FakeSlack, photo_msg, text_msg

TS = "1788000000.000100"


@pytest.fixture(autouse=True)
def _no_download(no_download):
    pass


def ev(msg, **kw):
    return {**msg, **kw}


class Ephemeral(FakeSlack):
    def __init__(self):
        super().__init__()
        self.ephemeral = []

    def chat_postEphemeral(self, **kw):
        self.ephemeral.append(kw)
        return {}


@pytest.fixture
def eslack(env):
    env.slack = Ephemeral()
    return env.slack



# ---- message events ------------------------------------------------------------------------

def test_a_new_photo_is_archived_and_recorded(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, f"<@{ALICE}> snipe"))
    e = env.store.entries.get_by_message(CHANNEL, TS)
    assert e.kind is Kind.SNIPE and e.source == "live"
    assert env.store.archive.has_file(e.file.id) and env.store.archive.get_message(CHANNEL, TS)


def test_plain_chatter_is_archived_but_creates_no_entry(env):
    events.handle_message_event(env, text_msg(TS, ALICE, "lol"))
    assert env.store.archive.get_message(CHANNEL, TS) and env.store.entries.count() == 0


def test_bot_messages_and_other_channels_are_ignored(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, "x", bot_id="B1"))
    events.handle_message_event(env, {**photo_msg(TS, CAROL, "x"), "channel": "COTHER0001"})
    assert env.store.entries.count() == 0 and env.store.archive.count() == 0


def test_zekes_thread_reply_resolves_and_is_never_a_new_photo(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, "who"))
    reply = photo_msg("1788000001.000100", ZEKE, f"#snipe <@{ALICE}>", thread_ts=TS)    # even with an attachment
    events.handle_message_event(env, reply)
    e = env.store.entries.get_by_message(CHANNEL, TS)
    assert e.kind is Kind.SNIPE and e.sniped == (ALICE,)
    assert env.store.entries.count() == 1                                              # the reply isn't an entry
    assert [m["text"] for m in env.store.archive.replies(CHANNEL, TS)] == [f"#snipe <@{ALICE}>"]


def test_other_peoples_thread_replies_never_resolve_anything(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, "who"))
    events.handle_message_event(env, text_msg("1788000001.000100", BOB, f"#snipe <@{ALICE}>", thread_ts=TS))
    assert env.store.entries.get_by_message(CHANNEL, TS).kind is Kind.UNIDENTIFIED


def test_an_edit_reclassifies_and_refreshes_the_archive(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, "who"))
    edited = photo_msg(TS, CAROL, f"<@{ALICE}> snipe")
    events.handle_message_event(env, {"channel": CHANNEL, "subtype": "message_changed", "message": edited})
    assert env.store.entries.get_by_message(CHANNEL, TS).kind is Kind.SNIPE
    assert env.store.archive.get_message(CHANNEL, TS)["text"] == f"<@{ALICE}> snipe"


def test_a_deletion_event_removes_the_snipe(env):
    events.handle_message_event(env, photo_msg(TS, CAROL, f"<@{ALICE}> snipe"))
    events.handle_message_event(env, {"channel": CHANNEL, "subtype": "message_deleted", "deleted_ts": TS})
    assert env.store.entries.find() == []


# ---- /donotsnipe -----------------------------------------------------------------------------

def cmd(user, text="", channel="D1"):
    return {"user_id": user, "text": text, "channel_id": channel}


def test_donotsnipe_on_yourself(env, eslack):
    events.handle_donotsnipe(env, cmd(ALICE))
    assert env.store.dns.is_listed(ALICE)
    assert "added to the do-not-snipe list" in eslack.ephemeral[-1]["text"]


def test_donotsnipe_for_someone_else_needs_zeke(env, eslack):
    events.handle_donotsnipe(env, cmd(ALICE, f"<@{BOB}>"))
    assert not env.store.dns.is_listed(BOB)
    events.handle_donotsnipe(env, cmd(ZEKE, f"<@{BOB}>"))
    assert env.store.dns.is_listed(BOB) and env.store.names.get(BOB) == "Bob"


def test_donotsnipe_unknown_name_is_reported(env, eslack):
    eslack.users_list = lambda: {"members": []}
    events.handle_donotsnipe(env, cmd(ZEKE, "nobody"))
    assert env.store.dns.list() == [] and "Couldn't find" in eslack.ephemeral[-1]["text"]


# ---- review UI ----------------------------------------------------------------------------------

def test_reviewsnipes_shows_the_next_pending_photo_or_says_the_queue_is_empty(env, eslack):
    review_ui.reviewsnipes(env, cmd(ZEKE))
    assert "empty" in eslack.ephemeral[-1]["text"]
    svc.record_photo(env, photo_msg(TS, CAROL, "who"))
    review_ui.reviewsnipes(env, cmd(ZEKE))
    assert eslack.ephemeral[-1]["blocks"]


def test_reviewmode_toggle(env, eslack):
    review_ui.reviewmode(env, {**cmd(ZEKE), "text": "on"})
    assert notify.enabled(env)
    review_ui.reviewmode(env, {**cmd(ZEKE), "text": "off"})
    assert not notify.enabled(env)
    review_ui.reviewmode(env, {**cmd(ZEKE), "text": "maybe"})
    assert "Usage" in eslack.ephemeral[-1]["text"]


@pytest.fixture
def card_updates(monkeypatch):
    sent = []
    monkeypatch.setattr(review_ui.requests, "post", lambda url, json=None, timeout=None: sent.append((url, json)))
    return sent


def test_review_buttons_resolve_and_update_the_card(env, card_updates):
    e = svc.record_photo(env, photo_msg(TS, CAROL, f"<@{ALICE}>"))[0]
    body = {"response_url": "https://hooks/x", "actions": [{"value": e.id}]}
    review_ui.mark(env, body, Kind.SNOPE)
    assert env.store.entries.get(e.id).kind is Kind.SNOPE and "snope" in card_updates[-1][1]["text"]
    review_ui.mark(env, body, Kind.NOT_A_SNIPE)                                # a second click is harmless
    assert "already been resolved" in card_updates[-1][1]["text"]
    assert env.store.entries.get(e.id).kind is Kind.SNOPE


def test_snipe_modal_round_trip(env, eslack, card_updates):
    e = svc.record_photo(env, photo_msg(TS, CAROL, f"hmm <@{ALICE}>"))[0]
    opened = {}
    eslack.views_open = lambda **kw: opened.update(kw) or {}
    review_ui.open_snipe_modal(env, {"trigger_id": "T", "response_url": "https://hooks/x", "actions": [{"value": e.id}]})
    element = opened["view"]["blocks"][0]["element"]
    assert element["initial_users"] == [ALICE]                                    # guessed from the caption
    view = {"private_metadata": opened["view"]["private_metadata"],
            "state": {"values": {"sniped_block": {"sniped_users": {"selected_users": [ALICE, BOB]}}}}}
    review_ui.submit_snipe_modal(env, view)
    done = env.store.entries.get(e.id)
    assert done.kind is Kind.SNIPE and done.sniped == (ALICE, BOB) and "Marked as a snipe" in card_updates[-1][1]["text"]


def test_snipe_modal_with_nobody_selected_is_rejected_cleanly(env, card_updates):
    e = svc.record_photo(env, photo_msg(TS, CAROL, "who"))[0]
    view = {"private_metadata": json.dumps({"entry_id": e.id, "response_url": "https://hooks/x"}),
            "state": {"values": {"sniped_block": {"sniped_users": {"selected_users": []}}}}}
    review_ui.submit_snipe_modal(env, view)
    assert env.store.entries.get(e.id).kind is Kind.UNIDENTIFIED and "Couldn't resolve" in card_updates[-1][1]["text"]


# ---- the Bolt wiring itself ----------------------------------------------------------------------------

def test_every_listener_is_registered_and_a_command_dispatches_end_to_end(env):
    from slack_bolt import App, BoltRequest
    from slack_bolt.authorization import AuthorizeResult
    # A stub authorizer: Bolt would otherwise call auth.test over the network on the first request.
    app = App(signing_secret="x", authorize=lambda **kw: AuthorizeResult(
        enterprise_id=None, team_id="T1", bot_user_id=BOT, bot_token="xoxb-test"))
    fake = Ephemeral()
    build_app(env, app)
    env.slack = fake                                    # build_app pointed it at Bolt's real client
    req = BoltRequest(mode="socket_mode", body={"type": "slash_commands", "command": "/reviewmode", "text": "on",
                                                "user_id": ZEKE, "channel_id": "D1", "team_id": "T1"})
    resp = app.dispatch(req)
    assert resp.status == 200
    deadline = time.time() + 5
    while not notify.enabled(env) and time.time() < deadline:
        time.sleep(0.05)
    assert notify.enabled(env)

    ev_req = BoltRequest(mode="socket_mode", body={"type": "event_callback", "team_id": "T1", "event": photo_msg(TS, CAROL, f"<@{ALICE}> snipe") | {"type": "message"},
                                                   "event_id": "E1", "authorizations": [{"team_id": "T1", "user_id": BOT}]})
    assert app.dispatch(ev_req).status == 200
    deadline = time.time() + 5
    while env.store.entries.get_by_message(CHANNEL, TS) is None and time.time() < deadline:
        time.sleep(0.05)
    assert env.store.entries.get_by_message(CHANNEL, TS).kind is Kind.SNIPE
