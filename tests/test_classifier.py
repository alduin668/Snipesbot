import pytest

from snipesbot import classifier as c
from snipesbot.models import Kind

SNIPE, SNOPE = ("snipe", "sniped", "snipes", "sniping"), ("snope", "snoped", "snopes", "snoping")


def cls(text, sniper="U9SNIPER1"):
    return c.classify(text, sniper, snipe_words=SNIPE, snope_words=SNOPE)


@pytest.mark.parametrize("text, kind, sniped", [
    ("<@UALICE001> snipe", Kind.SNIPE, ("UALICE001",)),
    ("sniped <@UALICE001> and <@UBOB00001>", Kind.SNIPE, ("UALICE001", "UBOB00001")),
    ("<@UALICE001> SNIPED!!", Kind.SNIPE, ("UALICE001",)),
    ("<@UALICE001> snope", Kind.SNOPE, ("UALICE001",)),
    ("snope", Kind.SNOPE, ()),                                  # a snope needs no mention
    ("<@UALICE001> snipe snope", Kind.SNOPE, ("UALICE001",)),   # snope always wins
    ("<@UALICE001>", Kind.LIKELY_SNIPE, ("UALICE001",)),
    ("<@UALICE001> <@UBOB00001>", Kind.LIKELY_SNIPE, ("UALICE001", "UBOB00001")),
    ("<@UALICE001> nice one", Kind.UNIDENTIFIED, ()),           # mention + ordinary text
    ("snipe", Kind.UNIDENTIFIED, ()),                           # keyword but nobody tagged
    ("just a photo", Kind.UNIDENTIFIED, ()),
    ("", Kind.UNIDENTIFIED, ()),
])
def test_classification(text, kind, sniped):
    result = cls(text)
    assert (result.kind, result.sniped) == (kind, sniped)


@pytest.mark.parametrize("text", [
    "<@UALICE001> not a snipe",
    "<@UALICE001> no snipe lol",
    "<@UALICE001> that wasn't a snipe",
    "<@UALICE001> isn’t a snipe",          # curly apostrophe from Slack
    "<@UALICE001> never snipe",
])
def test_negated_keywords_do_not_auto_confirm(text):
    assert cls(text).kind is Kind.UNIDENTIFIED


@pytest.mark.parametrize("text", ["<@UALICE001> sniper", "<@UALICE001> snipey", "<@UALICE001> unsnipeable"])
def test_keywords_match_whole_words_only(text):
    assert cls(text).kind is Kind.UNIDENTIFIED


def test_a_mention_id_never_matches_a_keyword():
    assert cls("<@USNIPE001>").kind is Kind.LIKELY_SNIPE


def test_sniper_is_excluded_from_their_own_victims():
    result = cls("<@U9SNIPER1> <@UALICE001> snipe", sniper="U9SNIPER1")
    assert result.sniped == ("UALICE001",)
    assert cls("<@U9SNIPER1> snipe", sniper="U9SNIPER1").kind is Kind.UNIDENTIFIED


def test_old_style_mentions_with_names_are_understood():
    assert c.extract_mentions("<@UALICE001|alice> hi") == ["UALICE001"]


def test_mentions_are_distinct_and_ordered():
    assert c.extract_mentions("<@UBOB00001> <@UALICE001> <@UBOB00001>") == ["UBOB00001", "UALICE001"]


def test_guessed_sniped_prefers_caption_then_context():
    assert c.guessed_sniped("<@UALICE001>", "U9") == ["UALICE001"]
    assert c.guessed_sniped("hmm", "U9", ["lol <@UBOB00001>"]) == ["UBOB00001"]
    assert c.guessed_sniped("hmm", "U9", ["nothing"]) == []


@pytest.mark.parametrize("text, expected", [
    ("#snipe nas", True), ("NAS", True), ("#snipe Jonas", False), ("#snipe nasty", False), ("", False),
])
def test_says_nas_is_a_whole_word(text, expected):
    assert c.says_nas(text) is expected
