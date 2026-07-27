"""Backfill modern_usage into already-cached word_cache rows.

Removing/re-adding modern_usage deliberately didn't bump model_version (see
app/version.py) to avoid evicting the prewarmed cache, so a cache hit just
returns the old payload as-is — a row cached before Merriam-Webster was
wired in has no modern_usage key at all until it's regenerated. This
back-fetches it in place, spending only Merriam-Webster's free-tier quota
(1000/day, a separate pool from the LLM's) without touching literal_meaning,
morphemes, or any rerank/assemble data already paid for.

Idempotent: a row is skipped once its payload has a "modern_usage" key
(even if the value is null, meaning "fetched, no definition found" — a
definitive answer, not a thing to retry). Stops after a run of consecutive
non-definitive responses (network trouble or a spent daily quota); re-run
later to resume.

Usage: python -m scripts.backfill_modern_usage
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app import compose
from app.db import connect
from app.version import model_version

ROOT = Path(__file__).parent.parent


def backfill(
    db: Path, sleep_s: float = 0.3, stop_after: int = 5, log=print
) -> dict[str, int]:
    conn = connect(db)
    version = model_version()
    rows = conn.execute(
        "SELECT word, payload FROM word_cache WHERE model_version = ?",
        (version,),
    ).fetchall()
    parsed = ((r["word"], json.loads(r["payload"])) for r in rows)
    todo = [(w, p) for w, p in parsed if "modern_usage" not in p]
    stats = {"total": len(rows), "todo": len(todo), "found": 0,
              "definitive_miss": 0, "skipped": 0}
    consecutive = 0
    for i, (word, payload) in enumerate(todo):
        if i and sleep_s:
            time.sleep(sleep_s)
        modern, definitive = compose.fetch_definition(word)
        if not definitive:
            consecutive += 1
            stats["skipped"] += 1
            if consecutive >= stop_after:
                log(f"stopping after {consecutive} consecutive non-definitive"
                    " responses (quota exhausted or network trouble);"
                    " re-run later to resume")
                break
            continue
        consecutive = 0
        payload["modern_usage"] = modern
        if modern:
            stats["found"] += 1
            note = payload.get("status_note")
            addition = "modern usage quoted from Merriam-Webster definition"
            payload["status_note"] = (
                f"{note}; {addition}" if note else addition
            )
        else:
            stats["definitive_miss"] += 1
        with conn:
            conn.execute(
                "UPDATE word_cache SET payload = ? WHERE word = ?",
                (json.dumps(payload), word),
            )
        if (i + 1) % 100 == 0:
            log(f"... {i + 1}/{len(todo)} words")
    conn.close()
    return stats


def main() -> None:
    stats = backfill(ROOT / "wikiword.db")
    print(f"{stats['found']} found, {stats['definitive_miss']} definitive"
          f" misses, {stats['skipped']} skipped (retry later),"
          f" {stats['todo']} of {stats['total']} needed backfill")


if __name__ == "__main__":
    main()
