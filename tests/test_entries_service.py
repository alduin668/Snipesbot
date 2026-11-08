import pytest

from snipesbot.models import Kind
from snipesbot.services import entries as svc
from conftest import ALICE, BOB, BOT, CAROL, CHANNEL, ZEKE, photo_msg, text_msg

TS = "1788000000.000100"


def record(env, caption, ts=TS, user=CAROL, **kw):
    entry, created = svc.record_photo(env, photo_msg(ts, user, caption), **kw)
    return entry


def bot_reactions(slack, ts=TS):
    return sorted(n for n, u in slack.reactions.get((CHANNEL, ts), []) if u == BOT)


# ---- recording ---------------------------------------------------------------------

@pytest.mark.parametrize("caption, kind, emoji", [
    (f"<@{ALICE}> snipe", Kind.SNIPE, "large_green_circle"),
    (f"<@{ALICE}> snope", Kind.SNOPE, "large_purple_circle"),
    (f"<@{ALICE}>", Kind.LIKELY_SNIPE, "large_orange_circle"),
    ("no one tagged", Kind.UNIDENTIFIED, "question"),
])
def test_new_photo_is_classified_and_gets_the_matching_reaction(env, slack, caption, kind, emoji):
    entry = record(env, caption)
    assert entry.kind is kind and not entry.manually_resolved
    assert bot_reactions(slack) == [emoji]
    assert entry.reacted_kind is kind


def test_messages_without_media_or_user_are_ignored(env):
    assert svc.record_photo(env, text_msg(TS, CAROL, "hi")) == (None, False)
    assert svc.record_photo(env, dict(photo_msg(TS, CAROL), user=None)) == (None, False)


def test_recording_twice_is_idempotent_and_refreshes_the_caption(env, slack):
    first = record(env, "before")
    again, created = svc.record_photo(env, photo_msg(TS, CAROL, "after"))
    assert not created and again.id == first.id and again.caption == "after"
    assert env.store.entries.count() == 1


def test_multiple_attachments_keep_the_first_as_primary(env):
    msg = photo_msg(TS, CAROL, "x")
    msg["files"].append({"id": "F2", "name": "v.mp4", "mimetype": "video/mp4"})
    entry, _ = svc.record_photo(env, msg)
    assert entry.file.id.startswith("F") and entry.extra_files[0].is_video


# ---- the single transition path -------------------------------------------------------

def test_apply_state_replaces_the_bots_old_reaction_only(env, slack):
    entry = record(env, f"<@{ALICE}>")
    slack.reactions[(CHANNEL, TS)].append(("tada", "USOMEONE1"))        # somebody else's reaction
    svc.apply_state(env, entry.id, Kind.SNIPE)
    assert bot_reactions(slack) == ["large_green_circle"]
    assert ("tada", "USOMEONE1") in slack.reactions[(CHANNEL, TS)]


def test_a_snipe_needs_someone_sniped(env):
    entry = record(env, "nothing")
    with pytest.raises(svc.InvalidTransition):
        svc.apply_state(env, entry.id, Kind.SNIPE)
    assert env.store.entries.get(entry.id).kind is Kind.UNIDENTIFIED     # nothing was half-applied


def test_apply_state_defaults_sniped_from_caption_mentions(env):
    entry = record(env, f"hmm <@{ALICE}> and <@{BOB}>")
    assert entry.kind is Kind.UNIDENTIFIED
    assert svc.apply_state(env, entry.id, Kind.SNIPE).sniped == (ALICE, BOB)


def test_moving_to_a_non_snipe_kind_clears_sniped(env):
    entry = record(env, f"<@{ALICE}> snipe")
    assert svc.set_kind(env, entry.id, Kind.NOT_A_SNIPE).sniped == ()


def test_unknown_entry_raises_a_specific_error(env):
    with pytest.raises(svc.EntryNotFound):
        svc.apply_state(env, "nope", Kind.SNOPE)


def test_a_slack_failure_never_undoes_the_decision_and_is_repaired_later(env, slack):
    entry = record(env, "nothing")
    slack.fail = {"reactions_add", "reactions_get"}
    result = svc.apply_state(env, entry.id, Kind.NOT_A_SNIPE)
    assert result.kind is Kind.NOT_A_SNIPE and result.reacted_kind is Kind.UNIDENTIFIED   # stale marker
    slack.fail = set()
    fixed = svc.reclassify(env, result, result.caption)          # any later pass repairs it
    assert fixed.reacted_kind is Kind.NOT_A_SNIPE and bot_reactions(slack) == ["black_circle"]


