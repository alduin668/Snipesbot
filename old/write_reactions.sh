cat > /mnt/shared/snipesbot/handlers/reactions.py << 'PYFILE_EOF'
"""
Slack reaction handling for SnipesBot.

Classification states:
    ?       unidentified
    orange  likely snipe
    green   confirmed snipe
    purple  snope
    black   NAS / not a snipe
    red     incorrect snipe (Zeke only, -1)

Fingerprint is retired. Zeke's red reaction is only a correction on a
confirmed green snipe; it never means "unidentified".
"""
import config
from storage import json_store, user_names
from handlers import snipe_detection


def handle_reaction_added(event: dict, client):
    reaction = event.get("reaction")
    reacting_user = event.get("user")
    item = event.get("item", {})
    if item.get("type") != "message":
        return

    channel = item.get("channel")
    ts = item.get("ts")
    if not channel or not ts:
        return

    entry = json_store.find_snipe_by_message(channel, ts)

    # TEMP DEBUG: prints the raw reaction name Slack sent, who sent it, and
    # whether it matched a tracked entry -- pulls this out of guesswork if a
    # reaction still seems to do nothing. Safe to remove once confirmed.
    print(
        f"[reactions] :{reaction}: from {reacting_user} on {channel}/{ts} -> "
        + (f"entry found, kind={entry.get('kind')}" if entry else "NO TRACKED ENTRY for this message"),
        flush=True,
    )

    # Zeke is the only person whose classification reactions have meaning.
    if reacting_user == config.ZEKE_USER_ID and entry:
        if reaction == config.INCORRECT_SNIPE_EMOJI:
            # Red is deliberately narrow: only a confirmed green snipe can
            # become an incorrect snipe. This is an irreversible manual call.
            if entry.get("kind") == "snipe" and not entry.get("ignored"):
                snipe_detection.mark_incorrect_snipe(entry["id"], client)
            return

        if reaction == config.NOT_A_SNIPE_EMOJI:
            # Black is the NAS resolution for things that are still pending.
            if entry.get("kind") in snipe_detection.PENDING_KINDS:
                snipe_detection.resolve_entry(entry["id"], "not_a_snipe", client)
            elif entry.get("kind") == "not_a_snipe":
                snipe_detection.mark_manual_kind(entry["id"], "not_a_snipe", client)
            return

        if reaction in (config.SNIPE_EMOJI, config.SNOPE_EMOJI):
            # Allow Zeke to explicitly correct a normal classification. Never
            # allow any later reaction to undo an incorrect-snipe ruling.
            if entry.get("kind") != "incorrect_snipe" and not entry.get("ignored"):
                snipe_detection.mark_manual_kind(
                    entry["id"],
                    "snipe" if reaction == config.SNIPE_EMOJI else "snope",
                    client,
                )
            return

        if reaction == config.LIKELY_SNIPE_EMOJI:
            # Orange is meaningful only as a pending state.
            if entry.get("kind") in snipe_detection.PENDING_KINDS:
                snipe_detection.mark_manual_kind(entry["id"], "likely_snipe", client)
            return

        # Question is the bot's unresolved state; Zeke adding one doesn't
        # resolve anything.
        if reaction == config.UNIDENTIFIED_EMOJI:
            return

    # Star remains independent of classification.
    if reaction == config.STAR_EMOJI:
        _handle_star(channel, ts, reacting_user, client)


def _handle_star(channel: str, ts: str, reacting_user: str, client):
    entry = json_store.find_snipe_by_message(channel, ts)
    if not entry:
        return

    user_names.ensure_cached(reacting_user, client)
    star_users = set(entry.get("star_users", []))
    star_users.add(reacting_user)
    star_users_list = list(star_users)

    notable = (config.ZEKE_USER_ID in star_users) or (
        len(star_users - {config.ZEKE_USER_ID}) >= config.NOTABLE_MIN_OTHER_STARS
    )

    json_store.update_snipe(
        entry["id"],
        star_users=star_users_list,
        notable=notable,
    )
    if notable != entry.get("notable"):
        entry["notable"] = notable
        if entry.get("kind") == "snipe" and not entry.get("ignored"):
            try:
                snipe_detection._sync_entry_to_log(entry, client)
            except Exception as e:
                print(f"[reactions] failed to sync notable star for {entry['id']}: {e}")
PYFILE_EOF
