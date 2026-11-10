"""
The bot's background loop: scheduled sync, connectivity watch, review DMs.

One thread, one loop, no per-photo timers. It replaces the separate catch-up
thread plus a threading.Timer per pending photo.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from ..env import Env
from ..store import Busy
from . import notify, sync as sync_service

log = logging.getLogger("snipesbot")

NOTIFY_TICK_SECONDS = 15


def check_archive_staleness(env: Env) -> None:
    """One-time startup warning: season is close to Slack's retention cutoff
    and nothing has been archived yet."""
    cfg = env.cfg
    days = (datetime.now(cfg.tz).date() - cfg.season_start).days
    if days < cfg.retention_days - 14 or env.store.archive.count() > 0:
        return
    msg = (f":rotating_light: The season started {days} days ago (Slack keeps messages {cfg.retention_days} days) "
           f"but the local archive is empty. Run `python -m snipesbot sync` (or the webapp's Maintenance tab) "
           f"now, before anything ages out of Slack for good.")
    log.warning(msg)
    try:
        env.slack.chat_postMessage(channel=cfg.zeke_user_id, text=msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not DM the staleness warning: %s", exc)


def _is_online(env: Env) -> bool:
    try:
        env.slack.auth_test()
        return True
    except Exception:  # noqa: BLE001
        return False


def _sync_quietly(env: Env) -> None:
    try:
        sync_service.run_sync(env, progress=lambda line: log.info(line))
    except Busy:
        log.info("[sync] another sync is running; skipping this pass")
    except Exception as exc:  # noqa: BLE001 - watermark isn't advanced, so the next pass retries
        log.warning("[sync] pass failed, will retry: %s", exc)


def run_forever(env: Env, *, sleep=time.sleep, clock=time.time, max_ticks: int | None = None) -> None:
    """Loop body, with injectable time so it is testable."""
    cfg = env.cfg
    env.workspace_url()
    check_archive_staleness(env)
    _sync_quietly(env)
    next_sync = clock() + cfg.catchup_interval_seconds
    next_conn = clock() + cfg.connectivity_interval_seconds
    online, offline_since, ticks = True, None, 0

    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        sleep(NOTIFY_TICK_SECONDS)
        now = clock()
        try:
            notify.run_once(env, now)
        except Exception as exc:  # noqa: BLE001
            log.warning("review notification pass failed: %s", exc)

        if now >= next_conn:
            next_conn = now + cfg.connectivity_interval_seconds
            if not _is_online(env):
                if online:
                    offline_since = now
                    log.warning("lost connection to Slack; watching for it to come back")
                online = False
                continue
            if not online:
                gap = now - (offline_since or now)
                log.info("back online after %.0fs; running an immediate sync", gap)
                try:
                    env.slack.chat_postMessage(
                        channel=cfg.zeke_user_id,
                        text=f":satellite: Lost connection to Slack for ~{max(1, round(gap / 60))} min and "
                             f"reconnected. Running a catch-up pass now.")
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not DM the reconnect notice: %s", exc)
                online = True
                _sync_quietly(env)
                next_sync = clock() + cfg.catchup_interval_seconds
                continue

        if now >= next_sync:
            _sync_quietly(env)
            next_sync = clock() + cfg.catchup_interval_seconds


def start(env: Env) -> threading.Thread:
    t = threading.Thread(target=run_forever, args=(env,), daemon=True, name="snipesbot-background")
    t.start()
    return t
