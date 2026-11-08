"""
One registry of maintenance tasks, used by both the CLI and the webapp's
Maintenance tab. (The old Scripts tab had nine separately-wired scripts and a
registry that had to be kept in step by hand with the README and each script.)
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Any, Callable, Optional

from ..env import Env
from ..models import PENDING_KINDS
from . import legacy, selftest, sync as sync_service
from ..store import Busy

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str            # "date" | "bool"
    help: str = ""


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    description: str
    run: Callable[[Env, dict, Progress], Any]
    params: tuple = ()
    dangerous: bool = False     # asks for confirmation in the UI


def _date_to_ts(env: Env, value: str) -> float:
    d = datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.combine(d, dtime.min, tzinfo=env.cfg.tz).timestamp()


def _run_sync(env: Env, params: dict, progress: Progress):
    since = _date_to_ts(env, params["since"]) if params.get("since") else None
    return sync_service.run_sync(
        env, since=since, full=bool(params.get("full")),
        repair_reactions=bool(params.get("repair_reactions")), progress=progress,
    )


def _recover(env: Env, params: dict, progress: Progress):
    return legacy.recover_manual_decisions(env, progress)


def _selftest(env: Env, params: dict, progress: Progress):
    progress(selftest.run_test_batch(env))


def _selftest_clear(env: Env, params: dict, progress: Progress):
    progress(selftest.clear_test_batch(env))


def diagnose(env: Env, params: dict, progress: Progress) -> None:
    """Quick connectivity / permissions / data check."""
    cfg = env.cfg
    try:
        auth = env.slack.auth_test()
        progress(f"auth.test OK: bot {auth.get('user')} ({auth.get('user_id')}) in {auth.get('team')}")
    except Exception as exc:  # noqa: BLE001
        progress(f"auth.test FAILED: {exc}")
        return
    checks = (
        ("channel readable", lambda: env.slack.conversations_history(channel=cfg.channel_id, limit=1)),
        ("channel info", lambda: env.slack.conversations_info(channel=cfg.channel_id)),
        ("users.info (needs users:read)", lambda: env.slack.users_info(user=cfg.zeke_user_id)),
    )
    for label, call in checks:
        try:
            call()
            progress(f"{label}: OK")
        except Exception as exc:  # noqa: BLE001
            progress(f"{label}: FAILED ({exc})")
    s = env.store
    progress(f"database: {s.entries.count()} entries, {s.entries.count(sorted(PENDING_KINDS))} pending, "
             f"{s.archive.count()} archived messages")
    progress(f"app token configured: {'yes' if cfg.app_token else 'no (bot cannot start; webapp is fine)'}")
    progress(f"webapp password: {'set' if cfg.webapp_password else 'NOT SET'}")


TASKS: "OrderedDict[str, Task]" = OrderedDict((t.key, t) for t in (
    Task("sync", "Sync & recount",
         "Archives new Slack messages, adds any photos not yet tracked, re-runs the rules on automatic entries "
         "(manual decisions are never changed), and fixes stale reactions. Runs automatically every few hours.",
         _run_sync,
         params=(Param("since", "Rescan from", "date", "Leave empty to continue from the last sync."),
                 Param("full", "Rescan the whole season", "bool"),
                 Param("repair_reactions", "Verify every reaction on Slack", "bool",
                       "One Slack call per photo; slow on a big season."))),
    Task("recover_manuals", "Recover manual decisions from reactions",
         "One-time: treats Zeke's own classification reactions on undecided photos as manual decisions. "
         "Only needed after migrating from the old JSON files.",
         _recover, dangerous=True),
    Task("diagnose", "Diagnose", "Checks the Slack connection, permissions, and data.", diagnose),
    Task("selftest", "Run detector self-test",
         "Posts six synthetic photos to the channel and classifies them. Clear them afterwards.",
         _selftest, dangerous=True),
    Task("selftest_clear", "Clear self-test messages",
         "Deletes the self-test's Slack messages and removes their entries.", _selftest_clear, dangerous=True),
))


class JobRunner:
    """Runs one maintenance task at a time in a background thread and keeps
    a bounded log of the last few runs for the UI to poll."""

    MAX_RUNS = 20
    MAX_LINES = 2000

    def __init__(self, env: Env, tasks=None):
        self.env = env
        self.tasks = tasks or TASKS
        self._runs: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()

    def start(self, key: str, params: Optional[dict] = None) -> str:
        task = self.tasks.get(key)
        if task is None:
            raise KeyError(key)
        with self._lock:
            if any(not r["done"] for r in self._runs.values()):
                raise Busy("another task is still running")
            run_id = uuid.uuid4().hex
            run = {"id": run_id, "task": key, "started": time.time(), "lines": deque(maxlen=self.MAX_LINES),
                   "done": False, "ok": None, "error": None}
            self._runs[run_id] = run
            while len(self._runs) > self.MAX_RUNS:
                self._runs.popitem(last=False)
        threading.Thread(target=self._execute, args=(task, params or {}, run), daemon=True,
                         name=f"task-{key}").start()
        return run_id

    def _execute(self, task: Task, params: dict, run: dict) -> None:
        try:
            task.run(self.env, params, lambda line: run["lines"].append(str(line)))
            run["ok"] = True
        except Busy as exc:
            run["ok"], run["error"] = False, str(exc)
            run["lines"].append(f"Not started: {exc}")
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            run["ok"], run["error"] = False, f"{type(exc).__name__}: {exc}"
            run["lines"].append(f"FAILED: {run['error']}")
        finally:
            self.env.store.db.close()   # this thread's connection
            run["done"] = True

    def get(self, run_id: str) -> Optional[dict]:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return {"id": run["id"], "task": run["task"], "output": "\n".join(run["lines"]),
                "done": run["done"], "ok": run["ok"], "error": run["error"]}
