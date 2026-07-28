"""llm_assemble (plan §4): constrained synthesis of the literal meaning.

Input: the grounded morphemes (meanings/origins already retrieved from the
affix table) plus fetched etymology prose. Output: literal_meaning,
synthesized strictly from the provided facts — nothing may be invented.

If no morpheme carries a verified meaning there is nothing to synthesize
from — assemble() returns None without spending an API call, and the
response ships a null literal_meaning rather than an invented one.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Sequence

import re

from app import llm
from app.ground import GroundedMorpheme, deaccent

log = logging.getLogger(__name__)

ASSEMBLE_MODEL = os.environ.get("WIKIWORD_ASSEMBLE_MODEL", "claude-opus-4-8")
PROMPT_VERSION = "assemble-v4"  # v4: literal meaning only — modern usage is
                                # quoted from a dictionary, never synthesized

ASSEMBLE_SYSTEM = """\
You write the literal-meaning field of an etymology reference entry. You are \
given a word, its morphemes with meanings and origins retrieved from an \
authoritative affix table, and etymology text fetched from Wiktionary.

Rules:
- Synthesize only from the facts provided. Do not introduce any origin or \
meaning not present in the input. Do not invent content.
- literal_meaning: compose the word's literal sense from the provided \
morpheme meanings alone (e.g. "Relating to a single stone.").
- Morphemes marked [unverified] have no confirmed meaning in the affix \
table; say nothing about them unless the fetched etymology text itself \
states their meaning — then use exactly the sense that text states (e.g. \
if it says alone is literally "all one", the literal_meaning may be \
"All one.")."""

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "literal_meaning": {"type": "string"},
        },
        "required": ["literal_meaning"],
        "additionalProperties": False,
    },
}


# --- grounding check ---------------------------------------------------------
# The prompt forbids inventing a meaning for an [unverified] morpheme, but a
# prompt is not an enforcement mechanism: asked for "environment" (environ
# [unverified] + -ment "result or means of an action") the model answered
# "Result or means of surrounding." — correct, and sourced from nothing in
# the input. A literal_meaning is only publishable if every content word in
# it traces back to a fact we supplied.

_WORD_RE = re.compile(r"[a-z]+")

# Gloss scaffolding: generic enough to be phrasing rather than a claim about
# this word's etymology.
_SCAFFOLD = frozenset("""
about act action being characterized concerning condition denoting does doing
done form formed from full given having into kind literal literally made
manner marked means meaning more most onto past pertaining place plural
process quality relate relates relating relation result sense singular
someone something sort state study tense than that these thing this those
toward upon used which with without word
""".split())

_MIN_CONTENT_LEN = 4  # shorter tokens are function words in practice
_STEM_LEN = 3  # ask/asking, move/moving, own/owns all agree on three

# Wiktionary's editorial metalanguage describes the entry, it does not state
# what the word means. Left in, "By surface analysis, environ + -ment" lends
# its "surface" to license "surrounding" — the very invention we are hunting.
_BOILERPLATE = frozenset("""
analysis attested borrowed calque cognate compare derived doublet equivalent
inherited learned literally mentioned obsolete perhaps possibly probably
reconstructed spelling surface uncertain unknown unverified variant
""".split())


def _fact_vocabulary(
    word: str,
    morphemes: Sequence[GroundedMorpheme],
    etymology_texts: Sequence[str],
) -> set[str]:
    """Every token the model was actually given."""
    parts: list[str] = [word]
    for m in morphemes:
        parts += [m.surface, m.meaning or "", m.origin or "",
                  m.source_form or "", m.notes or ""]
    parts += list(etymology_texts)
    tokens = {t for p in parts for t in _WORD_RE.findall(deaccent(p).lower())}
    return tokens - _BOILERPLATE


def ungrounded_tokens(
    literal_meaning: str,
    word: str,
    morphemes: Sequence[GroundedMorpheme],
    etymology_texts: Sequence[str],
) -> list[str]:
    """Content words in the output that no input fact supports.

    Matching is stem-ish (first three characters) so that ordinary inflection
    of a supplied fact — stone/stones, surround/surrounding, ask/asking —
    counts as grounded.
    """
    vocab = _fact_vocabulary(word, morphemes, etymology_texts)
    stems = {v[:_STEM_LEN] for v in vocab if len(v) >= _STEM_LEN}
    out: list[str] = []
    for t in _WORD_RE.findall(deaccent(literal_meaning).lower()):
        if len(t) < _MIN_CONTENT_LEN or t in _SCAFFOLD or t in vocab:
            continue
        if t[:_STEM_LEN] in stems:
            continue
        out.append(t)
    return out


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
    lines += ["", "Write literal_meaning."]
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
) -> str | None:
    """The literal meaning, or None (no facts to work from, or call failed)."""
    if not has_facts(morphemes):
        return None
    try:
        raw = call(ASSEMBLE_SYSTEM, build_user_prompt(word, morphemes, etymology_texts))
        literal = str(json.loads(raw)["literal_meaning"])
    except Exception as exc:
        log.warning("assemble(%s) failed: %s", word, exc)
        return None
    invented = ungrounded_tokens(literal, word, morphemes, etymology_texts)
    if invented:
        # Better a null field than a plausible sentence we cannot source.
        log.warning("assemble(%s) dropped — ungrounded tokens %s in %r",
                    word, invented, literal)
        return None
    return literal
