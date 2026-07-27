"""Deterministic prose fallback: no LLM, no API key, nothing invented.

When LLM calls are disabled, literal_meaning is composed mechanically from
the affix-table glosses of the chosen segmentation, joined in word order.
Pure table lookup — it cannot originate a fact.
"""

from __future__ import annotations

from typing import Sequence

from app.ground import GroundedMorpheme

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
