"""Pre-warm the word cache so interactive lookups are instant.

Runs the real /lookup pipeline over the most frequent wordlist words (or a
custom list), skipping words already cached under the current model_version
— re-runs are incremental and cost nothing for already-warmed words. With
LLM credentials configured each uncached word can spend up to two API
calls, so the default sleep respects Gemini's free-tier RPM, and the run
stops early after several consecutive degraded (uncached) responses —
usually an exhausted daily quota. Everything warmed before the stop stays
cached; just re-run later to continue.

Usage:
  python -m scripts.warm_cache                 # top 1000 wordlist words
  python -m scripts.warm_cache --top 5000
  python -m scripts.warm_cache --words mylist.txt
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from scripts.eval_batch import load_wordlist

ROOT = Path(__file__).parent.parent


def warm(
    words: list[str],
    db: Path,
    sleep_s: float,
    stop_after: int = 5,
    log=print,
) -> dict[str, int]:
    from fastapi.testclient import TestClient

    from app.db import connect
    from app.main import create_app
    from app.version import model_version

    conn = connect(db)
    conn_cached = {r["word"] for r in conn.execute(
        "SELECT word FROM word_cache WHERE model_version = ?",
        (model_version(),),
    )}
    conn.close()
    todo = [w for w in words if w not in conn_cached]
    stats = {"requested": len(words), "cached": len(words) - len(todo),
             "warmed": 0, "degraded": 0, "errors": 0}
    consecutive = 0
    with TestClient(create_app(db)) as client:
        for i, w in enumerate(todo):
            if i and sleep_s:
                time.sleep(sleep_s)
            resp = client.get("/lookup", params={"word": w})
            if resp.status_code != 200:
                stats["errors"] += 1
                continue
            note = resp.json().get("status_note") or ""
            if "uncached" in note:
                stats["degraded"] += 1
                consecutive += 1
                if consecutive >= stop_after:
                    log(f"stopping after {consecutive} consecutive degraded"
                        " responses (LLM quota exhausted?); warmed words are"
                        " cached — re-run later to resume")
                    break
            else:
                stats["warmed"] += 1
                consecutive = 0
            if (i + 1) % 25 == 0:
                log(f"... {i + 1}/{len(todo)} words")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=1000,
                    help="warm the N most frequent wordlist words")
    ap.add_argument("--words", type=Path,
                    help="file with one word per line (overrides --top)")
    ap.add_argument("--sleep", type=float,
                    help="seconds between uncached words (default: 13 with"
                         " LLM credentials — free-tier RPM — else 0.3)")
    ap.add_argument("--db", type=Path, default=ROOT / "wikiword.db")
    ap.add_argument("--stop-after", type=int, default=5,
                    help="stop after this many consecutive degraded words")
    args = ap.parse_args()

    from app import llm

    sleep_s = args.sleep if args.sleep is not None else (
        13.0 if llm.is_enabled() else 0.3)
    if args.words:
        words = [w.strip().lower() for w in args.words.read_text().split()
                 if w.strip()]
    else:
        words = load_wordlist(args.top)
    stats = warm(words, args.db, sleep_s, args.stop_after)
    print(f"{stats['warmed']} warmed, {stats['cached']} already cached,"
          f" {stats['degraded']} degraded, {stats['errors']} errors")


if __name__ == "__main__":
    main()
