"""ground() + status() (plan §5): verify the chosen segmentation against the
affix table and retrieved etymology text.

Rules implemented:
- The affix table is truth: a morpheme backed by a table row is verified,
  with the row's meaning/origin and its (actually fetched) citations.
- Retrieved etymology is corroboration and an additional citation source —
  a record URL attaches to a morpheme only when that record's text actually
  mentions it.
- Free-word pieces have no authoritative meaning; they verify only via
  corroboration, and no meaning is ever invented for them.
- Conflict: the text mentions a morpheme but attributes only origin
  languages disjoint from the table row's. Any conflict caps status at
  'partial' (§9 decision).
- Every unknown span and every reviewed=0 morpheme is queued for curation
  (deduped by surface), subject to the queue guards below: nothing shorter
  than MIN_QUEUE_SURFACE, nothing from a proper noun, nothing at all from a
  word no dictionary recognises. Guards gate curation only — the served
  payload is identical with or without them.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.retrieve import EtymologyRecord, prose
from app.segment import COMBINING, FREE, UNKNOWN, Candidate

GROUND_VERSION = "ground-v4"  # v4: free-word pieces surface as type "word"
                              # (they were never affix-table roots)

WORD = "word"  # display type for free-word pieces

# Queue hygiene. These gate what reaches review_queue only — segmentation
# and the served payload are untouched, which is why they do not bump
# GROUND_VERSION.
#
# A one- or two-letter unmatched span is a leftover of a bad split, not a
# morpheme somebody should curate; before this guard they were 46% of the
# unknown-span queue.
MIN_QUEUE_SURFACE = 3

PROPER_NOUNS_PATH = Path(__file__).parent.parent / "seed" / "proper_nouns.txt"


def _load_proper_nouns(path: Path = PROPER_NOUNS_PATH) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


PROPER_NOUNS = _load_proper_nouns()

LANG_KEYWORDS = frozenset(
    "greek latin english french german norse dutch italian spanish arabic "
    "hebrew sanskrit".split()
)


@dataclass(frozen=True)
class GroundedMorpheme:
    surface: str
    type: str  # prefix | root | suffix | combining_form | word | unknown
    origin: str | None
    source_form: str | None
    meaning: str | None
    verified: bool
    citations: tuple[str, ...]
    notes: str | None


@dataclass(frozen=True)
class GroundingResult:
    morphemes: tuple[GroundedMorpheme, ...]
    conflicts: tuple[dict, ...]
    status: str  # grounded | partial | unverified
    status_note: str | None


def _tokens_of(surface: str, canonical: str | None, source_form: str | None) -> list[str]:
    tokens = [surface]
    if canonical:
        tokens.append(canonical.strip("-"))
    if source_form:
        tokens += [part.strip("- ") for part in re.split(r"[/,]", source_form)]
    return [t.lower() for t in dict.fromkeys(tokens) if len(t) >= 2]


def deaccent(text: str) -> str:
    """Drop combining marks. Wiktionary writes Latin and transliterated
    Greek with length marks (lex -> lēx, optio -> optiō, thea -> theā), so
    a literal match against an ASCII source_form never fires — which
    silently defeats homograph disambiguation: neither sense of "leg"
    matches legal's prose, so the first table row wins by accident rather
    than by evidence."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def _mentions(text: str, tokens: list[str]) -> bool:
    low = deaccent(text).lower()
    return any(
        re.search(rf"\b{re.escape(deaccent(t).lower())}\b", low)
        for t in tokens
    )


def _langs_in(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w in LANG_KEYWORDS}


def _row_langs(origin_lang: str) -> set[str]:
    return _langs_in(origin_lang)


def _sentences(records: list[EtymologyRecord]) -> list[str]:
    """Prose sentences only. The 'Etymology tree' lineage block lists every
    historical stage ('English photo-', 'Proto-Hellenic *-íā') — those are
    not origin claims and must not feed conflict detection."""
    out = []
    for r in records:
        out += [s.strip() for s in re.split(r"[.;\n]", prose(r.text)) if s.strip()]
    return out


def _queue(conn: sqlite3.Connection, surface: str, word: str, proposed: dict) -> None:
    if word in PROPER_NOUNS:
        return
    if proposed.get("kind") == "unknown" and len(surface) < MIN_QUEUE_SURFACE:
        return
    exists = conn.execute(
        "SELECT 1 FROM review_queue WHERE surface = ?", (surface,)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO review_queue (surface, seen_in, proposed) VALUES (?, ?, ?)",
            (surface, word, json.dumps(proposed)),
        )


