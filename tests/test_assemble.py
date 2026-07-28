import json

import pytest

from app.assemble import (
    ASSEMBLE_SYSTEM,
    assemble,
    build_user_prompt,
    has_facts,
)
from app.ground import GroundedMorpheme


def gm(surface, type_, origin=None, source_form=None, meaning=None,
       verified=False):
    return GroundedMorpheme(
        surface=surface, type=type_, origin=origin, source_form=source_form,
        meaning=meaning, verified=verified, citations=(), notes=None,
    )


MONO = gm("mono", "prefix", "Ancient Greek", "monos", "one, single, alone", True)
LITH = gm("lith", "root", "Ancient Greek", "lithos", "stone", True)
IC = gm("ic", "suffix", "Latin/Greek", "-icus/-ikos",
        "relating to, characterized by", True)
UNKNOWN_S = gm("s", "unknown")

GOOD = json.dumps({"literal_meaning": "Relating to a single stone."})


def fake_call(response_text):
    calls = []

    def call(system, user):
        calls.append((system, user))
        return response_text

    call.calls = calls
    return call


def test_assemble_returns_literal_meaning():
    call = fake_call(GOOD)
    result = assemble("monolithic", [MONO, LITH, IC], ["From French monolithique."],
                      call=call)
    assert result == "Relating to a single stone."


def test_prompt_carries_constraint_and_facts():
    call = fake_call(GOOD)
    assemble("monolithic", [MONO, LITH, IC], ["From French monolithique."],
             call=call)
    system, user = call.calls[0]
    # Plan §4 constraint, verbatim intent, version-controlled.
    assert "Synthesize only from the facts provided" in system
    assert "Do not invent content" in system
    assert "one, single, alone" in user
    assert "Ancient Greek" in user
    assert "monos" in user
    assert "From French monolithique." in user


def test_unverified_morphemes_are_marked():
    prompt = build_user_prompt("strengths", [gm("strength", "root"), UNKNOWN_S], [])
    assert "[unverified]" in prompt
    assert "no confirmed meaning" in prompt


def test_no_facts_skips_llm_entirely():
    call = fake_call(GOOD)
    result = assemble("strengths", [gm("strength", "root"), UNKNOWN_S], [],
                      call=call)
    assert result is None
    assert call.calls == []  # nothing to synthesize from; no API spend
    assert not has_facts([gm("strength", "root"), UNKNOWN_S])
    assert has_facts([MONO])


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"modern_usage": "x"}),  # missing literal_meaning
])
def test_invalid_response_returns_none(bad):
    assert assemble("monolithic", [MONO], [], call=fake_call(bad)) is None


def test_transport_error_returns_none():
    def broken(system, user):
        raise RuntimeError("api down")

    assert assemble("monolithic", [MONO], [], call=broken) is None


def test_model_version_covers_assemble_prompt():
    from app import version

    v1 = version.model_version()
    original = version.assemble.ASSEMBLE_SYSTEM
    try:
        version.assemble.ASSEMBLE_SYSTEM = original + " tweaked"
        assert version.model_version() != v1
    finally:
        version.assemble.ASSEMBLE_SYSTEM = original
    assert version.model_version() == v1


# --- the grounding check ----------------------------------------------------
# A prompt rule is not enforcement. Asked for "environment" (environ
# [unverified] + -ment "result or means of an action") the model answered
# "Result or means of surrounding." — right, and sourced from nothing it was
# given. Every content word must trace back to a supplied fact.

ENVIRON = gm("environ", "unknown")
MENT = gm("ment", "suffix", "Latin", "-mentum", "result or means of an action",
          True)
ENV_TEXT = ["From Middle French environnement. By surface analysis,"
            " environ + -ment."]


def test_invented_meaning_for_an_unverified_morpheme_is_dropped():
    call = fake_call(json.dumps(
        {"literal_meaning": "Result or means of surrounding."}))
    assert assemble("environment", [ENVIRON, MENT], ENV_TEXT, call) is None


def test_meaning_grounded_in_the_fetched_text_survives():
    # Same shape, but now the retrieved prose states environ's sense, so the
    # synthesis is sourced and must be kept.
    texts = ['From Old French environ ("around, surrounding"), + -ment.']
    call = fake_call(json.dumps(
        {"literal_meaning": "Result or means of surrounding."}))
    assert assemble("environment", [ENVIRON, MENT], texts, call) == (
        "Result or means of surrounding.")


def test_unverified_marker_never_leaks_into_prose():
    # Seen in production: 'State or quality of not [unverified].'
    call = fake_call(json.dumps(
        {"literal_meaning": "State or quality of not [unverified]."}))
    assert assemble("insurance", [gm("sur", "unknown"), MENT], [], call) is None


def test_inflected_forms_of_a_supplied_fact_count_as_grounded():
    # "stones" must not read as invented just because the table says "stone".
    call = fake_call(json.dumps({"literal_meaning": "Made of single stones."}))
    assert assemble("monolithic", [MONO, LITH], [], call) == (
        "Made of single stones.")


def test_a_meaning_that_contradicts_the_given_gloss_is_dropped():
    # along: a- is supplied as "not, without"; "against" is the model's own.
    a = gm("a", "prefix", "Latin", "a-", "not, without", True)
    call = fake_call(json.dumps({"literal_meaning": "Against long."}))
    assert assemble("along", [a, gm("long", "word")], [], call) is None


@pytest.mark.parametrize("meaning", [
    "Relating to a single stone.",
    "One who is characterized by a single stone.",
    "The state or quality of being single.",
])
def test_ordinary_syntheses_are_not_rejected(meaning):
    call = fake_call(json.dumps({"literal_meaning": meaning}))
    assert assemble("monolithic", [MONO, LITH, IC], [], call) == meaning
