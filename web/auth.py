"""
Authentication and CSRF protection for the admin UI.

The old webapp had neither, listened on 0.0.0.0, and could run scripts that
delete Slack messages. Now:
  * set WEBAPP_PASSWORD to require HTTP Basic auth (any username);
  * binding to a non-loopback address without a password refuses to start
    (override with WEBAPP_ALLOW_INSECURE=1 if you really are on a trusted network);
  * every state-changing request must carry `X-Requested-With: snipesbot`,
    which a cross-site page can't add without a CORS preflight we never grant.
"""
from __future__ import annotations

import hmac
import ipaddress
import os

from flask import Flask, Response, jsonify, request

from ..config import Config, ConfigError

CSRF_HEADER = ("X-Requested-With", "snipesbot")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_startup(cfg: Config, environ=os.environ) -> None:
    if cfg.webapp_password or is_loopback(cfg.webapp_host):
        return
    if environ.get("WEBAPP_ALLOW_INSECURE") == "1":
        return
    raise ConfigError(
        f"Refusing to serve an unauthenticated admin UI on {cfg.webapp_host}. Set WEBAPP_PASSWORD, "
        f"bind WEBAPP_HOST=127.0.0.1 (and tunnel in), or set WEBAPP_ALLOW_INSECURE=1 to accept the risk."
    )


def install(app: Flask, cfg: Config) -> None:
    @app.before_request
    def guard():
        if cfg.webapp_password:
            supplied = request.authorization.password if request.authorization else None
            if supplied is None or not hmac.compare_digest(supplied.encode(), cfg.webapp_password.encode()):
                return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="SnipesBot"'})
        if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER[0]) != CSRF_HEADER[1]:
            return jsonify({"error": "missing X-Requested-With header"}), 403
        return None
