"""Cache-invalidation version string (plan §4).

word_cache rows are only served when their model_version matches the current
one, so changing anything that affects output invalidates stale entries.
Today that's the segmentation cost constants; the LLM milestones will fold
the model id and both prompt templates into the key.
"""

import hashlib

from app import assemble, ground, llm, rerank
from app import segment as s

_ALGORITHM_TAG = "segment-rerank-v1"  # bump on structural algorithm changes


def model_version() -> str:
    key = repr((
        _ALGORITHM_TAG,
        s.KNOWN_BASE, s.UNREVIEWED_PENALTY, s.FREE_BASE, s.FREE_PER_CHAR,
        s.LINKER_COST, s.UNKNOWN_BASE, s.UNKNOWN_PER_CHAR,
        s.FUNCTION_WORD_PENALTY, s.MIXED_ROOT_PENALTY,
        # LLM calls: model ids + both prompt templates + whether they run at
        # all (plan §4). A no-credentials deployment must not serve cache
        # written by an LLM-enabled deployment, and vice versa.
        rerank.RERANK_MODEL, rerank.PROMPT_VERSION, rerank.RERANK_SYSTEM,
        assemble.ASSEMBLE_MODEL, assemble.PROMPT_VERSION, assemble.ASSEMBLE_SYSTEM,
        llm.is_enabled(),
        ground.GROUND_VERSION,
    ))
    return "seg-" + hashlib.sha256(key.encode()).hexdigest()[:12]