def test_failed_reactions_lookup_costs_one_extra_call_not_a_fan_out(env, slack):
    entry = record(env, "nothing")
    slack.calls.clear()
    slack.fail = {"reactions_get"}
    svc.apply_state(env, entry.id, Kind.SNOPE, sniped=())
    assert slack.calls.count("reactions_remove") == 0 and len(slack.calls) <= 3


# ---- edits / manual locking --------------------------------------------------------------

def test_editing_a_caption_reclassifies_automatic_entries(env, slack):
    record(env, "oops no tag")
    entry = svc.handle_edit(env, CHANNEL, TS, f"<@{ALICE}> snipe")
    assert entry.kind is Kind.SNIPE and bot_reactions(slack) == ["large_green_circle"]
    assert entry.caption == f"<@{ALICE}> snipe"


def test_manual_decisions_survive_edits_but_the_caption_is_refreshed(env):
    entry = record(env, f"<@{ALICE}> snipe")
    svc.apply_state(env, entry.id, Kind.NOT_A_SNIPE)
    after = svc.handle_edit(env, CHANNEL, TS, f"<@{ALICE}> snope")
    assert after.kind is Kind.NOT_A_SNIPE and after.manually_resolved and after.caption.endswith("snope")


def test_editing_an_unknown_or_deleted_message_does_nothing(env):
    assert svc.handle_edit(env, CHANNEL, "9.9", "x") is None
    record(env, "x")
    svc.handle_deleted(env, CHANNEL, TS)
    assert svc.handle_edit(env, CHANNEL, TS, "<@%s> snipe" % ALICE) is None


# ---- resolving pending photos --------------------------------------------------------------

def test_resolve_pending_only_works_on_pending_resolvable(env):
    entry = record(env, f"<@{ALICE}>")
    assert svc.resolve_pending(env, entry.id, Kind.SPECIAL) is None
    done = svc.resolve_pending(env, entry.id, Kind.SNIPE)
    assert done.kind is Kind.SNIPE and done.manually_resolved
    assert svc.resolve_pending(env, entry.id, Kind.SNOPE) is None      # someone already resolved it


def test_thread_reply_snipe_resolves_with_the_mentioned_users(env):
    record(env, "who is this")
    assert svc.resolve_via_thread_reply(env, CHANNEL, TS, f"#snipe <@{ALICE}>")
    e = env.store.entries.get_by_message(CHANNEL, TS)
    assert e.kind is Kind.SNIPE and e.sniped == (ALICE,) and e.manually_resolved


def test_thread_reply_nas_is_a_whole_word_match(env):
    record(env, "who is this")
    assert not svc.resolve_via_thread_reply(env, CHANNEL, TS, "#snipe Jonas")     # was a false NAS before
    assert svc.resolve_via_thread_reply(env, CHANNEL, TS, "#snipe nas")
    assert env.store.entries.get_by_message(CHANNEL, TS).kind is Kind.NOT_A_SNIPE


def test_thread_reply_that_settles_nothing_does_not_lock_the_entry(env):
    record(env, "who is this")
    assert not svc.resolve_via_thread_reply(env, CHANNEL, TS, "#snipe")          # no mention at all
    e = env.store.entries.get_by_message(CHANNEL, TS)
    assert e.kind is Kind.UNIDENTIFIED and not e.manually_resolved               # still reviewable/auto


def test_thread_reply_never_overrides_a_manual_decision(env):
    entry = record(env, "x")
    svc.apply_state(env, entry.id, Kind.SNOPE, sniped=())
    assert not svc.resolve_via_thread_reply(env, CHANNEL, TS, f"#snipe <@{ALICE}>")


# ---- stars -----------------------------------------------------------------------------------

def test_notable_when_zeke_stars_or_enough_others_do(env):
    record(env, "x")
    assert not svc.record_star(env, CHANNEL, TS, ALICE, True).notable
    assert svc.record_star(env, CHANNEL, TS, ZEKE, True).notable
    assert not svc.record_star(env, CHANNEL, TS, ZEKE, False).notable            # un-starring now works
    for u in ("U00000001", "U00000002", "U00000003"):
        svc.record_star(env, CHANNEL, TS, u, True)
    assert env.store.entries.get_by_message(CHANNEL, TS).notable


def test_manual_notable_override_beats_stars_and_can_be_cleared(env):
    entry = record(env, "x")
    svc.record_star(env, CHANNEL, TS, ZEKE, True)
    assert not svc.set_notable(env, entry.id, False).notable
    svc.record_star(env, CHANNEL, TS, ALICE, True)                               # a later star can't undo it
    assert not env.store.entries.get(entry.id).notable
    assert svc.set_notable(env, entry.id, None).notable


