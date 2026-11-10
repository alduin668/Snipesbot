"""Process entry points for the two long-running processes."""
from __future__ import annotations

import logging
import sys

from .config import ConfigError, load_config
from .env import Env


def _fail(exc: Exception) -> None:
    print(f"Configuration error: {exc}", file=sys.stderr)
    sys.exit(2)


def run_bot() -> None:
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from .services import background
    from .slack_app import build_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config()
        token = cfg.require_app_token()
    except ConfigError as exc:
        _fail(exc)
    env = Env.create(cfg)
    app = build_app(env)
    background.start(env)
    SocketModeHandler(app, token).start()


def run_web() -> None:
    from .slack_io import make_client
    from .web import auth, create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config()
        auth.check_startup(cfg)
    except ConfigError as exc:
        _fail(exc)
    env = Env.create(cfg, slack=make_client(cfg.bot_token))
    create_app(env).run(host=cfg.webapp_host, port=cfg.webapp_port, threaded=True)
