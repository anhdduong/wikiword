"""FastAPI service: GET /lookup?word=... (plan §1, milestone 3).

Current pipeline: word_cache hit? -> return; else segment() -> cache raw
candidates. Reranking (milestone 5), grounding (6), and prose assembly (7)
slot in between segment() and the cache write.

The lexicon is immutable and loaded once at startup; SQLite connections are
per-request (cheap, and sqlite3 objects are not thread-safe across the
threadpool FastAPI runs sync endpoints on).
"""

from __future__ import annotations

import difflib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from dataclasses import asdict

from app import assemble as assemble_module
from app import compose as compose_module
from app import ground as ground_module
from app import llm as llm_module
from app import rerank as rerank_module
from app import retrieve as retrieve_module
from app.db import DEFAULT_DB_PATH, connect, migrate
from app.retrieve import prose
from app.lexicon import load_lexicon
from app.segment import Candidate, segment
from app.version import model_version

WORD_RE = re.compile(r"^[a-z]{1,40}$")


def _candidate_dict(c: Candidate) -> dict:
    return {
        "cost": round(c.cost, 3),
        "pieces": [
            {
                "surface": p.surface,
                "span": [p.start, p.end],
                "kind": p.kind,
                "linker": p.linker,
                "affix_ids": list(p.affix_ids),
                "free_match": p.free_match,
            }
            for p in c.pieces
        ],
    }


