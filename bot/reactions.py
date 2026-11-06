"""
Slack reaction handling for SnipesBot.

Classification states:
    ?           unidentified
    orange      likely snipe
    green       confirmed snipe
    purple      snope
    black       NAS / not a snipe
    red         incorrect snipe (Zeke only, -1)
    fingerprint special -- too niche for the detector; see the webapp's
                Special tab and bot/detection.py's create_special_snipe()

Zeke is the only user whose classification reactions have meaning.

Orange is special:
    The bot uses orange to mark a likely snipe.
    When Zeke reacts orange to that message, it confirms the snipe and
    changes the classification to green / snipe.

Red is deliberately narrow:
    It only turns a confirmed green snipe into an incorrect_snipe correction.
    It never means "unidentified".

Fingerprint can be applied from ANY state (including an already-resolved
one) -- it's an override, not a resolution. It doesn't create a snipe/
snope/etc by itself; it just flags the photo for the Special tab, where
Zeke builds whatever actual record(s) the situation calls for.

Manual decisions are permanently protected by the detection layer.
"""

import config
from storage import json_store, user_names
from bot import detection


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

    print(
        f"[reactions] :{reaction}: from {reacting_user} on {channel}/{ts} -> "
        + (
            f"entry found, kind={entry.get('kind')}"
            if entry
            else "NO TRACKED ENTRY for this message"
        ),
        flush=True,
    )

    # ------------------------------------------------------------
    # Zeke's classification reactions
    # ------------------------------------------------------------
    if reacting_user == config.ZEKE_USER_ID and entry:

        # --------------------------------------------------------
        # Fingerprint = special
        #
        # An override, not a resolution -- applies from ANY current
        # state (including an already-resolved one), for cases too
        # niche for the detector to ever handle on its own. Doesn't
        # create a snipe/snope/etc by itself; it just flags the photo
        # for the webapp's Special tab, where Zeke builds whatever
        # actual record(s) the situation calls for (see
        # bot/detection.py's create_special_snipe()).
        # --------------------------------------------------------
        if reaction == config.SPECIAL_EMOJI:
            if entry.get("kind") != "special" and not entry.get("ignored"):
                detection.mark_manual_kind(
                    entry["id"],
                    "special",
                    client,
                )
            return

        # --------------------------------------------------------
        # Red = incorrect snipe
        #
        # ONLY a confirmed green snipe can become incorrect_snipe.
        # This is an irreversible manual decision.
        # --------------------------------------------------------
        if reaction == config.INCORRECT_SNIPE_EMOJI:
            if entry.get("kind") == "snipe" and not entry.get("ignored"):
                detection.mark_incorrect_snipe(entry["id"], client)
            return

        # --------------------------------------------------------
        # Black = not a snipe
        #
        # Pending classifications can be resolved to NAS.
        # Already-NAS entries can be explicitly marked manual.
        # --------------------------------------------------------
        if reaction == config.NOT_A_SNIPE_EMOJI:
            if (
                entry.get("kind") != "incorrect_snipe"
                and not entry.get("ignored")
            ):
                detection.mark_manual_kind(
                    entry["id"],
                    "not_a_snipe",
                    client,
                )
            return

        # --------------------------------------------------------
        # Green = snipe
        # Purple = snope
        #
        # Zeke can manually correct the normal classification.
        # Never allow this to overwrite an incorrect-snipe ruling.
        # --------------------------------------------------------
        if reaction in (
            config.SNIPE_EMOJI,
            config.SNOPE_EMOJI,
        ):
            if (
                entry.get("kind") != "incorrect_snipe"
                and not entry.get("ignored")
            ):
                detection.mark_manual_kind(
                    entry["id"],
                    (
                        "snipe"
                        if reaction == config.SNIPE_EMOJI
                        else "snope"
                    ),
                    client,
                )
            return

        # --------------------------------------------------------
        # Orange = confirm likely snipe
        #
        # IMPORTANT:
        # Orange is normally the BOT'S pending classification.
        # When Zeke reacts orange to a likely_snipe, that means:
        #
        #     "Yes, this is a snipe."
        #
        # Therefore it becomes a confirmed snipe / green, NOT a
        # manually-resolved likely_snipe.
        # --------------------------------------------------------
        if reaction == config.LIKELY_SNIPE_EMOJI:
            if (
                entry.get("kind") == "likely_snipe"
                and not entry.get("ignored")
            ):
                detection.resolve_entry(
                    entry["id"],
                    "snipe",
                    client,
                )
            return

        # --------------------------------------------------------
        # Question = unidentified
        #
        # Zeke can explicitly resolve a pending entry as unidentified.
        # --------------------------------------------------------
        if reaction == config.UNIDENTIFIED_EMOJI:
            if entry.get("kind") in detection.PENDING_KINDS:
                detection.resolve_entry(
                    entry["id"],
                    "unidentified",
                    client,
                )
            elif entry.get("kind") == "unidentified":
                detection.mark_manual_kind(
                    entry["id"],
                    "unidentified",
                    client,
                )
            return

    # ------------------------------------------------------------
    # Star is independent of classification
    # ------------------------------------------------------------
    if reaction == config.STAR_EMOJI:
        _handle_star(
            channel,
            ts,
            reacting_user,
            client,
        )


def _handle_star(channel: str, ts: str, reacting_user: str, client):
    entry = json_store.find_snipe_by_message(channel, ts)

    if not entry:
        return

    user_names.ensure_cached(
        reacting_user,
        client,
    )

    star_users = set(entry.get("star_users", []))
    star_users.add(reacting_user)
    star_users_list = list(star_users)

    notable = (
        config.ZEKE_USER_ID in star_users
    ) or (
        len(star_users - {config.ZEKE_USER_ID})
        >= config.NOTABLE_MIN_OTHER_STARS
    )

    json_store.update_snipe(
        entry["id"],
        star_users=star_users_list,
        notable=notable,
    )

    if notable != entry.get("notable"):
        entry["notable"] = notable

        if (
            entry.get("kind") == "snipe"
            and not entry.get("ignored")
        ):
            try:
                detection._sync_entry_to_log(
                    entry,
                    client,
                )
            except Exception as e:
                print(
                    f"[reactions] failed to sync notable star "
                    f"for {entry['id']}: {e}",
                    flush=True,
                )
