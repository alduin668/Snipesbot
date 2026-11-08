"""
Configuration, loaded explicitly.

Nothing here runs at import time: `load_config()` reads the environment (and
a `.env` file if present) when a process starts. That is what lets the web
app start without SLACK_APP_TOKEN (only the bot needs it) and lets tests
build a `Config` without touching the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date, timezone, tzinfo
from pathlib import Path
from typing import Mapping, Optional
from zoneinfo import ZoneInfo


class ConfigError(RuntimeError):
    pass


def system_zone() -> tzinfo:
    """Best effort at the server's IANA zone: $TZ, /etc/localtime, /etc/timezone, else UTC."""
    candidates = []
    if os.environ.get("TZ"):
        candidates.append(os.environ["TZ"].lstrip(":"))
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for name in candidates:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - unknown/invalid zone name
            continue
    return timezone.utc


@dataclass(frozen=True)
class Emojis:
    """Reaction names (no colons) the bot owns, one per classification."""
    snipe: str = "large_green_circle"
    snope: str = "large_purple_circle"
    unidentified: str = "question"
    likely_snipe: str = "large_orange_circle"
    not_a_snipe: str = "black_circle"
    incorrect_snipe: str = "red_circle"
    special: str = "fingerprint"
    star: str = "star"
    # Reactions older versions of the bot used; still cleaned up when the
    # bot's own copy is found, never added.
    legacy: tuple = ("thumbsup",)


@dataclass(frozen=True)
class Config:
    # --- Slack ---
    bot_token: str
    channel_id: str
    zeke_user_id: str
    app_token: Optional[str] = None
    reviewer_user_id: str = ""
    test_channel_id: str = ""          # where /test posts; defaults to channel_id

    # --- Storage ---
    data_dir: Path = Path("data")

    # --- Webapp ---
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8420
    webapp_password: str = ""

    # --- Detection ---
    snipe_keywords: tuple = ("snipe", "sniped", "snipes", "sniping")
    snope_keywords: tuple = ("snope", "snoped", "snopes", "snoping")
    emojis: Emojis = field(default_factory=Emojis)
    notable_min_other_stars: int = 3

    # --- Review queue ---
    context_window: int = 2
    review_notify_delay_seconds: int = 90
    review_notify_max_age_seconds: int = 3600

    # --- Archive / sync ---
    season_start: date = date(2026, 8, 15)
    retention_days: int = 90
    catchup_interval_seconds: int = 6 * 3600
    connectivity_interval_seconds: int = 30
    purge_files_on_delete: bool = True

    timezone_name: str = ""

    # ------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "snipesbot.db"

    @property
    def archive_files_dir(self) -> Path:
        return self.data_dir / "archive" / "files"

    @property
    def tz(self) -> tzinfo:
        """The configured zone, else the server's. Always a real, DST-aware zone:
        `datetime.astimezone().tzinfo` would be a fixed offset and put winter
        photos an hour off after a clock change."""
        return ZoneInfo(self.timezone_name) if self.timezone_name else system_zone()

    @property
    def effective_reviewer(self) -> str:
        return self.reviewer_user_id or self.zeke_user_id

    @property
    def effective_test_channel(self) -> str:
        return self.test_channel_id or self.channel_id

    def emoji_for(self, kind) -> str:
        return getattr(self.emojis, str(getattr(kind, "value", kind)))

    def managed_emojis(self) -> list:
        """Every reaction name the bot may have placed on a photo."""
        e = self.emojis
        names = [e.snipe, e.snope, e.unidentified, e.likely_snipe,
                 e.not_a_snipe, e.incorrect_snipe, e.special, *e.legacy]
        return list(dict.fromkeys(names))

    def require_app_token(self) -> str:
        if not self.app_token:
            raise ConfigError("SLACK_APP_TOKEN is required to run the Slack bot (Socket Mode).")
        return self.app_token

    def with_(self, **changes) -> "Config":
        return replace(self, **changes)


def _csv(value: Optional[str], default: tuple) -> tuple:
    if not value:
        return default
    items = tuple(v.strip().lower() for v in value.split(",") if v.strip())
    return items or default


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_config(environ: Optional[Mapping[str, str]] = None, *, load_dotenv_file: bool = True) -> Config:
    """Build a Config from the environment. Raises ConfigError listing every
    missing required variable at once, rather than one KeyError at a time."""
    if environ is None:
        if load_dotenv_file:
            from dotenv import load_dotenv
            load_dotenv()
        environ = os.environ

    missing = [k for k in ("SLACK_BOT_TOKEN", "SNIPES_CHANNEL_ID", "ZEKE_USER_ID") if not environ.get(k)]
    if missing:
        raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

    defaults = Config(bot_token="", channel_id="", zeke_user_id="")
    try:
        season = date.fromisoformat(environ.get("SEASON_START_DATE") or defaults.season_start.isoformat())
    except ValueError as exc:
        raise ConfigError(f"SEASON_START_DATE must be YYYY-MM-DD: {exc}") from exc

    return Config(
        bot_token=environ["SLACK_BOT_TOKEN"],
        app_token=environ.get("SLACK_APP_TOKEN") or None,
        channel_id=environ["SNIPES_CHANNEL_ID"],
        zeke_user_id=environ["ZEKE_USER_ID"],
        reviewer_user_id=environ.get("REVIEWER_USER_ID") or "",
        test_channel_id=environ.get("TEST_CHANNEL_ID") or "",
        data_dir=Path(environ.get("SNIPESBOT_DATA_DIR") or "data"),
        webapp_host=environ.get("WEBAPP_HOST") or defaults.webapp_host,
        webapp_port=int(environ.get("WEBAPP_PORT") or defaults.webapp_port),
        webapp_password=environ.get("WEBAPP_PASSWORD") or "",
        snipe_keywords=_csv(environ.get("SNIPE_KEYWORDS"), defaults.snipe_keywords),
        snope_keywords=_csv(environ.get("SNOPE_KEYWORDS"), defaults.snope_keywords),
        notable_min_other_stars=int(environ.get("NOTABLE_MIN_OTHER_STARS") or defaults.notable_min_other_stars),
        context_window=int(environ.get("CONTEXT_WINDOW") or defaults.context_window),
        review_notify_delay_seconds=int(environ.get("CONTEXT_CAPTURE_DELAY_SECONDS") or defaults.review_notify_delay_seconds),
        season_start=season,
        retention_days=int(environ.get("SLACK_MESSAGE_RETENTION_DAYS") or defaults.retention_days),
        catchup_interval_seconds=int(environ.get("CATCHUP_INTERVAL_SECONDS") or defaults.catchup_interval_seconds),
        connectivity_interval_seconds=int(
            environ.get("CONNECTIVITY_CHECK_INTERVAL_SECONDS") or defaults.connectivity_interval_seconds
        ),
        purge_files_on_delete=_bool(environ.get("PURGE_FILES_ON_DELETE"), True),
        timezone_name=environ.get("TIMEZONE") or "",
    )
