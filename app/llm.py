"""Shared LLM plumbing for the two calls (rerank, assemble).

Two providers, picked by available credentials:
- Anthropic (preferred when configured). The SDK constructs a client
  successfully with no credentials and only fails at request time, so the
  check looks for an actual credential source.
- Gemini via the AI Studio REST API (free tier, no SDK — stdlib urllib):
  set GEMINI_API_KEY (or GOOGLE_API_KEY). The Claude model id callers pass
  is ignored on this provider; WIKIWORD_GEMINI_MODEL picks the model.

tests/conftest.py stubs is_enabled/call_structured so tests never reach a
real API.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

GEMINI_MODEL = os.environ.get("WIKIWORD_GEMINI_MODEL", "gemini-flash-latest")
GEMINI_TIMEOUT_S = 60

_client = None
_client_error = False


def _credentials_present(client) -> bool:
    if getattr(client, "api_key", None) or getattr(client, "auth_token", None):
        return True
    # Workload Identity Federation env vars
    if os.environ.get("ANTHROPIC_IDENTITY_TOKEN_FILE") or os.environ.get(
        "ANTHROPIC_IDENTITY_TOKEN"
    ):
        return True
    # An `ant auth login` OAuth profile on disk (resolved lazily by the SDK)
    from pathlib import Path

    config_dir = Path(
        os.environ.get("ANTHROPIC_CONFIG_DIR")
        or Path.home() / ".config" / "anthropic"
    )
    creds = config_dir / "credentials"
    return creds.is_dir() and any(creds.glob("*.json"))


def get_client():
    """Anthropic client, or None when no credentials are configured."""
    global _client, _client_error
    if _client is None and not _client_error:
        try:
            import anthropic

            client = anthropic.Anthropic()
            if _credentials_present(client):
                _client = client
            else:
                _client_error = True
        except Exception:
            _client_error = True
    return _client


def _gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def provider() -> str:
    """'anthropic' | 'gemini' | 'none'. Anthropic wins when both are set."""
    if get_client() is not None:
        return "anthropic"
    if _gemini_key():
        return "gemini"
    return "none"


def is_enabled() -> bool:
    return provider() != "none"


def resolved_model(requested: str) -> str:
    """The model that would actually serve a call asking for `requested`."""
    return GEMINI_MODEL if provider() == "gemini" else requested


def _gemini_schema(schema: dict) -> dict:
    """Our JSON schema -> Gemini responseSchema (OpenAPI subset): uppercase
    type names, no additionalProperties."""
    out = {}
    if "type" in schema:
        out["type"] = schema["type"].upper()
    if "properties" in schema:
        out["properties"] = {
            k: _gemini_schema(v) for k, v in schema["properties"].items()
        }
    if "items" in schema:
        out["items"] = _gemini_schema(schema["items"])
    for key in ("required", "enum", "description", "minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    return out


def _http_post(url: str, payload: dict, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _call_gemini(system: str, user: str, schema: dict) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
        },
    }
    status, body = _http_post(url, payload, {"x-goog-api-key": _gemini_key()})
    if status != 200:
        raise RuntimeError(f"Gemini HTTP {status}: {body[:200]!r}")
    data = json.loads(body)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data.get('promptFeedback')}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise RuntimeError("Gemini returned no text parts")
    return text


def list_gemini_models() -> list[str]:
    """Model names this key can use with generateContent."""
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": _gemini_key()},
    )
    with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_S) as resp:
        data = json.loads(resp.read())
    return [
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def call_structured(
    model: str,
    system: str,
    user: str,
    output_format: dict,
    effort: str = "low",
    max_tokens: int = 1024,
) -> str:
    """One structured-output request; returns the JSON text. Raises on any
    failure (no credentials, transport, refusal) — callers degrade gracefully."""
    which = provider()
    if which == "gemini":
        return _call_gemini(system, user, output_format["schema"])
    client = get_client()
    if client is None:
        raise RuntimeError("no LLM credentials configured")
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"format": output_format, "effort": effort},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the request")
    return next(b.text for b in response.content if b.type == "text")


if __name__ == "__main__":
    # Self-test: `python -m app.llm` — one real structured call, so a broken
    # key/quota/model shows its actual error instead of a silent fallback.
    which = provider()
    print(f"provider: {which}")
    if which == "none":
        print("no credentials found (set GEMINI_API_KEY or Anthropic creds)")
        raise SystemExit(1)
    print(f"model: {resolved_model('claude-opus-4-8')}")
    fmt = {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    }
    try:
        raw = call_structured(
            "claude-opus-4-8", "Reply as instructed.",
            'Return {"ok": true}.', fmt,
        )
        print(f"response: {raw.strip()}")
        print("LLM call OK")
    except Exception as exc:
        print(f"LLM call FAILED: {exc}")
        if which == "gemini":
            try:
                print("\nmodels available to this key (set WIKIWORD_GEMINI_MODEL):")
                for name in list_gemini_models():
                    print(f"  {name}")
            except Exception as list_exc:
                print(f"(could not list models: {list_exc})")
        raise SystemExit(1)
