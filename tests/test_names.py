import pytest

from snipesbot.services import entries as svc, people, sync
from snipesbot.store.misc import Names
from snipesbot.web import create_app
from conftest import ALICE, BOB, CAROL, ZEKE, photo_msg, text_msg

H = {"X-Requested-With": "snipesbot"}


# ---- display ------------------------------------------------------------------------------

def test_display_is_the_name_alone_with_sensible_fallbacks():
    names = Names({ALICE: "Alice"})
    assert people.display(ALICE, names) == "Alice"
    assert people.display("U0UNKNOWN1", names) == "Unknown (U0UNKNOWN1)"        # a real id we can't resolve (yet)
    assert people.display("deer", names) == "deer"                              # free text passes through
    assert people.display(None, names) == "" and people.display("", names) == ""


def test_two_people_with_the_same_name_are_told_apart():
    names = Names({ALICE: "Sam Lee", BOB: "sam lee", CAROL: "Carol"})
    assert people.display(ALICE, names) == "Sam Lee (E001)" and people.display(BOB, names) == "sam lee (0001)"
    assert people.display(CAROL, names) == "Carol"


# ---- text ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (f"<@{ALICE}> snipe", "@Alice snipe"),
    (f"<@{ALICE}|alice> hi", "@Alice hi"),
    ("see <#C0123ABCD|general> now", "see #general now"),
    ("<!here> and <!channel> and <!everyone>", "@here and @channel and @everyone"),
    ("<!subteam^S123|@astro-team> meet", "@astro-team meet"),
    ("on <!date^1788000000^{date}|Aug 29> ok", "on Aug 29 ok"),
    ("read <https://example.com/x|the post> and <https://a.b/c> or <mailto:a@b.c|mail me>",
     "read the post and https://a.b/c or mail me"),
    ("a &amp; b &lt;3 &gt; c", "a & b <3 > c"),
    ("&amp;lt; stays one level deep", "&lt; stays one level deep"),
    (f"<@{BOB}> is unknown", "@Unknown (UBOB00001) is unknown"),
    (None, ""),
])
def test_slack_text_becomes_readable(raw, expected):
    assert people.render_text(raw, Names({ALICE: "Alice"})) == expected


def test_decoded_text_is_plain_text_and_never_html():
    out = people.render_text("&lt;img src=x onerror=alert(1)&gt;", Names())
    assert out == "<img src=x onerror=alert(1)>"       # plain text; the UI's escaping is what makes it safe


# ---- lookup ---------------------------------------------------------------------------------------

def test_ensure_names_is_bounded_cached_and_remembers_failures(env, slack):
    env.store.names.set(ALICE, "Alice")
    slack.users["UGHOST0001"] = "Ghost"
    del slack.users[CAROL]                                           # lookups for Carol will fail
    people.ensure_names(env, [ALICE, BOB, CAROL, "UGHOST0001", "deer"])
    assert env.store.names.get(BOB) == "Bob" and env.store.names.get("UGHOST0001") == "Ghost"
    assert env.store.names.get(CAROL) is None
    calls = slack.count("users_info")
    people.ensure_names(env, [ALICE, BOB, CAROL])                    # nothing new to fetch; Carol's failure is remembered
    assert slack.count("users_info") == calls


def test_ensure_names_respects_its_limit(env, slack):
    ids = [f"UMANY{i:05d}" for i in range(30)]
    slack.users.update({i: f"n{i}" for i in ids})
    assert people.ensure_names(env, ids, limit=5) == 5 and slack.count("users_info") == 5


def test_directory_refresh_caches_everyone_across_pages_and_is_rate_limited_to_daily(env, slack):
    slack.users_page_size = 2
    assert people.refresh_directory(env) == 4 and slack.count("users_list") == 2     # 4 members over 2 pages
    assert env.store.names.all() == {ALICE: "Alice", BOB: "Bob", CAROL: "Carol", ZEKE: "Zeke"}
    assert people.refresh_directory(env) == 0 and slack.count("users_list") == 2     # within a day: no calls
    assert people.refresh_directory(env, force=True) == 4


