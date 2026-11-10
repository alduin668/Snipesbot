"""Slack wiring: Bolt listeners routed to plain functions."""
from __future__ import annotations

from typing import Optional

from slack_bolt import App

from ..env import Env
from ..models import Kind
from ..services import selftest
from ..slack_io import add_rate_limit_retry
from . import events, reactions, review_ui


def build_app(env: Env, app: Optional[App] = None) -> App:
    app = app or App(token=env.cfg.bot_token)
    add_rate_limit_retry(app.client)
    env.slack = app.client

    @app.event("message")
    def on_message(event):
        events.handle_message_event(env, event)

    @app.event("reaction_added")
    def on_reaction_added(event):
        reactions.on_reaction_added(env, event)

    @app.event("reaction_removed")
    def on_reaction_removed(event):
        reactions.on_reaction_removed(env, event)

    @app.command("/donotsnipe")
    def on_donotsnipe(ack, command):
        ack()
        events.handle_donotsnipe(env, command)

    @app.command("/reviewsnipes")
    def on_reviewsnipes(ack, command):
        ack()
        review_ui.reviewsnipes(env, command)

    @app.command("/reviewmode")
    def on_reviewmode(ack, command):
        ack()
        review_ui.reviewmode(env, command)

    @app.command("/test")
    def on_test(ack, command):
        ack()
        review_ui._reply(env, command["channel_id"], command["user_id"], selftest.run_test_batch(env))

    @app.command("/cleartest")
    def on_cleartest(ack, command):
        ack()
        review_ui._reply(env, command["channel_id"], command["user_id"], selftest.clear_test_batch(env))

    @app.action("review_mark_snipe")
    def on_mark_snipe(ack, body):
        ack()
        review_ui.open_snipe_modal(env, body)

    @app.action("review_mark_snope")
    def on_mark_snope(ack, body):
        ack()
        review_ui.mark(env, body, Kind.SNOPE)

    @app.action("review_mark_nas")
    def on_mark_nas(ack, body):
        ack()
        review_ui.mark(env, body, Kind.NOT_A_SNIPE)

    @app.action("review_skip")
    def on_skip(ack, body):
        ack()
        review_ui.skip(body)

    @app.view("review_snipe_modal")
    def on_modal(ack, view):
        ack()
        review_ui.submit_snipe_modal(env, view)

    return app
