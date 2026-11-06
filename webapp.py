"""
Entrypoint for the live-log/admin webapp. See web/app.py for what this
actually runs -- this file just exists so `python webapp.py` (and the
systemd unit in deploy/webapp.service) keeps working.

    python webapp.py
"""
import config
from web.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=config.WEBAPP_HOST, port=config.WEBAPP_PORT, threaded=True)