def test_a_failed_directory_refresh_changes_nothing_and_retries_next_time(env, slack):
    slack.fail = {"users_list"}
    assert people.refresh_directory(env) == 0 and env.store.settings.get(people.DIRECTORY_KEY) is None
    slack.fail = set()
    assert people.refresh_directory(env) == 4


def test_every_sync_refreshes_the_directory_first(env, slack, no_download):
    slack.history.append(photo_msg("1788000000.000100", CAROL, f"<@{ALICE}> snipe"))
    stats = sync.run_sync(env, progress=lambda _l: None)
    assert stats.names == 4 and env.store.names.get(ALICE) == "Alice"


# ---- end to end: no raw ids reach the UI --------------------------------------------------------------

@pytest.fixture
def client(env, no_download):
    app = create_app(env)
    app.testing = True
    return app.test_client()


def test_cards_context_and_replies_all_show_names_not_ids(env, client):
    from snipesbot.services import archiver
    msg = photo_msg("1788000500.000100", CAROL, f"<@{ALICE}> snipe &amp; <@{BOB}>")
    archiver.archive_message(env, msg)
    svc.record_photo(env, msg)
    env.store.archive.save_message(text_msg("1788000400.000100", BOB, f"hey <@{ZEKE}> look <#C0123ABCD|general>"))
    env.store.archive.save_message(text_msg("1788000501.000100", ALICE, f"<@{CAROL}> fine", thread_ts=msg["ts"]))
    e = client.get("/api/entries").get_json()["entries"][0]
    assert e["sniper"] == "Carol" and e["sniped_display"] == ["Alice", "Bob"]
    assert e["caption"] == "@Alice snipe & @Bob"
    ctx = client.get(f"/api/entries/{e['id']}/context").get_json()
    assert ctx["before"][0]["user"] == "Bob" and ctx["before"][0]["text"] == "hey @Zeke look #general"
    reply = client.get(f"/api/entries/{e['id']}/replies").get_json()["replies"][0]
    assert reply["user"] == "Alice" and reply["text"] == "@Carol fine"
    shown = [e["sniper"], e["caption"], *e["sniped_display"], ctx["before"][0]["user"], ctx["before"][0]["text"],
             reply["user"], reply["text"]]
    assert not [t for t in shown for uid in (ALICE, BOB, CAROL, ZEKE) if uid in t]      # ids live only in the *_id fields


def test_mentions_of_people_never_seen_before_are_looked_up_not_shown_raw(env, client, slack):
    slack.users["UNEWBIE001"] = "Newbie"
    svc.record_photo(env, photo_msg("1788000000.000100", CAROL, "<@UNEWBIE001> hello"))
    assert client.get("/api/entries").get_json()["entries"][0]["caption"] == "@Newbie hello"


def test_slack_cards_keep_slacks_own_text_so_slack_renders_it(env):
    from snipesbot.services import review_cards
    import time
    base = time.time()
    env.store.archive.save_message(text_msg(f"{base - 5:.6f}", ALICE, f"<@{BOB}> a &lt; b &amp; c"))
    e = svc.record_photo(env, photo_msg(f"{base:.6f}", CAROL, "who"))[0]
    text = str(review_cards.build_blocks(env, e))
    assert f"<@{BOB}> a &lt; b &amp; c" in text            # not decoded: decoding would let '<' be read as markup by Slack


def test_exports_and_cli_use_names(env, client):
    from snipesbot.services import exports
    from openpyxl import load_workbook
    import io
    env.store.names.set_many({ALICE: "Alice", CAROL: "Carol"})
    svc.record_photo(env, photo_msg("1788000000.000100", CAROL, f"<@{ALICE}> snipe"))
    ws = load_workbook(io.BytesIO(exports.build_xlsx(env))).active
    row = list(ws.iter_rows(values_only=True))[1]
    assert row[2] == "Carol" and row[3] == "Alice"
