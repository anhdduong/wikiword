"""Admin review view (plan milestone 9): drain review_queue into curated
affix rows.

Actions:
- approve: the queue entry references an existing affix row (an LLM-era
  reviewed=0 seed row) -> mark it reviewed = 1.
- promote: the entry is an unknown span -> create a new affix row
  (reviewed = 1) plus its surface forms.
- dismiss: drop the entry without touching the lexicon.

Every mutation clears word_cache (curation changes segmentation costs and
grounding output) and reloads the in-memory lexicon, so the next lookup
reflects the curated state immediately.

No authentication: this is a local curation tool in the same trust domain
as the SQLite file itself. Put it behind a proxy before exposing it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import connect
from app.lexicon import load_lexicon

router = APIRouter(prefix="/admin")


def _conn(request: Request) -> sqlite3.Connection:
    return connect(request.app.state.db_path)


def _refresh_after_curation(request: Request, conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM word_cache")
    request.app.state.lexicon = load_lexicon(conn)


def _entry(conn: sqlite3.Connection, entry_id: int):
    row = conn.execute(
        "SELECT * FROM review_queue WHERE id = ?", (entry_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="queue entry not found")
    return row


@router.get("/queue")
def list_queue(request: Request):
    conn = _conn(request)
    try:
        entries = []
        for row in conn.execute(
            "SELECT * FROM review_queue ORDER BY created_at, id"
        ):
            proposed = json.loads(row["proposed"])
            affix = None
            if "affix_id" in proposed:
                a = conn.execute(
                    "SELECT id, canonical, type, origin_lang, source_form,"
                    " gloss, reviewed FROM affix WHERE id = ?",
                    (proposed["affix_id"],),
                ).fetchone()
                if a is not None:
                    affix = dict(a)
            entries.append({
                "id": row["id"],
                "surface": row["surface"],
                "seen_in": row["seen_in"],
                "created_at": row["created_at"],
                "proposed": proposed,
                "affix": affix,
            })
        return {"entries": entries}
    finally:
        conn.close()


@router.post("/queue/{entry_id}/approve")
def approve(entry_id: int, request: Request):
    conn = _conn(request)
    try:
        row = _entry(conn, entry_id)
        proposed = json.loads(row["proposed"])
        if "affix_id" not in proposed:
            raise HTTPException(
                status_code=400,
                detail="entry has no affix row; use promote to create one",
            )
        with conn:
            updated = conn.execute(
                "UPDATE affix SET reviewed = 1 WHERE id = ?",
                (proposed["affix_id"],),
            ).rowcount
            if not updated:
                raise HTTPException(
                    status_code=409, detail="referenced affix row no longer exists"
                )
            conn.execute("DELETE FROM review_queue WHERE id = ?", (entry_id,))
            _refresh_after_curation(request, conn)
        return {"approved": proposed["affix_id"], "surface": row["surface"]}
    finally:
        conn.close()


@router.post("/queue/{entry_id}/dismiss")
def dismiss(entry_id: int, request: Request):
    conn = _conn(request)
    try:
        _entry(conn, entry_id)
        with conn:
            conn.execute("DELETE FROM review_queue WHERE id = ?", (entry_id,))
        return {"dismissed": entry_id}
    finally:
        conn.close()


class PromoteBody(BaseModel):
    canonical: str = Field(min_length=1)
    type: Literal["prefix", "root", "suffix", "combining_form"]
    origin_lang: str = Field(min_length=1)
    gloss: str = Field(min_length=1)
    source_form: str | None = None
    notes: str | None = None
    forms: list[str] = Field(min_length=1)


@router.post("/queue/{entry_id}/promote")
def promote(entry_id: int, body: PromoteBody, request: Request):
    conn = _conn(request)
    try:
        row = _entry(conn, entry_id)
        forms = [f.strip().lower() for f in body.forms if f.strip()]
        if not forms:
            raise HTTPException(status_code=400, detail="no usable forms")
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO affix (canonical, type, origin_lang,"
                    " source_form, gloss, notes, reviewed)"
                    " VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (body.canonical, body.type, body.origin_lang,
                     body.source_form, body.gloss, body.notes),
                )
                affix_id = cur.lastrowid
                conn.executemany(
                    "INSERT INTO affix_form (form, affix_id) VALUES (?, ?)",
                    [(f, affix_id) for f in forms],
                )
                conn.execute(
                    "DELETE FROM review_queue WHERE id = ?", (entry_id,)
                )
                _refresh_after_curation(request, conn)
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="an affix with this canonical/type/gloss already exists",
            )
        return {"promoted": affix_id, "surface": row["surface"], "forms": forms}
    finally:
        conn.close()
