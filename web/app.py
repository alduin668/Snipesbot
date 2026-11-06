"""
Flask app factory for the live-log/admin webapp.

Runs as its OWN process, separate from the Slack bot (app.py) -- see
deploy/webapp.service. They share the .xlsx file and data/ directory on
disk, written atomically by storage/xlsx_store.py and storage/json_store.py,
so it's safe for both to be running at once.

    python webapp.py
    # then visit http://<host>:8420/  (port configurable via WEBAPP_PORT)

SECURITY NOTE: there is no authentication on any of this. The Review Queue
tab can resolve snipes, and the Scripts tab can run backfill/resync/etc on
the server -- anyone who can reach this port can do both. Fine on a
trusted LAN; if this is ever exposed more broadly, put it behind a reverse
proxy with auth, or bind WEBAPP_HOST to 127.0.0.1 and tunnel/VPN in.
"""
import os

from flask import Flask

from web import scripts_runner
from web.routes import bp

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app() -> Flask:
    scripts_runner.PROJECT_DIR = PROJECT_DIR
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
