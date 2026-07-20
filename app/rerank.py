"""llm_rerank (plan §4): low-trust, closed-set choice over segment() output.

The LLM never originates an etymological fact here — it only returns the
index of one candidate produced by the deterministic segmenter, plus a
reason. Anything unexpected (bad JSON, out-of-range index, transport error,
refusal) discards the rerank and the caller falls back to cost order.

The system prompt is version-controlled; app/version.py folds it, the model
id, and the enabled-state into model_version so cached responses can never
outlive a prompt or model change (plan §4).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable

from app import llm
from app.segment import Candidate

log = logging.getLogger(__name__)

RERANK_MODEL = os.environ.get("WIKIWORD_RERANK_MODEL", "claude-opus-4-8")
PROMPT_VERSION = "rerank-v1"
# Below this cost gap between the top two candidates the choice is genuinely
# ambiguous and worth an LLM call; at or above it the cost order stands
# without API spend. Hashed into model_version like the cost constants.
RERANK_MARGIN = 0.75

RERANK_SYSTEM = """\
You are the segmentation reranker for an etymology reference tool. You are \
given a word and a numbered list of candidate morpheme segmentations produced \
by a deterministic algorithm over a curated affix lexicon.

Pick the single candidate that best matches the word's actual etymology.

Rules:
- You may only choose from the provided candidates. Do not propose a new \
segmentation. Do not add or change any morpheme meaning.
- Beware of false friends: a split that reuses ordinary English words \
(the|rapist for therapist) is usually wrong when a learned Greek/Latin \
analysis is available.
- A lower algorithm cost means the algorithm preferred it, but the algorithm \
cannot judge etymology — override it when a costlier candidate is \
etymologically correct.
- "unknown" pieces are spans the algorithm could not match; a candidate with \
unknown pieces can still be the honest best choice (e.g. for words that do \
not decompose)."""

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "choice": {"type": "integer",
                       "description": "Index of the best candidate"},
            "reason": {"type": "string",
                       "description": "One-sentence justification"},
        },
        "required": ["choice", "reason"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class RerankResult:
    choice: int
    reason: str


def is_enabled() -> bool:
    return llm.is_enabled()


def _default_call(system: str, user: str) -> str:
    return llm.call_structured(
        RERANK_MODEL, system, user, _OUTPUT_FORMAT, effort="low"
    )


def _render_candidate(c: Candidate) -> str:
    pieces = " + ".join(
        p.surface + (f"·{p.linker}" if p.linker else "") + f"({p.kind})"
        for p in c.pieces
    )
    return f"{pieces}  [algorithm cost {c.cost:.2f}]"


def build_user_prompt(word: str, candidates: list[Candidate]) -> str:
    lines = [f"Word: {word}", "", "Candidates:"]
    lines += [f"{i}. {_render_candidate(c)}" for i, c in enumerate(candidates)]
    lines.append("")
    lines.append("Return the index of the best candidate and a one-sentence reason.")
    return "\n".join(lines)


def rerank(
    word: str,
    candidates: list[Candidate],
    call: Callable[[str, str], str] = _default_call,
) -> RerankResult | None:
    """Pick one candidate; None means the rerank failed and cost order stands."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return RerankResult(0, "only one candidate")
    margin = candidates[1].cost - candidates[0].cost
    if margin >= RERANK_MARGIN:
        return RerankResult(
            0, f"clear cost margin (+{margin:.2f} to runner-up); rerank skipped"
        )
    try:
        raw = call(RERANK_SYSTEM, build_user_prompt(word, candidates))
        data = json.loads(raw)
        choice = data["choice"]
        reason = data["reason"]
    except Exception as exc:
        log.warning("rerank(%s) failed: %s", word, exc)
        return None
    if not isinstance(choice, int) or isinstance(choice, bool):
        log.warning("rerank(%s): non-integer choice %r", word, choice)
        return None
    if not 0 <= choice < len(candidates):
        log.warning("rerank(%s): choice %r out of range", word, choice)
        return None
    return RerankResult(choice, str(reason))
