"""Seed the affix / affix_form tables from seed/affixes.csv.

Idempotent: rows are keyed on (canonical, type, gloss). Re-running updates
origin/source_form/notes and replaces the form set, but never touches
`reviewed` or `citations` on existing rows — those are curation state owned
by the review flow, not the seed file.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

from app.db import DEFAULT_DB_PATH, connect, migrate

SEED_CSV = Path(__file__).parent.parent / "seed" / "affixes.csv"

VALID_TYPES = {"prefix", "root", "suffix", "combining_form"}


def seed(conn: sqlite3.Connection, csv_path: Path = SEED_CSV) -> dict[str, int]:
    stats = {"inserted": 0, "updated": 0, "forms": 0}
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, start=2):  # start=2: header is line 1
        canonical, type_, gloss = row["canonical"], row["type"], row["gloss"]
        if not canonical or type_ not in VALID_TYPES or not gloss:
            raise ValueError(f"{csv_path.name} line {i}: bad row {row!r}")
        forms = [s for s in row["forms"].split("|") if s]
        if not forms:
            raise ValueError(f"{csv_path.name} line {i}: no forms for {canonical!r}")

        with conn:
            existing = conn.execute(
                "SELECT id FROM affix WHERE canonical = ? AND type = ? AND gloss = ?",
                (canonical, type_, gloss),
            ).fetchone()
            if existing:
                affix_id = existing["id"]
                conn.execute(
                    "UPDATE affix SET origin_lang = ?, source_form = ?, notes = ?"
                    " WHERE id = ?",
                    (row["origin_lang"], row["source_form"] or None,
                     row["notes"] or None, affix_id),
                )
                stats["updated"] += 1
            else:
                cur = conn.execute(
                    "INSERT INTO affix (canonical, type, origin_lang, source_form,"
                    " gloss, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (canonical, type_, row["origin_lang"],
                     row["source_form"] or None, gloss, row["notes"] or None),
                )
                affix_id = cur.lastrowid
                stats["inserted"] += 1

            conn.execute("DELETE FROM affix_form WHERE affix_id = ?", (affix_id,))
            conn.executemany(
                "INSERT INTO affix_form (form, affix_id) VALUES (?, ?)",
                [(form, affix_id) for form in forms],
            )
            stats["forms"] += len(forms)

    # New rows change what the segmenter can match, but the lexicon isn't
    # part of model_version — clear the cache so stale segmentations can't
    # be served. (Same rule as admin mutations. A running server must still
    # be restarted to reload its in-memory lexicon.)
    if stats["inserted"]:
        with conn:
            stats["cache_cleared"] = conn.execute(
                "DELETE FROM word_cache"
            ).rowcount
    return stats


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    conn = connect(db_path)
    migrate(conn)
    stats = seed(conn)
    print(f"Seeded {db_path}: {stats['inserted']} inserted, "
          f"{stats['updated']} updated, {stats['forms']} forms")
    if stats["inserted"]:
        print(f"word_cache cleared ({stats['cache_cleared']} rows) — restart"
              " the server to reload the lexicon")
