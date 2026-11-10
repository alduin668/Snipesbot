"""Flask app factory for the admin UI."""
from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template

from ..env import Env
from ..services.entries import EntryNotFound, InvalidTransition
from ..services.maintenance import JobRunner
from ..store import Busy
from . import auth
from .api import compare, entries, people, scoring, system


def create_app(env: Env) -> Flask:
    app = Flask(__name__)
    app.config["ENV"] = env
    app.config["RUNNER"] = JobRunner(env)
    auth.install(app, env.cfg)

    for module in (compare, entries, people, scoring, system):
        app.register_blueprint(module.bp, url_prefix="/api")

    @app.teardown_request
    def _release_connection(_exc):
        env.store.db.close()      # each request runs on its own thread; don't leak its connection

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.errorhandler(InvalidTransition)
    def _invalid(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(EntryNotFound)
    def _missing(exc):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(Busy)
    def _busy(exc):
        return jsonify({"error": str(exc)}), 409

    @app.errorhandler(Exception)
    def _unexpected(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return exc
        logging.getLogger("snipesbot").exception("unhandled error in request")
        return jsonify({"error": "internal error"}), 500

    return app
