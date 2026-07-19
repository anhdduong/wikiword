"""Shared Anthropic client plumbing for the two LLM calls (rerank, assemble).

The SDK constructs a client successfully with no credentials and only fails
at request time, so is_enabled() checks for an actual credential source.
tests/conftest.py stubs is_enabled so tests never reach the real API.
"""

from __future__ import annotations

import os

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


def is_enabled() -> bool:
    return get_client() is not None


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
    client = get_client()
    if client is None:
        raise RuntimeError("no Anthropic credentials configured")
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
