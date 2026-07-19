"""segment(): decompose a word into ranked candidate morpheme tilings.

Framed per plan §3 as k-shortest paths on a morpheme DAG:

- Nodes are character positions 0..n plus a positional *phase* that encodes
  the constraints of §3.2 as a small state machine, so a word must read as
  (prefix* root-like+)+ suffix*  — prefixes may recur mid-word
  (photo|syn|the|sis) but never after the last root; suffixes never before
  the first root; every accepted path contains a root-like piece.
- Edges are dictionary matches (affix forms and free words), optional
  linking-vowel extensions of root-like matches (speed·o·meter), and
  unknown-span fallbacks that keep the graph connected. Unknown spans are
  maximal: an unknown edge may not follow another unknown edge.
- k-shortest is a lazy best-first enumeration over (position, phase) —
  equivalent to Yen's/Eppstein for our tiny graphs, far simpler. Costs are
  non-negative, so paths pop in true cost order.

Costs are hand-tuned against the §3.6 words; calibrating the false-friend
penalty against a labelled trap set is still open (plan §9).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import count

from app.lexicon import FUNCTION_WORDS, Lexicon

PREFIX, ROOT, SUFFIX, COMBINING, FREE, UNKNOWN = (
    "prefix", "root", "suffix", "combining_form", "free", "unknown",
)
ROOT_LIKE = {ROOT, FREE, UNKNOWN}

KNOWN_BASE = 1.0            # every affix-table morpheme
UNREVIEWED_PENALTY = 0.2    # affix row not yet human-reviewed
FREE_BASE = 1.25            # free-word root (slightly worse than a reviewed affix)
FREE_PER_CHAR = 0.15        # long free words must not swallow a learned tiling
                            # (monolith|ic must lose to mono|lith|ic)
LINKER_COST = 0.1           # absorbing a linking vowel
UNKNOWN_BASE = 4.0          # any unknown span
UNKNOWN_PER_CHAR = 0.6      # ... plus per uncovered character
FUNCTION_WORD_PENALTY = 3.0  # 'the' et al. posing as a root
MIXED_ROOT_PENALTY = 2.0    # free root directly adjacent to a learned root
FREE_WHOLE_BASE = 2.0       # whole-word candidate for everyday words
FREE_WHOLE_PER_CHAR = 0.35  # ... must lose to any decent decomposition
                            # (some|thing beats something; that beats tha|t)
FREE_WHOLE_MAX_RANK = 3000  # only common words earn a whole-word candidate;
                            # learned vocabulary (therapist, monolith) must
                            # still decompose

LINKING_VOWELS = "oi"
MAX_EXPANSIONS = 5000

# Phases of the positional state machine.
P_PRE, P_MID, P_STEM, P_SUF = range(4)
ACCEPTING = {P_STEM, P_SUF}


@dataclass(frozen=True)
class Piece:
    start: int
    end: int              # end of the full span, linker included
    surface: str          # matched form, linker NOT included
    linker: str | None
    kind: str
    affix_ids: tuple[int, ...]
    free_match: bool


@dataclass(frozen=True)
class Candidate:
    pieces: tuple[Piece, ...]
    cost: float


@dataclass(frozen=True)
class _Edge:
    start: int
    end: int
    surface: str
    linker: str | None
    kind: str
    affix_ids: tuple[int, ...]
    cost: float


def _next_phase(phase: int, kind: str) -> int | None:
    """Positional state machine; None = transition not allowed."""
    if kind in (PREFIX, COMBINING):
        return {P_PRE: P_PRE, P_MID: P_MID, P_STEM: P_MID}.get(phase)
    if kind in (ROOT, FREE):
        return P_STEM if phase in (P_PRE, P_MID, P_STEM) else None
    if kind == UNKNOWN:
        if phase in (P_PRE, P_MID, P_STEM):
            return P_STEM  # an unknown span may act as the root
        return P_SUF       # ... or as trailing unknown material
    if kind == SUFFIX:
        return P_SUF if phase in (P_STEM, P_SUF) else None
    raise ValueError(f"unknown piece kind {kind!r}")


def _build_edges(word: str, lex: Lexicon) -> list[list[_Edge]]:
    """Dictionary-match edges grouped by start position (unknowns are
    generated lazily during search)."""
    n = len(word)
    # Merge parallel senses of the same match: key collapses multiple affix
    # rows sharing a surface form and kind (in- 'not' / in- 'into').
    merged: dict[tuple, dict] = {}

    def add(start, end, surface, linker, kind, affix_ids, cost):
        key = (start, end, surface, linker, kind)
        slot = merged.setdefault(key, {"ids": set(), "cost": cost})
        slot["ids"].update(affix_ids)
        slot["cost"] = min(slot["cost"], cost)

    max_len = max(lex.max_form_len, max((len(w) for w in lex.free_words), default=0))
    for i in range(n):
        for j in range(i + 1, min(n, i + max_len) + 1):
            sub = word[i:j]

            by_kind: dict[str, list] = {}
            for entry in lex.forms.get(sub, ()):
                by_kind.setdefault(entry.kind, []).append(entry)
            for kind, entries in by_kind.items():
                cost = KNOWN_BASE + (
                    0.0 if any(e.reviewed for e in entries) else UNREVIEWED_PENALTY
                )
                ids = tuple(e.affix_id for e in entries)
                add(i, j, sub, None, kind, ids, cost)
                # Linking vowel after a root: lith + o, speed + o. Only if
                # more word follows the linker.
                if kind == ROOT and j + 1 < n and word[j] in LINKING_VOWELS:
                    add(i, j + 1, sub, word[j], kind, ids, cost + LINKER_COST)

            if sub in lex.free_words:
                if i == 0 and j == n:
                    # The whole word as a single piece: only common everyday
                    # words (frequency-gated) get this "no decomposition"
                    # reading — it saves that/know/phone from junk splits,
                    # while rarer learned words must still decompose.
                    rank = lex.freq_rank.get(sub)
                    if rank is not None and rank <= FREE_WHOLE_MAX_RANK:
                        add(i, j, sub, None, FREE, (),
                            FREE_WHOLE_BASE + FREE_WHOLE_PER_CHAR * n)
                else:
                    cost = FREE_BASE + FREE_PER_CHAR * len(sub) + (
                        FUNCTION_WORD_PENALTY if sub in FUNCTION_WORDS else 0.0
                    )
                    add(i, j, sub, None, FREE, (), cost)
                    if j + 1 < n and word[j] in LINKING_VOWELS:
                        add(i, j + 1, sub, word[j], FREE, (), cost + LINKER_COST)

    edges_from: list[list[_Edge]] = [[] for _ in range(n + 1)]
    for (start, end, surface, linker, kind), slot in sorted(merged.items()):
        edges_from[start].append(
            _Edge(start, end, surface, linker, kind,
                  tuple(sorted(slot["ids"])), slot["cost"])
        )
    return edges_from


def _build_piece(edge: _Edge, lex: Lexicon) -> Piece:
    """Resolve a path edge into an output Piece, enriching free-word matches
    with any learned-root reading of the same surface (meter -> metr row)."""
    kind, ids = edge.kind, edge.affix_ids
    if kind == FREE:
        root_ids = tuple(
            e.affix_id for e in lex.forms.get(edge.surface, ()) if e.kind == ROOT
        )
        if root_ids:
            kind, ids = ROOT, root_ids
    return Piece(
        start=edge.start,
        end=edge.end,
        surface=edge.surface,
        linker=edge.linker,
        kind=kind,
        affix_ids=ids,
        free_match=edge.surface in lex.free_words,
    )


def equivalent(a: Candidate, b: Candidate) -> bool:
    """True when two candidates are the same analysis: identical piece
    boundaries, and each piece pair is either the same surface or grounds to
    at least one common affix row (demo|cracy vs dem·o|cracy)."""
    if len(a.pieces) != len(b.pieces):
        return False
    return all(
        pa.start == pb.start
        and pa.end == pb.end
        and (pa.surface == pb.surface or set(pa.affix_ids) & set(pb.affix_ids))
        for pa, pb in zip(a.pieces, b.pieces)
    )


def segment(word: str, lex: Lexicon, k: int = 5) -> list[Candidate]:
    word = word.strip().lower()
    n = len(word)
    if n == 0:
        return []
    edges_from = _build_edges(word, lex)

    tie = count()
    # heap entries: (cost, tiebreak, position, phase, last_was_unknown, path)
    heap: list[tuple] = [(0.0, next(tie), 0, P_PRE, False, ())]
    results: list[Candidate] = []
    expansions = 0

    while heap and len(results) < k and expansions < MAX_EXPANSIONS:
        cost, _, pos, phase, last_unknown, path = heapq.heappop(heap)

        if pos == n:
            if phase in ACCEPTING:
                cand = Candidate(tuple(_build_piece(e, lex) for e in path), cost)
                # Cheaper-first order means the kept variant of an analysis is
                # always its best-cost form (direct match beats linker split).
                if not any(equivalent(cand, prev) for prev in results):
                    results.append(cand)
            continue

        expansions += 1
        prev = path[-1] if path else None

        for edge in edges_from[pos]:
            nxt = _next_phase(phase, edge.kind)
            if nxt is None:
                continue
            extra = 0.0
            if prev is not None and {prev.kind, edge.kind} == {ROOT, FREE}:
                extra = MIXED_ROOT_PENALTY
            heapq.heappush(heap, (
                cost + edge.cost + extra, next(tie),
                edge.end, nxt, False, path + (edge,),
            ))

        if not last_unknown:  # unknown spans are maximal
            for end in range(pos + 1, n + 1):
                nxt = _next_phase(phase, UNKNOWN)
                ucost = UNKNOWN_BASE + UNKNOWN_PER_CHAR * (end - pos)
                heapq.heappush(heap, (
                    cost + ucost, next(tie), end, nxt, True,
                    path + (_Edge(pos, end, word[pos:end], None, UNKNOWN, (), ucost),),
                ))

    return results
