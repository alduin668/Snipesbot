"""
SnipesBot entrypoint. Wires Slack events/commands/actions to the handlers
in bot/, and starts the background catch-up loop. This process is the only
one that needs SLACK_APP_TOKEN (Socket Mode) -- web/app.py is a separate,
optional process that only needs SLACK_BOT_TOKEN.

Every message this bot ever sees in #snipes -- live, edited, or a thread
reply -- gets archived (bot/archiver.py) before anything else happens to
it, so nothing downstream ever ends up depending on Slack still having it
once its 90-day retention window closes.

    python app.py
"""
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config
from bot import archiver, detection, reactions, blocking, review, catchup, testing

app = App(token=config.SLACK_BOT_TOKEN)

SNIPE_RESOLUTION_RE = re.compile(r"#snipe\b", re.IGNORECASE)


@app.event("message")
def on_message(event, client, say):
    if event.get("bot_id"):
        return
    subtype = event.get("subtype")

    if subtype == "message_deleted":
        blocking.handle_message_deleted(event, client)
        return

    if subtype == "message_changed":
        new_msg = dict(event.get("message", {}))
        if event.get("channel") == config.SNIPES_CHANNEL_ID and new_msg.get("ts"):
            new_msg["channel"] = event["channel"]
            archiver.archive_message(new_msg, client)
            detection.reevaluate_edit(
                event["channel"], new_msg["ts"], new_msg.get("text", ""), client
            )
        return

    if event.get("channel") != config.SNIPES_CHANNEL_ID:
        return

    # Archived unconditionally, before anything else -- this covers live
    # thread replies too (a reply arrives as a normal message event with
    # thread_ts set, so it flows through here same as any other message).
    archiver.archive_message(event, client)

    thread_ts = event.get("thread_ts")
    if thread_ts and thread_ts != event.get("ts") and event.get("user") == config.ZEKE_USER_ID:
        text = event.get("text", "")
        if SNIPE_RESOLUTION_RE.search(text):
            detection.resolve_via_thread_reply(
                event["channel"], thread_ts, text, client
            )
        # Thread replies never participate in normal per-photo detection when
        # Zeke is using them as an explicit #snipe correction.
        return

    detection.handle_message(event, client)


@app.event("reaction_added")
def on_reaction_added(event, client):
    reactions.handle_reaction_added(event, client)


@app.command("/donotsnipe")
def on_donotsnipe(ack, command, client):
    blocking.handle_donotsnipe_command(ack, command, client)


@app.command("/reviewsnipes")
def on_reviewsnipes(ack, command, client):
    review.handle_reviewsnipes_command(ack, command, client)


@app.command("/reviewmode")
def on_reviewmode(ack, command, client):
    review.handle_reviewmode_command(ack, command, client)


@app.command("/test")
def on_test(ack, command, client):
    ack()
    result = testing.run_test_batch(client)
    client.chat_postEphemeral(channel=command["channel_id"], user=command["user_id"], text=result)


@app.command("/cleartest")
def on_cleartest(ack, command, client):
    ack()
    result = testing.clear_test_batch(client)
    client.chat_postEphemeral(channel=command["channel_id"], user=command["user_id"], text=result)


@app.action("review_mark_snipe")
def on_review_mark_snipe(ack, body, client):
    review.handle_mark_snipe(ack, body, client)


@app.action("review_mark_snope")
def on_review_mark_snope(ack, body, client):
    review.handle_mark_snope(ack, body, client)


@app.action("review_mark_nas")
def on_review_mark_nas(ack, body, client):
    review.handle_mark_nas(ack, body, client)


@app.action("review_skip")
def on_review_skip(ack, body, client):
    review.handle_skip(ack, body, client)


@app.view("review_snipe_modal")
def on_review_snipe_modal(ack, body, client, view):
    review.handle_review_snipe_modal_submission(ack, body, client, view)


if __name__ == "__main__":
    catchup.start_background_catchup(app.client)
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    handler.start()
