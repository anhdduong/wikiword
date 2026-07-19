"""Deterministic prose fallback: no LLM, no API key, nothing invented.

When LLM calls are disabled the prose fields are composed mechanically:
- literal_meaning: the affix-table glosses of the chosen segmentation,
  joined in word order. Pure table lookup — it cannot originate a fact.
- modern_usage: the word's first definition from the Free Dictionary API,
  quoted verbatim. Fetched, not generated.

Stiffer prose than llm_assemble, but every character traces to the affix
table or a fetched source, which is the plan's core rule anyway.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Sequence

from app.ground import GroundedMorpheme
from app.retrieve import HttpGet, _default_http_get, free_dictionary_url

COMPOSE_VERSION = "compose-v2"  # v2: definition fetched even without glosses


def _surface(m: GroundedMorpheme) -> str:
    if m.type == "prefix":
        return m.surface + "-"
    if m.type == "suffix":
        return "-" + m.surface
    return m.surface


def literal_meaning(morphemes: Sequence[GroundedMorpheme]) -> str | None:
    """Glosses joined in word order, or None when no morpheme has one."""
    if not any(m.meaning for m in morphemes):
        return None
    parts = [
        f'{_surface(m)} "{m.meaning}"' if m.meaning
        else f"{_surface(m)} [unverified]"
        for m in morphemes
    ]
    return " + ".join(parts)


def _first_definition(body: bytes) -> str | None:
    try:
        entries = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        for meaning in entry.get("meanings") or []:
            for d in meaning.get("definitions") or []:
                text = (d.get("definition") or "").strip()
                if text:
                    pos = meaning.get("partOfSpeech")
                    return f"({pos}) {text}" if pos else text
    return None


def fetch_definition(
    word: str, http_get: HttpGet = _default_http_get
) -> tuple[str | None, bool]:
    """(definition, definitive) from the Free Dictionary API.

    definitive=False means a transport failure or 5xx — the caller must not
    cache the response, so the next lookup retries. A 404 (word unknown) is
    a definitive empty answer.
    """
    try:
        status, body = http_get(free_dictionary_url(word))
    except (OSError, urllib.error.URLError):
        return None, False
    if status == 200:
        return _first_definition(body), True
    return None, status == 404
