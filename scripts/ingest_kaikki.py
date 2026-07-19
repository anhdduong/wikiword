"""Bulk-ingest a kaikki.org wiktextract dump into the etymology table.

Optional preprocessing step (plan §9): the lazy per-word retrieval in
app/retrieve.py works without it; this just pre-fills the local store so
lookups never need the network. Accepts .jsonl or .jsonl.gz (e.g. the
English-only extract from kaikki.org).

Idempotent: re-ingesting a word replaces its previous kaikki_dump rows.
Dump rows carry no source_url — we didn't fetch a per-word URL for them;
grounding must fetch one if it wants a user-facing citation.

Usage: python -m scripts.ingest_kaikki <dump.jsonl[.gz]> [db_path]
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from app.db import DEFAULT_DB_PATH, connect, migrate


def ingest(conn, dump_path: str | Path) -> dict[str, int]:
    dump_path = Path(dump_path)
    opener = gzip.open if dump_path.suffix == ".gz" else open
    stats = {"lines": 0, "words": 0, "rows": 0}
    pending: dict[str, list[tuple]] = {}
    # Words whose old dump rows were already replaced in this run: a word's
    # entries may straddle a flush boundary, and only the first flush may
    # delete its previous rows.
    replaced: set[str] = set()

    def flush():
        with conn:
            for word, rows in pending.items():
                if word not in replaced:
                    conn.execute(
                        "DELETE FROM etymology WHERE word = ?"
                        " AND source = 'kaikki_dump'",
                        (word,),
                    )
                    replaced.add(word)
                conn.executemany(
                    "INSERT INTO etymology (word, pos, etymology_text, source)"
                    " VALUES (?, ?, ?, 'kaikki_dump')",
                    rows,
                )
                stats["rows"] += len(rows)
        stats["words"] = len(replaced)
        pending.clear()

    with opener(dump_path, "rt", encoding="utf-8") as f:
        for line in f:
            stats["lines"] += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("lang_code") != "en":
                continue
            word = entry.get("word")
            text = (entry.get("etymology_text") or "").strip()
            if not word or not text:
                continue
            pending.setdefault(word, []).append((word, entry.get("pos"), text))
            if len(pending) >= 5000:
                flush()
    flush()
    return stats


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    conn = connect(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB_PATH)
    migrate(conn)
    stats = ingest(conn, sys.argv[1])
    print(f"{stats['lines']} lines -> {stats['words']} words, {stats['rows']} rows")