def create_app(db_path: str | Path = DEFAULT_DB_PATH) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(db_path)
        migrate(conn)
        app.state.lexicon = load_lexicon(conn)
        conn.close()
        yield

    app = FastAPI(title="wikiword", lifespan=lifespan)
    app.state.db_path = db_path

    @app.get("/lookup")
    def lookup(word: str):
        w = word.strip().lower()
        if not WORD_RE.match(w):
            raise HTTPException(
                status_code=400, detail="word must be 1-40 ASCII letters"
            )
        version = model_version()
        conn = connect(db_path)
        try:
            row = conn.execute(
                "SELECT payload FROM word_cache WHERE word = ? AND model_version = ?",
                (w, version),
            ).fetchone()
            if row:
                return json.loads(row["payload"])

            candidates = segment(w, app.state.lexicon)

            # llm_rerank (plan §4): closed-set choice; on any failure the
            # cost order stands. A *transient* failure must not be frozen
            # into the cache, so we serve the response but skip the write.
            # The rerank call and the etymology fetch are independent, so
            # the LLM call rides a worker thread while retrieval keeps the
            # request thread (and its non-thread-safe DB connection).
            llm_enabled = llm_module.is_enabled()
            rerank_future = None
            if llm_enabled:
                pool = ThreadPoolExecutor(max_workers=1)
                rerank_future = pool.submit(rerank_module.rerank, w, candidates)
                pool.shutdown(wait=False)

            # kaikki repeats one etymology under several parts of speech;
            # keep each distinct prose text once.
            records = []
            seen_prose: set[str] = set()
            for r in retrieve_module.retrieve(conn, w):
                p = prose(r.text)
                if p not in seen_prose:
                    seen_prose.add(p)
                    records.append(r)

            # A word absent from the frequency wordlist that every source
            # definitively doesn't know is probably a misspelling: flag it,
            # suggest close wordlist matches, and never synthesize a modern
            # usage for it — no dictionary knows it, so there is no grounded
            # basis for one. Transport failures leave no negative-cache row
            # and so never trigger this.
            unrecognized = (
                not records
                and w not in app.state.lexicon.free_words
                and conn.execute(
                    "SELECT 1 FROM etymology WHERE word = ?"
                    " AND etymology_text IS NULL",
                    (w,),
                ).fetchone() is not None
            )
            suggestions = (
                difflib.get_close_matches(
                    w, app.state.lexicon.free_words, n=3, cutoff=0.8)
                if unrecognized else []
            )
            unrecognized_note = (
                "word not found in any dictionary source — possible"
                " misspelling" if unrecognized else None
            )

            result = None
            rerank_degraded = False
            if llm_enabled:
                result = rerank_future.result()
                rerank_degraded = result is None and len(candidates) > 1
                note = (
                    None
                    if result is not None
                    else "rerank failed for this request; showing lowest-cost"
                         " segmentation (uncached)"
                )
            else:
                note = "LLM calls disabled: no API credentials"
            chosen_index = result.choice if result else 0

            # ground() + status() (plan §5): verify the chosen segmentation
            # against the affix table and retrieved etymology.
            grounding = ground_module.ground(
                conn, w, candidates[chosen_index], records
            )
            # Prose fields. With the LLM: llm_assemble (plan §4), synthesized
            # only from grounded facts. Without it: deterministic fallback —
            # glosses joined for the literal sense, a fetched dictionary
            # definition for modern usage. Either way, no facts -> null
            # fields, never invented prose; a transient failure serves the
            # response uncached, same rule as the rerank.
            prose_texts = [prose(r.text) for r in records]
            literal = modern = None
            assemble_degraded = False
            assemble_note = None
            if llm_enabled:
                if not assemble_module.has_facts(grounding.morphemes):
                    assemble_note = (
                        "no verified morpheme meanings; prose fields omitted"
                    )
                else:
                    assembled = assemble_module.assemble(
                        w, grounding.morphemes, prose_texts
                    )
                    if assembled is None:
                        assemble_degraded = True
                        assemble_note = (
                            "prose assembly failed for this request (uncached)"
                        )
                    else:
                        literal = assembled.literal_meaning
                        modern = None if unrecognized else assembled.modern_usage
            else:
                # The literal sense needs glosses, but the dictionary
                # definition is independently grounded — fetch it regardless
                # (except for unrecognized words, which no dictionary has).
                literal = compose_module.literal_meaning(grounding.morphemes)
                if unrecognized:
                    modern, definitive = None, True
                else:
                    modern, definitive = compose_module.fetch_definition(w)
                assemble_degraded = not definitive
                fallback_parts = [
                    "literal sense composed from affix glosses" if literal else
                    "no verified morpheme meanings; literal sense omitted"
                ]
                if modern:
                    fallback_parts.append(
                        "modern usage quoted from Free Dictionary definition"
                    )
                elif not definitive:
                    fallback_parts.append(
                        "dictionary lookup failed for this request (uncached)"
                    )
                assemble_note = "; ".join(fallback_parts)

            note_parts = [
                p for p in (unrecognized_note, grounding.status_note,
                            assemble_note, note) if p
            ]

            payload = {
                "word": w,
                "status": grounding.status,
                "status_note": "; ".join(note_parts) or None,
                "unrecognized": unrecognized,
                "suggestions": suggestions,
                "literal_meaning": literal,
                "modern_usage": modern,
                "morphemes": [asdict(m) for m in grounding.morphemes],
                "conflicts": list(grounding.conflicts),
                "etymology": [
                    {"text": t, "source": r.source, "url": r.url}
                    for r, t in zip(records, prose_texts)
                ],
                "chosen_index": chosen_index,
                "rerank": (
                    {
                        "model": llm_module.resolved_model(rerank_module.RERANK_MODEL),
                        "reason": result.reason,
                    }
                    if result is not None
                    else None
                ),
                "candidates": [_candidate_dict(c) for c in candidates],
                "model_version": version,
            }
            if not (rerank_degraded or assemble_degraded):
                with conn:
                    conn.execute(
                        "INSERT INTO word_cache (word, status, payload, model_version)"
                        " VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(word) DO UPDATE SET"
                        "   status = excluded.status,"
                        "   payload = excluded.payload,"
                        "   model_version = excluded.model_version,"
                        "   created_at = CURRENT_TIMESTAMP",
                        (w, payload["status"], json.dumps(payload), version),
                    )
            return payload
        finally:
            conn.close()

    from app.admin import router as admin_router

    app.include_router(admin_router)

    # Production serving: mount the built front end (front/dist) at /.
    # Registered after the API routes, so /lookup keeps priority. In dev,
    # `npm run dev` in front/ serves the UI and proxies /lookup here.
    front_dist = Path(__file__).parent.parent / "front" / "dist"
    if front_dist.is_dir():
        app.mount("/", StaticFiles(directory=front_dist, html=True), name="front")

    return app


app = create_app()
