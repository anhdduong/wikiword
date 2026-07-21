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
