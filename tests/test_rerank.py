import json

import pytest

from app.db import connect, migrate
from app.lexicon import load_lexicon
from app.seed import seed
from app.segment import segment
from app.rerank import RERANK_SYSTEM, build_user_prompt, rerank


@pytest.fixture(scope="module")
def lex(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("db") / "test.db")
    migrate(conn)
    seed(conn)
    return load_lexicon(conn)


def fake_call(response_text):
    """A fake LLM transport: records prompts, returns canned text."""
    calls = []

    def call(system, user):
        calls.append((system, user))
        return response_text

    call.calls = calls
    return call


def test_rerank_returns_choice(lex):
    cands = segment("therapist", lex)
    call = fake_call(json.dumps({"choice": 0, "reason": "therap+ist is the Greek analysis"}))
    result = rerank("therapist", cands, call=call)
    assert result is not None
    assert result.choice == 0
    assert "Greek" in result.reason


def test_prompt_is_closed_set(lex):
    cands = segment("therapist", lex)
    call = fake_call(json.dumps({"choice": 0, "reason": "x"}))
    rerank("therapist", cands, call=call)
    system, user = call.calls[0]
    # The plan §4 constraint must be in the version-controlled system prompt.
    assert "only choose from the provided candidates" in system
    assert "Do not propose a new segmentation" in system
    # Every candidate index and the word appear in the user prompt.
    assert "therapist" in user
    for i in range(len(cands)):
        assert f"{i}." in user


def test_user_prompt_renders_pieces(lex):
    cands = segment("chronology", lex)
    prompt = build_user_prompt("chronology", cands)
    assert "chrono" in prompt
    assert "logy" in prompt
    assert "suffix" in prompt


@pytest.mark.parametrize("bad", [
    "not json at all",
    json.dumps({"reason": "missing choice"}),
    json.dumps({"choice": 99, "reason": "out of range"}),
    json.dumps({"choice": -1, "reason": "negative"}),
    json.dumps({"choice": "zero", "reason": "wrong type"}),
])
def test_invalid_responses_return_none(lex, bad):
    cands = segment("monolithic", lex)
    assert rerank("monolithic", cands, call=fake_call(bad)) is None


def test_transport_error_returns_none(lex):
    cands = segment("monolithic", lex)

    def broken(system, user):
        raise RuntimeError("api down")

    assert rerank("monolithic", cands, call=broken) is None


def test_empty_candidates_returns_none():
    assert rerank("word", [], call=fake_call("{}")) is None


def test_single_candidate_skips_llm(lex):
    # One candidate = nothing to rank; must not spend an API call.
    cands = segment("qzxvqx", lex)
    assert len(cands) == 1
    call = fake_call(json.dumps({"choice": 0, "reason": "x"}))
    result = rerank("qzxvqx", cands, call=call)
    assert result is not None
    assert result.choice == 0
    assert call.calls == []


def test_model_version_covers_rerank_prompt():
    from app import version

    v1 = version.model_version()
    original = version.rerank.RERANK_SYSTEM
    try:
        version.rerank.RERANK_SYSTEM = original + " tweaked"
        assert version.model_version() != v1
    finally:
        version.rerank.RERANK_SYSTEM = original
    assert version.model_version() == v1
