"""Cache-invalidation version string (plan §4).

word_cache rows are only served when their model_version matches the current
one, so changing anything that affects output invalidates stale entries.
Today that's the segmentation cost constants; the LLM milestones will fold
the model id and both prompt templates into the key.
"""

import hashlib

from app import assemble, compose, ground, llm, rerank
from app import segment as s

_ALGORITHM_TAG = "segment-rerank-v1"  # bump on structural algorithm changes

# Bump whenever main.py's payload shape or lookup orchestration changes:
# cached payloads predating the change then evict themselves. (Lives here,
# not in main.py, because main imports this module.)
PAYLOAD_VERSION = "payload-v2"  # v2: unrecognized flag + suggestions


def model_version() -> str:
    key = repr((
        _ALGORITHM_TAG,
        PAYLOAD_VERSION,
        s.KNOWN_BASE, s.UNREVIEWED_PENALTY, s.FREE_BASE, s.FREE_PER_CHAR,
        s.LINKER_COST, s.UNKNOWN_BASE, s.UNKNOWN_PER_CHAR,
        s.FUNCTION_WORD_PENALTY, s.MIXED_ROOT_PENALTY,
        s.FREE_WHOLE_BASE, s.FREE_WHOLE_PER_CHAR, s.FREE_WHOLE_MAX_RANK,
        s.FUNCTION_WHOLE_COST,
        # LLM calls: model ids + both prompt templates + which provider (if
        # any) serves them (plan §4). A no-credentials deployment must not
        # serve cache written by an LLM-enabled one, nor Gemini-written
        # cache pass for Anthropic-written, and vice versa.
        rerank.RERANK_MODEL, rerank.PROMPT_VERSION, rerank.RERANK_SYSTEM,
        rerank.RERANK_MARGIN,
        assemble.ASSEMBLE_MODEL, assemble.PROMPT_VERSION, assemble.ASSEMBLE_SYSTEM,
        llm.is_enabled(), llm.provider(), llm.GEMINI_MODEL,
        ground.GROUND_VERSION,
        compose.COMPOSE_VERSION,
    ))
    return "seg-" + hashlib.sha256(key.encode()).hexdigest()[:12]
