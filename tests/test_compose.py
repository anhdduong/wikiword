import json

import pytest

import urllib.error

from app.compose import (
    definitions_enabled,
    fetch_definition,
    literal_meaning,
)
from app.ground import GroundedMorpheme


def gm(surface, type_, meaning=None):
    return GroundedMorpheme(
        surface=surface, type=type_, origin=None, source_form=None,
        meaning=meaning, verified=bool(meaning), citations=(), notes=None,
    )


MONO = gm("mono", "prefix", "one, single, alone")
LITH = gm("lith", "root", "stone")
IC = gm("ic", "suffix", "relating to, characterized by")


def test_literal_meaning_joins_glosses_in_order():
    assert literal_meaning([MONO, LITH, IC]) == (
        'mono- "one, single, alone" + lith "stone"'
        ' + -ic "relating to, characterized by"'
    )


def test_literal_meaning_marks_unverified_pieces():
    out = literal_meaning([LITH, gm("s", "unknown")])
    assert out == 'lith "stone" + s [unverified]'


def test_literal_meaning_none_without_any_gloss():
    assert literal_meaning([gm("strength", "root"), gm("s", "unknown")]) is None


MW_ENTRY = {
    "meta": {"id": "philosophy"},
    "fl": "noun",
    "def": [{"sseq": [[
        ["sense", {"sn": "1 a", "dt": [
            ["text", "{bc}a discipline comprising primarily"
                     " {d_link|logic|logic} and ethics "],
            ["vis", [{"t": "a degree in {wi}philosophy{/wi}"}]],
        ]}],
    ]]}],
}

MW_DX_ENTRY = {
    "meta": {"id": "telephone"},
    "fl": "noun",
    "def": [{"sseq": [[
        ["bs", {"sense": {"dt": [
            ["text", "{bc}a device for talking at a distance"
                     " {dx_def}see {dxt|receiver|receiver|d(1)}{/dx_def}"
                     " {bc}{sx|phone||} "],
        ]}}],
    ]]}],
}

# A sense that is *entirely* a cross reference, no other prose (e.g. "more"
# -> {sx|greater|...}) — must flatten to the target word, not vanish.
MW_PURE_XREF_ENTRY = {
    "meta": {"id": "more"},
    "fl": "adjective",
    "def": [{"sseq": [[
        ["sense", {"sn": "1", "dt": [
            ["text", "{bc}{sx|greater|great:1|} "],
        ]}],
    ]]}],
}


