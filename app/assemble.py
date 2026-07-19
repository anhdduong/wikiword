"""llm_assemble (plan §4): constrained synthesis of the prose fields.

Input: the grounded morphemes (meanings/origins already retrieved from the
affix table) plus fetched etymology prose. Output: literal_meaning and
modern_usage, both synthesized strictly from the provided facts — nothing
may be invented.

If no morpheme carries a verified meaning there is nothing to synthesize
from — assemble() returns None without spending an API call, and the
response ships null prose fields rather than invented ones.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Sequence

from app import llm
from app.ground import GroundedMorpheme

ASSEMBLE_MODEL = os.environ.get("WIKIWORD_ASSEMBLE_MODEL", "claude-opus-4-8")
PROMPT_VERSION = "assemble-v2"  # v2: dropped example_sentence

ASSEMBLE_SYSTEM = """\
You write the prose fields of an etymology reference entry. You are given a \
word, its morphemes with meanings and origins retrieved from an authoritative \
affix table, and etymology text fetched from Wiktionary.

Rules:
- Synthesize only from the facts provided. Do not introduce any origin or \
meaning not present in the input. Do not invent content.
- literal_meaning: compose the word's literal sense from the provided \
morpheme meanings alone (e.g. "Relating to a single stone.").
- modern_usage: one short sentence on how the word is used today, staying \
consistent with the provided facts (e.g. "Large, powerful, and uniform \
(like a massive, immovable stone structure).").
- Morphemes marked [unverified] have no confirmed meaning; do not assert \
anything about them."""

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "literal_meaning": {"type": "string"},
            "modern_usage": {"type": "string"},
        },
        "required": ["literal_meaning", "modern_usage"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class AssembleResult:
    literal_meaning: str
    modern_usage: str


def has_facts(morphemes: Sequence[GroundedMorpheme]) -> bool:
    """True when at least one morpheme carries an authoritative meaning."""
    return any(m.meaning for m in morphemes)


def _render_morpheme(m: GroundedMorpheme) -> str:
    if m.meaning:
        origin = m.origin or "unknown origin"
        src = f" (source form: {m.source_form})" if m.source_form else ""
        return f"- {m.surface} ({m.type}, {origin}{src}): {m.meaning}"
    return f"- {m.surface} ({m.type}): [unverified] no confirmed meaning"


def build_user_prompt(
    word: str,
    morphemes: Sequence[GroundedMorpheme],
    etymology_texts: Sequence[str],
) -> str:
    lines = [f"Word: {word}", "", "Morphemes:"]
    lines += [_render_morpheme(m) for m in morphemes]
    if etymology_texts:
        lines += ["", "Fetched etymology text:"]
        lines += [f"- {t}" for t in etymology_texts]
    lines += ["", "Write literal_meaning and modern_usage."]
    return "\n".join(lines)


def _default_call(system: str, user: str) -> str:
    return llm.call_structured(
        ASSEMBLE_MODEL, system, user, _OUTPUT_FORMAT,
        effort="medium", max_tokens=2048,
    )


def assemble(
    word: str,
    morphemes: Sequence[GroundedMorpheme],
    etymology_texts: Sequence[str],
    call: Callable[[str, str], str] = _default_call,
) -> AssembleResult | None:
    """Prose fields, or None (no facts to work from, or the call failed)."""
    if not has_facts(morphemes):
        return None
    try:
        raw = call(ASSEMBLE_SYSTEM, build_user_prompt(word, morphemes, etymology_texts))
        data = json.loads(raw)
        return AssembleResult(
            literal_meaning=str(data["literal_meaning"]),
            modern_usage=str(data["modern_usage"]),
        )
    except Exception:
        return None
