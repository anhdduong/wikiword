import json

import urllib.error

from app.compose import fetch_definition, literal_meaning
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


DICT_BODY = json.dumps([{
    "word": "monolithic",
    "meanings": [{
        "partOfSpeech": "adjective",
        "definitions": [{"definition": "Massive, solid, and uniform."}],
    }],
}]).encode()


def test_fetch_definition_returns_first_definition():
    assert fetch_definition(
        "monolithic", http_get=lambda url: (200, DICT_BODY)
    ) == ("(adjective) Massive, solid, and uniform.", True)


def test_fetch_definition_404_is_definitive_miss():
    assert fetch_definition("qzxvqx", http_get=lambda url: (404, b"")) == (None, True)


def test_fetch_definition_5xx_is_not_definitive():
    assert fetch_definition("word", http_get=lambda url: (500, b"")) == (None, False)


def test_fetch_definition_transport_failure_is_not_definitive():
    def broken(url):
        raise urllib.error.URLError("offline")

    assert fetch_definition("word", http_get=broken) == (None, False)


def test_fetch_definition_bad_json_is_definitive_empty():
    assert fetch_definition(
        "word", http_get=lambda url: (200, b"not json")
    ) == (None, True)


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