def ground(
    conn: sqlite3.Connection,
    word: str,
    candidate: Candidate,
    records: list[EtymologyRecord],
    curate: bool = True,
) -> GroundingResult:
    """curate=False suppresses review_queue writes. main.py passes it for
    words no dictionary source knows (likely misspellings): their spans are
    artefacts of segmenting a non-word, so nothing there is worth a human's
    time. Grounding output itself is identical either way."""
    sentences = _sentences(records)
    morphemes: list[GroundedMorpheme] = []
    conflicts: list[dict] = []

    with conn:
        for piece in candidate.pieces:
            if piece.kind == UNKNOWN:
                morphemes.append(GroundedMorpheme(
                    surface=piece.surface, type=UNKNOWN, origin=None,
                    source_form=None, meaning=None, verified=False,
                    citations=(), notes="unmatched span",
                ))
                if curate:
                    _queue(conn, piece.surface, word, {"kind": "unknown"})
                continue

            if piece.kind == FREE or not piece.affix_ids:
                corroborating = tuple(
                    r.url for r in records
                    if r.url and _mentions(r.text, [piece.surface])
                )
                morphemes.append(GroundedMorpheme(
                    surface=piece.surface, type=WORD, origin=None,
                    source_form=None, meaning=None,
                    verified=bool(corroborating),
                    citations=corroborating,
                    notes="free English word (not in affix table)",
                ))
                continue

            # Table-backed morpheme: pick a sense, corroborate, detect conflict.
            rows = conn.execute(
                f"SELECT * FROM affix WHERE id IN "
                f"({','.join('?' * len(piece.affix_ids))}) ORDER BY id",
                piece.affix_ids,
            ).fetchall()
            corroborated = [
                row for row in rows
                if any(_mentions(r.text,
                                 _tokens_of(piece.surface, row["canonical"],
                                            row["source_form"]))
                       for r in records)
            ]
            # Homograph senses (Latin ad- vs Old English al-) both match the
            # surface token; prefer the sense whose origin language the
            # mentioning prose actually names.
            pool = corroborated or rows

            def _origin_agrees(r) -> bool:
                toks = _tokens_of(piece.surface, r["canonical"],
                                  r["source_form"])
                langs = set().union(
                    set(), *(_langs_in(s) for s in sentences
                             if _mentions(s, toks))
                )
                return bool(langs & _row_langs(r["origin_lang"]))

            lang_matched = [r for r in pool if _origin_agrees(r)]
            row = (lang_matched or pool)[0]
            tokens = _tokens_of(piece.surface, row["canonical"], row["source_form"])

            citations = list(json.loads(row["citations"]))
            for r in records:
                if r.url and _mentions(r.text, tokens) and r.url not in citations:
                    citations.append(r.url)

            notes = None
            if len(rows) > 1 and not corroborated:
                notes = f"{len(rows)} senses in affix table; not disambiguated"

            # Conflict: sentences that mention this morpheme name only
            # origin languages disjoint from the table row's.
            mentioning = [s for s in sentences if _mentions(s, tokens)]
            text_langs = set().union(*(_langs_in(s) for s in mentioning)) \
                if mentioning else set()
            row_langs = _row_langs(row["origin_lang"])
            if text_langs and row_langs and not (text_langs & row_langs):
                conflicts.append({
                    "morpheme": piece.surface,
                    "table_origin": row["origin_lang"],
                    "text_mentions": sorted(text_langs),
                    "snippet": mentioning[0],
                })

            morphemes.append(GroundedMorpheme(
                surface=piece.surface,
                type=piece.kind if piece.kind != COMBINING else COMBINING,
                origin=row["origin_lang"],
                source_form=row["source_form"],
                meaning=row["gloss"],
                verified=True,
                citations=tuple(citations),
                notes=notes,
            ))
            if curate and not row["reviewed"]:
                _queue(conn, piece.surface, word, {
                    "affix_id": row["id"], "canonical": row["canonical"],
                    "type": row["type"], "origin_lang": row["origin_lang"],
                    "gloss": row["gloss"],
                })

    status, note = _status(morphemes, conflicts)
    return GroundingResult(tuple(morphemes), tuple(conflicts), status, note)


def _status(
    morphemes: list[GroundedMorpheme], conflicts: list[dict]
) -> tuple[str, str | None]:
    total = len(morphemes)
    verified = sum(m.verified for m in morphemes)
    notes = []

    if verified == total:
        status = "grounded"
    elif verified > 0:
        status = "partial"
    else:
        status = "unverified"
    if verified < total:
        unverified = [m.surface for m in morphemes if not m.verified]
        notes.append(
            f"{verified} of {total} morphemes verified"
            f" (unverified: {', '.join(unverified)})"
        )
    if conflicts:
        if status == "grounded":
            status = "partial"
        names = ", ".join(c["morpheme"] for c in conflicts)
        notes.append(
            f"{len(conflicts)} conflict(s) between affix table and retrieved"
            f" etymology ({names})"
        )
    return status, "; ".join(notes) or None
