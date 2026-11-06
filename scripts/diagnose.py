"""
One-off connectivity/scope check -- run this directly, independent of the
bot process, to see exactly what Slack says instead of guessing further:

    python -m scripts.diagnose

Prints either OK or the exact Slack error for each thing the bot depends
on. A "missing_scope" error names the specific scope that's missing (Slack
puts it in the error response) -- that's more reliable than inferring it
from symptoms. auth.test also prints whatever scopes Slack reports are
actually active on this token right now, which is the ground truth if
you're unsure whether a scope change + reinstall actually took effect.
"""
from slack_bolt import App

import config

app = App(token=config.SLACK_BOT_TOKEN)
client = app.client


def check(name, fn):
    try:
        result = fn()
        print(f"OK    {name}")
        return result
    except Exception as e:
        print(f"FAIL  {name}: {e}")
        return None


def main():
    print(f"Bot token starts with: {config.SLACK_BOT_TOKEN[:12]}...")
    print(f"App token starts with: {config.SLACK_APP_TOKEN[:12]}...")
    print()

    auth = check("auth.test", lambda: client.auth_test())
    if auth is not None:
        scopes = auth.headers.get("x-oauth-scopes", "(not reported by this response)")
        print(f"      Scopes Slack reports on this token: {scopes}")
        print(f"      Bot user ID: {auth.get('user_id')}, team: {auth.get('team')}")

    check("users.info (ZEKE_USER_ID)", lambda: client.users_info(user=config.ZEKE_USER_ID))
    check("conversations.info (SNIPES_CHANNEL_ID)", lambda: client.conversations_info(channel=config.SNIPES_CHANNEL_ID))
    check(
        "conversations.history (SNIPES_CHANNEL_ID, limit=1)",
        lambda: client.conversations_history(channel=config.SNIPES_CHANNEL_ID, limit=1),
    )

    print()
    print("If conversations.info fails with 'not_in_channel', the bot isn't a")
    print("member of #snipes -- that alone would explain chat_postEphemeral")
    print("silently failing for any command run there (see FAIL lines above",
          "for the actual reason if one shows up).")


if __name__ == "__main__":
    main()