# ---- special -----------------------------------------------------------------------------------

def test_special_children_accept_free_text_names_and_share_the_photo(env, slack):
    parent = record(env, "a deer?")
    svc.apply_state(env, parent.id, Kind.SPECIAL, sniped=())
    child = svc.create_special(env, parent.id, Kind.SNIPE, "deer", [ALICE])
    assert child.derived_from == parent.id and child.sniper == "deer" and child.file == parent.file
    assert child.manually_resolved and child.reacted_kind is None
    assert bot_reactions(slack) == ["fingerprint"]          # children never touch the Slack reaction
    assert env.store.entries.get_by_message(CHANNEL, TS).id == parent.id


@pytest.mark.parametrize("kind, sniper, sniped", [
    (Kind.SNIPE, "deer", []), (Kind.SNOPE, "deer", ["  "]), (Kind.SNIPE, " ", [ALICE]), (Kind.SPECIAL, "x", [ALICE]),
])
def test_special_creation_rejects_nonsense(env, kind, sniper, sniped):
    parent = record(env, "x")
    assert svc.create_special(env, parent.id, kind, sniper, sniped) is None


def test_dismissing_a_special_photo_is_a_nas(env):
    parent = record(env, "x")
    svc.apply_state(env, parent.id, Kind.SPECIAL, sniped=())
    assert svc.dismiss_special(env, parent.id).kind is Kind.NOT_A_SNIPE


# ---- do not snipe ---------------------------------------------------------------------------------

def test_dns_warning_is_posted_once_per_snipe_and_target(env, slack):
    env.store.dns.add(ALICE)
    entry = record(env, f"<@{ALICE}> snipe")
    svc.apply_state(env, entry.id, Kind.SNIPE)                                    # re-applying must not re-warn
    svc.reclassify(env, env.store.entries.get(entry.id), entry.caption)
    warnings = [p for p in slack.posted if p.get("thread_ts") == TS]
    dms = [p for p in slack.posted if p["channel"] == CAROL]
    assert len(warnings) == 1 and len(dms) == 1 and "do-not-snipe" in dms[0]["text"]


def test_dns_ignores_free_text_names_and_unlisted_users(env, slack):
    env.store.dns.add("deer")
    parent = record(env, "x")
    svc.apply_state(env, parent.id, Kind.SPECIAL, sniped=())
    svc.create_special(env, parent.id, Kind.SNIPE, "deer", ["deer"])
    svc.create_special(env, parent.id, Kind.SNIPE, CAROL, [BOB])
    assert slack.posted == []


def test_a_failed_warning_releases_its_claim_so_it_can_retry(env, slack):
    env.store.dns.add(ALICE)
    slack.fail = {"chat_postMessage"}
    record(env, f"<@{ALICE}> snipe")
    assert env.store.dns.reserve_warning(CHANNEL, TS, ALICE, CAROL)               # claim was released


# ---- deletion ---------------------------------------------------------------------------------------

def test_deleting_the_slack_message_removes_the_snipe_everywhere(env, slack, no_download):
    from snipesbot.services import archiver
    env.store.dns.add(ALICE)
    msg = photo_msg(TS, CAROL, f"<@{ALICE}> snipe")
    archiver.archive_message(env, msg)
    entry, _ = svc.record_photo(env, msg)
    fid = entry.file.id
    assert env.store.archive.has_file(fid)

    svc.handle_deleted(env, CHANNEL, TS)

    assert env.store.entries.get(entry.id).deleted
    assert env.store.entries.find() == []                                          # off every list and the score
    assert not env.store.archive.has_file(fid)                                     # photo purged from disk
    assert any(d[0] == CHANNEL for d in slack.deleted)                             # bot's warning reply removed
    assert env.store.archive.before(CHANNEL, "1789000000.000000", 5) == []         # gone from context too


def test_files_are_kept_on_delete_when_purging_is_turned_off(env, cfg, slack, no_download):
    from snipesbot.services import archiver
    env.cfg = cfg.with_(purge_files_on_delete=False)
    msg = photo_msg(TS, CAROL, "x")
    archiver.archive_message(env, msg)
    entry, _ = svc.record_photo(env, msg)
    svc.handle_deleted(env, CHANNEL, TS)
    assert env.store.archive.has_file(entry.file.id) and env.store.entries.get(entry.id).deleted