def test_fetch_definition_returns_first_sense(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    assert fetch_definition(
        "philosophy", http_get=lambda url: (200, json.dumps([MW_ENTRY]).encode())
    ) == ("(noun) a discipline comprising primarily logic and ethics", True)


def test_fetch_definition_flattens_cross_reference_tokens(monkeypatch):
    # Cross-ref tokens (dx/dxt/sx) are flattened to their display text, not
    # dropped — dropping risks an empty definition (see the pure-xref test
    # below), so a little awkward phrasing beats missing data.
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    assert fetch_definition(
        "telephone", http_get=lambda url: (200, json.dumps([MW_DX_ENTRY]).encode())
    ) == (
        "(noun) a device for talking at a distance see receiver phone", True
    )


def test_fetch_definition_pure_cross_reference_sense_is_not_empty(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    assert fetch_definition(
        "more", http_get=lambda url: (200, json.dumps([MW_PURE_XREF_ENTRY]).encode())
    ) == ("(adjective) greater", True)


def test_fetch_definition_spelling_suggestions_is_definitive_miss(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    body = json.dumps(["nonsense", "no-nonsense"]).encode()
    assert fetch_definition("qzxvqx", http_get=lambda url: (200, body)) == (None, True)


def test_fetch_definition_empty_array_is_definitive_miss(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    assert fetch_definition(
        "qzxvqx", http_get=lambda url: (200, b"[]")
    ) == (None, True)


def test_fetch_definition_no_key_configured_is_definitive(monkeypatch):
    monkeypatch.delenv("WIKIWORD_MW_API_KEY", raising=False)
    called = []
    assert fetch_definition(
        "word", http_get=lambda url: called.append(url)
    ) == (None, True)
    assert called == []  # disabled feature: no network spend


def test_fetch_definition_5xx_is_not_definitive(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")
    assert fetch_definition("word", http_get=lambda url: (500, b"")) == (None, False)


def test_fetch_definition_transport_failure_is_not_definitive(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "testkey")

    def broken(url):
        raise urllib.error.URLError("offline")

    assert fetch_definition("word", http_get=broken) == (None, False)


def test_fetch_definition_bad_key_response_is_not_definitive(monkeypatch):
    # An invalid/revoked key comes back as a 200 plain-text error, not JSON
    # — must not be cached as a silent, permanent "no definition".
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "badkey")
    assert fetch_definition(
        "word",
        http_get=lambda url: (200, b"Invalid API key. Not subscribed for this reference."),
    ) == (None, False)


def test_model_version_covers_compose():
    from app import version

    v1 = version.model_version()
    original = version.compose.COMPOSE_VERSION
    try:
        version.compose.COMPOSE_VERSION = original + "-tweaked"
        assert version.model_version() != v1
    finally:
        version.compose.COMPOSE_VERSION = original
    assert version.model_version() == v1


# --- inflected headwords ----------------------------------------------------
# said, were, brought and these are ordinary MW entries that carry no "def",
# only a cross-reference to the base form. Reading def alone reported "no
# definition" for some of the commonest words in English.

MW_INFLECTION_ENTRY = {
    "meta": {"id": "said:1"},
    "hwi": {"hw": "said"},
    "cxs": [{"cxl": "past tense and past participle of",
             "cxtis": [{"cxt": "say"}]}],
}

MW_HOMOGRAPH_XREF_ENTRY = {
    "meta": {"id": "goes"},
    "hwi": {"hw": "goes"},
    "cxs": [{"cxl": "present tense third-person singular of",
             "cxtis": [{"cxt": "go:1"}]}],
}


def test_inflected_entry_falls_back_to_its_cross_reference(monkeypatch):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "k")
    assert fetch_definition(
        "said",
        http_get=lambda url: (200, json.dumps([MW_INFLECTION_ENTRY]).encode()),
    ) == ("past tense and past participle of say", True)


def test_cross_reference_strips_the_homograph_id(monkeypatch):
    # "of go:1" is MW's internal bookkeeping and must not reach the page.
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "k")
    definition, _ = fetch_definition(
        "goes",
        http_get=lambda url: (200,
                              json.dumps([MW_HOMOGRAPH_XREF_ENTRY]).encode()),
    )
    assert definition == "present tense third-person singular of go"


@pytest.mark.parametrize("cxs", [
    [],
    [{"cxl": "past tense of"}],                    # no target
    [{"cxtis": [{"cxt": "say"}]}],                 # no label
    [{"cxl": "past tense of", "cxtis": []}],
    "not-a-list",
])
def test_malformed_cross_reference_is_a_clean_miss(monkeypatch, cxs):
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "k")
    entry = {"meta": {"id": "x"}, "hwi": {"hw": "x"}, "cxs": cxs}
    assert fetch_definition(
        "x", http_get=lambda url: (200, json.dumps([entry]).encode())
    ) == (None, True)


def test_definitions_enabled_tracks_the_key(monkeypatch):
    monkeypatch.delenv("WIKIWORD_MW_API_KEY", raising=False)
    assert definitions_enabled() is False
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "k")
    assert definitions_enabled() is True


MW_SX_HOMOGRAPH_ENTRY = {
    "meta": {"id": "drilling"},
    "hwi": {"hw": "drilling"},
    "fl": "noun",
    "def": [{"sseq": [[["sense", {"dt": [["text", "{bc}{sx|drill:6||}"]]}]]]}],
}


def test_sense_cross_reference_drops_the_homograph_id(monkeypatch):
    # Shipped as "(noun) drill:6" — MW's internal sense id on the page.
    monkeypatch.setenv("WIKIWORD_MW_API_KEY", "k")
    definition, _ = fetch_definition(
        "drilling",
        http_get=lambda url: (200,
                              json.dumps([MW_SX_HOMOGRAPH_ENTRY]).encode()),
    )
    assert definition == "(noun) drill"


@pytest.mark.parametrize("text,expected", [
    ("mixed in a 3:1 ratio", "mixed in a 3:1 ratio"),   # prose keeps colons
    ("{bc}{sx|drill:6||}", "drill"),
    ("{bc}{sx|go:1||} quickly", "go quickly"),
    ("{bc}a {it}single{/it} stone", "a single stone"),
])
def test_clean_only_strips_ids_inside_tokens(text, expected):
    from app.compose import _clean

    assert _clean(text) == expected
