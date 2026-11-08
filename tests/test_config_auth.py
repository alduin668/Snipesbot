import pytest

from snipesbot.config import ConfigError, load_config
from snipesbot.web import auth

REQUIRED = {"SLACK_BOT_TOKEN": "xoxb-1", "SNIPES_CHANNEL_ID": "C1", "ZEKE_USER_ID": "U1"}


def test_the_webapp_no_longer_needs_the_socket_mode_token():
    """config.py used to raise KeyError for SLACK_APP_TOKEN in every process, webapp included."""
    cfg = load_config(REQUIRED, load_dotenv_file=False)
    assert cfg.app_token is None
    with pytest.raises(ConfigError, match="SLACK_APP_TOKEN"):
        cfg.require_app_token()


def test_all_missing_variables_are_reported_together():
    with pytest.raises(ConfigError) as exc:
        load_config({}, load_dotenv_file=False)
    assert all(k in str(exc.value) for k in REQUIRED)


def test_overrides_and_defaults():
    cfg = load_config({**REQUIRED, "SNIPE_KEYWORDS": "Snipe, Zap", "WEBAPP_PORT": "9000", "SEASON_START_DATE": "2026-09-01",
                       "PURGE_FILES_ON_DELETE": "0"}, load_dotenv_file=False)
    assert cfg.snipe_keywords == ("snipe", "zap") and cfg.webapp_port == 9000
    assert cfg.season_start.isoformat() == "2026-09-01" and cfg.purge_files_on_delete is False
    assert cfg.webapp_host == "127.0.0.1"                       # was 0.0.0.0
    with pytest.raises(ConfigError, match="SEASON_START_DATE"):
        load_config({**REQUIRED, "SEASON_START_DATE": "soon"}, load_dotenv_file=False)


def test_emoji_lookup_by_kind():
    cfg = load_config(REQUIRED, load_dotenv_file=False)
    from snipesbot.models import Kind
    assert cfg.emoji_for(Kind.SNIPE) == "large_green_circle" and cfg.emoji_for("special") == "fingerprint"
    assert "thumbsup" in cfg.managed_emojis()


@pytest.mark.parametrize("host, password, allow, ok", [
    ("127.0.0.1", "", "", True), ("localhost", "", "", True), ("::1", "", "", True),
    ("0.0.0.0", "", "", False), ("192.168.1.5", "", "", False),
    ("0.0.0.0", "secret", "", True), ("0.0.0.0", "", "1", True),
])
def test_refuses_to_expose_an_unauthenticated_admin_ui(host, password, allow, ok):
    cfg = load_config({**REQUIRED, "WEBAPP_HOST": host, "WEBAPP_PASSWORD": password}, load_dotenv_file=False)
    environ = {"WEBAPP_ALLOW_INSECURE": allow}
    if ok:
        auth.check_startup(cfg, environ)
    else:
        with pytest.raises(ConfigError, match="WEBAPP_PASSWORD"):
            auth.check_startup(cfg, environ)


def test_the_default_timezone_is_dst_aware(monkeypatch):
    """A fixed offset (what astimezone().tzinfo gives) would put winter photos an hour off."""
    from datetime import datetime, timezone
    monkeypatch.setenv("TZ", "America/New_York")
    tz = load_config(REQUIRED, load_dotenv_file=False).tz
    summer = datetime(2026, 7, 1, 12, tzinfo=timezone.utc).astimezone(tz)
    winter = datetime(2026, 1, 1, 12, tzinfo=timezone.utc).astimezone(tz)
    assert (summer.hour, winter.hour) == (8, 7)


def test_an_explicit_timezone_wins_over_the_system_one(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("TZ", "America/New_York")
    cfg = load_config({**REQUIRED, "TIMEZONE": "Asia/Tokyo"}, load_dotenv_file=False)
    assert datetime(2026, 1, 1, 0, tzinfo=timezone.utc).astimezone(cfg.tz).hour == 9


def test_an_unusable_system_zone_falls_back_to_utc(monkeypatch):
    from datetime import timezone
    from snipesbot import config
    monkeypatch.setenv("TZ", "Not/AZone")
    monkeypatch.setattr(config.os.path, "realpath", lambda p: "/nonexistent")
    monkeypatch.setattr(config.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError()))
    assert config.system_zone() is timezone.utc


def test_entry_points_exit_cleanly_on_bad_config(monkeypatch, capsys):
    from snipesbot import runtime
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    for fn in (runtime.run_bot, runtime.run_web):
        with pytest.raises(SystemExit) as exc:
            fn()
        assert exc.value.code == 2
    assert "Missing required environment variable" in capsys.readouterr().err
