import json

import pytest

from app import llm
from app.rerank import _OUTPUT_FORMAT as RERANK_FORMAT


@pytest.fixture
def no_anthropic(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def test_provider_none_without_credentials(no_anthropic):
    assert llm.provider() == "none"
    assert llm.resolved_model("claude-opus-4-8") == "claude-opus-4-8"


def test_provider_gemini_with_key(no_anthropic, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert llm.provider() == "gemini"
    assert llm.resolved_model("claude-opus-4-8") == llm.GEMINI_MODEL


def test_anthropic_wins_over_gemini(monkeypatch):
    monkeypatch.setattr(llm, "get_client", lambda: object())
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert llm.provider() == "anthropic"


def test_gemini_schema_conversion():
    out = llm._gemini_schema(RERANK_FORMAT["schema"])
    assert out["type"] == "OBJECT"
    assert out["properties"]["choice"]["type"] == "INTEGER"
    assert out["required"] == ["choice", "reason"]
    assert "additionalProperties" not in out  # Gemini rejects it


GEMINI_OK = json.dumps({
    "candidates": [{
        "content": {"parts": [
            {"text": "internal reasoning", "thought": True},
            {"text": '{"choice": 1, '},
            {"text": '"reason": "learned analysis"}'},
        ]},
    }],
}).encode()


def gemini_env(monkeypatch, status, body):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    posts = []

    def fake_post(url, payload, headers):
        posts.append((url, payload, headers))
        return status, body

    monkeypatch.setattr(llm, "_http_post", fake_post)
    return posts


def test_call_gemini_joins_text_and_skips_thoughts(no_anthropic, monkeypatch):
    posts = gemini_env(monkeypatch, 200, GEMINI_OK)
    schema = RERANK_FORMAT["schema"]
    out = llm._call_gemini("sys prompt", "user prompt", schema)
    assert json.loads(out) == {"choice": 1, "reason": "learned analysis"}
    url, payload, headers = posts[0]
    assert llm.GEMINI_MODEL in url
    assert headers["x-goog-api-key"] == "test-key"
    assert payload["system_instruction"]["parts"][0]["text"] == "sys prompt"
    gc = payload["generationConfig"]
    assert gc["responseMimeType"] == "application/json"
    assert gc["responseSchema"]["type"] == "OBJECT"
    # Thinking is off: it multiplies latency for these closed-set tasks.
    assert gc["thinkingConfig"] == {"thinkingBudget": 0}


def test_call_gemini_retries_without_thinking_cap_on_400(no_anthropic, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    posts = []

    def fake_post(url, payload, headers):
        posts.append(json.loads(json.dumps(payload)))
        if "thinkingConfig" in payload["generationConfig"]:
            return 400, b'{"error": "budget 0 not supported"}'
        return 200, GEMINI_OK

    monkeypatch.setattr(llm, "_http_post", fake_post)
    out = llm._call_gemini("s", "u", RERANK_FORMAT["schema"])
    assert json.loads(out)["choice"] == 1
    assert len(posts) == 2
    assert "thinkingConfig" not in posts[1]["generationConfig"]


def test_call_gemini_http_error_raises(no_anthropic, monkeypatch):
    gemini_env(monkeypatch, 429, b'{"error": "quota"}')
    with pytest.raises(RuntimeError, match="429"):
        llm._call_gemini("s", "u", RERANK_FORMAT["schema"])


def test_call_gemini_blocked_response_raises(no_anthropic, monkeypatch):
    gemini_env(monkeypatch, 200, json.dumps(
        {"promptFeedback": {"blockReason": "SAFETY"}}
    ).encode())
    with pytest.raises(RuntimeError, match="no candidates"):
        llm._call_gemini("s", "u", RERANK_FORMAT["schema"])


def test_model_version_covers_provider(no_anthropic, monkeypatch):
    from app import version

    v_none = version.model_version()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert version.model_version() != v_none
