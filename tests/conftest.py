"""Shared fixtures: a temp-database Env and a fake Slack client."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from snipesbot.config import Config  # noqa: E402
from snipesbot.env import Env  # noqa: E402

BOT, ZEKE, CHANNEL = "UBOT00001", "UZEKE0001", "C0SNIPES1"
ALICE, BOB, CAROL = "UALICE001", "UBOB00001", "UCAROL001"


class SlackError(Exception):
    def __init__(self, error):
        super().__init__(error)
        self.response = {"error": error}


class FakeSlack:
    """Just enough of slack_sdk.WebClient for the services, with call counting."""

    def __init__(self):
        self.calls: list = []
        self.reactions: dict = {}        # (channel, ts) -> [(name, user)]
        self.history: list = []          # raw messages, any order
        self.replies: dict = {}          # thread_ts -> [raw replies]
        self.posted: list = []           # chat_postMessage kwargs
        self.deleted: list = []
        self.fail: set = set()           # method names that should raise
        self.users = {ALICE: "Alice", BOB: "Bob", CAROL: "Carol", ZEKE: "Zeke"}

    def _rec(self, name, **kw):
        self.calls.append(name)
        if name in self.fail:
            raise SlackError("boom")

    def count(self, name):
        return self.calls.count(name)

    def auth_test(self):
        self._rec("auth_test")
        return {"user_id": BOT, "user": "snipesbot", "team": "T", "url": "https://team.slack.com/"}

    def reactions_get(self, channel, timestamp):
        self._rec("reactions_get")
        grouped: dict = {}
        for name, user in self.reactions.get((channel, timestamp), []):
            grouped.setdefault(name, []).append(user)
        return {"message": {"reactions": [{"name": n, "users": u} for n, u in grouped.items()]}}

    def reactions_add(self, channel, timestamp, name):
        self._rec("reactions_add")
        key = (channel, timestamp)
        if (name, BOT) in self.reactions.get(key, []):
            raise SlackError("already_reacted")
        self.reactions.setdefault(key, []).append((name, BOT))
        return {}

    def reactions_remove(self, channel, timestamp, name):
        self._rec("reactions_remove")
        self.reactions[(channel, timestamp)] = [r for r in self.reactions.get((channel, timestamp), []) if r != (name, BOT)]
        return {}

    def chat_postMessage(self, **kw):
        self._rec("chat_postMessage")
        self.posted.append(kw)
        return {"ts": f"1789000000.{len(self.posted):06d}"}

    def chat_delete(self, channel, ts):
        self._rec("chat_delete")
        self.deleted.append((channel, ts))
        return {}

    def users_info(self, user):
        self._rec("users_info")
        if user not in self.users:
            raise SlackError("user_not_found")
        return {"user": {"profile": {"display_name": self.users[user]}}}

    def conversations_history(self, channel, oldest="0", cursor=None, limit=200, **kw):
        self._rec("conversations_history")
        msgs = [m for m in self.history if float(m["ts"]) >= float(oldest)]
        return {"messages": sorted(msgs, key=lambda m: m["ts"], reverse=True)}

    def conversations_replies(self, channel, ts, cursor=None, limit=200):
        self._rec("conversations_replies")
        return {"messages": [{"ts": ts}] + self.replies.get(ts, [])}

    def files_info(self, file):
        self._rec("files_info")
        raise SlackError("file_not_found")


def photo_msg(ts, user, text="", file_id=None, **extra):
    fid = file_id or f"F{ts.replace('.', '')}"
    return {"ts": ts, "user": user, "text": text, "channel": CHANNEL,
            "files": [{"id": fid, "name": f"{fid}.jpg", "mimetype": "image/jpeg"}], **extra}


def text_msg(ts, user, text, **extra):
    return {"ts": ts, "user": user, "text": text, "channel": CHANNEL, **extra}


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(bot_token="xoxb-test", channel_id=CHANNEL, zeke_user_id=ZEKE, data_dir=tmp_path,
                  timezone_name="America/New_York", season_start=__import__("datetime").date(2026, 8, 15))


@pytest.fixture
def slack() -> FakeSlack:
    return FakeSlack()


@pytest.fixture
def env(cfg, slack) -> Env:
    e = Env.create(cfg, slack=slack)
    yield e
    e.store.db.close()


@pytest.fixture
def no_download(monkeypatch):
    """Archiving tries to download files over HTTP; make it write a stub instead."""
    from snipesbot.services import archiver

    def fake(env, f):
        if f.get("id"):
            env.store.archive.save_file(f["id"], f.get("name", ""), f.get("mimetype", ""), b"bytes-" + f["id"].encode())
        return True
    monkeypatch.setattr(archiver, "_download", fake)
