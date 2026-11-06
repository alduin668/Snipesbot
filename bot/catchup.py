"""
Automatic, ongoing catch-up -- replaces needing to remember to run
scripts/backfill.py by hand. Runs inside the live bot process (started from
app.py's __main__), re-scanning #snipes on a timer and folding in anything
missing, using the exact same core (scripts.backfill.run_backfill) as a
manual run, so this can never drift out of sync with what
`python -m scripts.backfill` does.

The first pass ever run (no watermark saved yet) scans from
config.SEASON_START_DATE -- the start of the season. Every pass after that
starts from a persisted watermark (the time the previous successful pass
began) instead of the season start, so this stays cheap as the season goes
on: routine catch-up only looks at what's happened since last time, not the
whole season, every single run.

If a pass fails (network blip, Slack error, etc.), the watermark is NOT
advanced -- the next scheduled pass retries the same window rather than
silently skipping whatever the failed pass missed.

The same loop also watches for connectivity loss. Socket Mode reconnects on
its own, but time spent disconnected is still a gap in what the live
listener ever saw -- so rather than silently waiting for the next regular
CATCHUP_INTERVAL_SECONDS tick (which could be hours away), losing and
regaining a connection to Slack is logged, DMs Zeke once it's back, and
triggers an immediate out-of-cycle catch-up pass right then.

Every pass -- scheduled or triggered by a reconnect -- also syncs
storage/archive_store.py's permanent local archive via bot/archiver.py
(run_backfill()'s first phase), since Slack only keeps messages/files for
SLACK_MESSAGE_RETENTION_DAYS. A startup check additionally warns (and DMs
Zeke) if the season's already close to that cutoff with nothing archived
yet -- see _check_archive_staleness().
"""
import threading
import time
from datetime import datetime

import config
from storage import archive_store, json_store
from scripts.backfill import run_backfill, print_summary

_WATERMARK_KEY = "last_catchup_ts"


def _check_archive_staleness(client):
    """A one-time sanity check at startup, not a substitute for the real
    safety net (every catch-up pass archives automatically regardless) --
    just loud, early visibility if this got deployed late enough that the
    season is already close to Slack's retention cutoff and nothing has
    been archived yet, since that combination means something may already
    be at real risk of aging out before the first scheduled pass gets to
    it."""
    season_start = datetime.strptime(config.SEASON_START_DATE, "%Y-%m-%d")
    days_elapsed = (datetime.now() - season_start).days
    if days_elapsed < config.SLACK_MESSAGE_RETENTION_DAYS - 14:
        return  # plenty of runway -- nothing worth warning about yet
    if archive_store.message_count() > 0:
        return  # already archiving something -- the real safety net is working

    warning = (
        f":rotating_light: The season started {days_elapsed} days ago (Slack's retention limit is "
        f"{config.SLACK_MESSAGE_RETENTION_DAYS} days) but the local archive is still empty. Run "
        f"`python -m scripts.archive_sync` (or the webapp's Scripts tab -> Sync Local Archive) now, "
        f"before anything ages out of Slack for good."
    )
    print(f"[archiver] {warning}", flush=True)
    try:
        client.chat_postMessage(channel=config.ZEKE_USER_ID, text=warning)
    except Exception as e:
        print(f"[archiver] failed to DM the staleness warning: {e}", flush=True)


def _run_once(client):
    since_ts = json_store.get_setting(_WATERMARK_KEY)
    if since_ts is None:
        since_ts = datetime.strptime(config.SEASON_START_DATE, "%Y-%m-%d").timestamp()

    started_at = time.time()
    print(f"[catchup] scanning #snipes since {datetime.fromtimestamp(since_ts).isoformat()}")
    try:
        stats = run_backfill(since_ts, client)
    except Exception as e:
        print(f"[catchup] pass failed, watermark not advanced, will retry next interval: {e}")
        return

    if stats["added"]:
        print_summary(stats)
    else:
        print("[catchup] up to date, nothing new")
    json_store.set_setting(_WATERMARK_KEY, started_at)


def _is_online(client) -> bool:
    try:
        client.auth_test()
        return True
    except Exception:
        return False


def _notify_reconnect(client, offline_since: float, back_online_at: float):
    """Best-effort DM to Zeke -- there's no way to notify him WHILE
    offline, only after reconnecting, so this is purely informational."""
    outage = back_online_at - offline_since
    minutes = outage / 60
    duration = f"{minutes:.0f} min" if minutes >= 1 else f"{outage:.0f}s"
    since_str = datetime.fromtimestamp(offline_since).strftime("%-I:%M %p")
    try:
        client.chat_postMessage(
            channel=config.ZEKE_USER_ID,
            text=(
                f":satellite: Lost connection to Slack around {since_str} and just reconnected "
                f"(~{duration} later). Running an immediate catch-up pass to sweep up anything missed."
            ),
        )
    except Exception as e:
        print(f"[connectivity] failed to DM Zeke about the reconnect: {e}", flush=True)


def start_background_catchup(client):
    """Call once at bot startup. Runs in a daemon thread so it starts and
    stops with the bot process -- no separate service to manage.

    Checks connectivity every CONNECTIVITY_CHECK_INTERVAL_SECONDS rather
    than sleeping the full CATCHUP_INTERVAL_SECONDS in one block, so a
    reconnect is noticed (and caught up on) promptly instead of only at the
    next regularly-scheduled pass."""
    def loop():
        was_online = True
        offline_since = None

        _check_archive_staleness(client)
        _run_once(client)
        next_scheduled = time.time() + config.CATCHUP_INTERVAL_SECONDS

        while True:
            time.sleep(config.CONNECTIVITY_CHECK_INTERVAL_SECONDS)
            online = _is_online(client)

            if not online:
                if was_online:
                    offline_since = time.time()
                    print("[connectivity] lost connection to Slack, watching for it to come back...", flush=True)
                was_online = False
                continue

            if not was_online:
                back_online_at = time.time()
                print(
                    f"[connectivity] back online after "
                    f"{back_online_at - offline_since:.0f}s -- running an immediate catch-up pass",
                    flush=True,
                )
                _notify_reconnect(client, offline_since, back_online_at)
                _run_once(client)
                next_scheduled = time.time() + config.CATCHUP_INTERVAL_SECONDS
                was_online = True
                continue

            if time.time() >= next_scheduled:
                _run_once(client)
                next_scheduled = time.time() + config.CATCHUP_INTERVAL_SECONDS

    thread = threading.Thread(target=loop, daemon=True, name="snipesbot-catchup")
    thread.start()
