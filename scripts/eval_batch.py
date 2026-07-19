"""Batch evaluation: sweep many words through the real /lookup pipeline and
report aggregate quality stats plus the worst cases for curation.

Usage:
  python -m scripts.eval_batch                    # top 300 wordlist words, no LLM
  python -m scripts.eval_batch --top 1000
  python -m scripts.eval_batch --words mylist.txt
  python -m scripts.eval_batch --gold seed/gold_segmentations.tsv
  python -m scripts.eval_batch --llm --top 40 --sleep 13   # respects free-tier RPM

Default runs disable the LLM so the sweep is free and fast — segmentation,
retrieval, and grounding are where most correctness lives. --llm exercises
rerank + assemble too; keep --top small and --sleep high (2 API calls per
uncached word). Repeat runs skip cached words, so sweeps are incremental.

Per-word results go to a JSONL file (default eval_results.jsonl, gitignored);
the summary and worst offenders print to stdout. Unknown spans also land in
the review queue for the admin UI, same as interactive lookups.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

WORD_RE = re.compile(r"^[a-z]{4,40}$")
ROOT = Path(__file__).parent.parent


def load_wordlist(top: int) -> list[str]:
    words = []
    for line in (ROOT / "seed" / "en_50k.txt").read_text().splitlines():
        w = line.split()[0] if line.strip() else ""
        if WORD_RE.match(w):
            words.append(w)
        if len(words) >= top:
            break
    return words


def load_gold(path: Path) -> dict[str, list[str]]:
    gold = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, expected = line.split("\t")
        gold[word] = expected.split("|")
    return gold


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=300,
                    help="evaluate the N most frequent wordlist words")
    ap.add_argument("--words", type=Path,
                    help="file with one word per line (overrides --top)")
    ap.add_argument("--gold", type=Path,
                    help="TSV of word<TAB>expected|pieces to check against")
    ap.add_argument("--llm", action="store_true",
                    help="let configured LLM credentials be used (costs quota)")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="seconds between words (be polite; 13+ with --llm)")
    ap.add_argument("--db", type=Path, default=ROOT / "wikiword.db")
    ap.add_argument("--out", type=Path, default=ROOT / "eval_results.jsonl")
    args = ap.parse_args()

    if not args.llm:
        # Kill both providers before the app spins up: no quota spend, and
        # cache rows are keyed under the no-LLM model_version.
        import os

        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        from app import llm

        llm.get_client = lambda: None

    from fastapi.testclient import TestClient

    from app.main import create_app

    if args.words:
        words = [w.strip().lower() for w in args.words.read_text().split()
                 if w.strip()]
    else:
        words = load_wordlist(args.top)
    gold = load_gold(args.gold) if args.gold else {}
    for w in gold:
        if w not in words:
            words.append(w)

    counts = {"grounded": 0, "partial": 0, "unverified": 0}
    literal_n = modern_n = 0
    unknown_words: list[tuple[str, list[str]]] = []
    conflict_words: list[str] = []
    degraded_words: list[str] = []
    errors: list[tuple[str, str]] = []
    gold_misses: list[tuple[str, list[str], list[str]]] = []
    rerank_overrides: list[str] = []

    started = time.monotonic()
    with TestClient(create_app(args.db)) as client, open(args.out, "w") as out:
        for i, w in enumerate(words):
            if i and args.sleep:
                time.sleep(args.sleep)
            try:
                resp = client.get("/lookup", params={"word": w})
                body = resp.json()
                if resp.status_code != 200:
                    raise RuntimeError(body.get("detail", resp.status_code))
            except Exception as exc:
                errors.append((w, str(exc)))
                continue

            counts[body["status"]] = counts.get(body["status"], 0) + 1
            literal_n += body["literal_meaning"] is not None
            modern_n += body["modern_usage"] is not None
            surfaces = [m["surface"] for m in body["morphemes"]]
            unknowns = [m["surface"] for m in body["morphemes"]
                        if m["type"] == "unknown"]
            if unknowns:
                unknown_words.append((w, unknowns))
            if body["conflicts"]:
                conflict_words.append(w)
            if "uncached" in (body["status_note"] or ""):
                degraded_words.append(w)
            if body["chosen_index"] != 0:
                rerank_overrides.append(w)
            if w in gold and surfaces != gold[w]:
                gold_misses.append((w, gold[w], surfaces))
            out.write(json.dumps({
                "word": w, "status": body["status"], "pieces": surfaces,
                "unknown": unknowns, "literal": body["literal_meaning"],
                "modern": body["modern_usage"],
                "conflicts": body["conflicts"],
                "note": body["status_note"],
            }) + "\n")
            if (i + 1) % 25 == 0:
                print(f"... {i + 1}/{len(words)} words", flush=True)

    n = sum(counts.values())
    elapsed = time.monotonic() - started
    print(f"\n=== {n} words evaluated in {elapsed:.0f}s "
          f"({'LLM on' if args.llm else 'LLM off'}) ===")
    for status in ("grounded", "partial", "unverified"):
        c = counts.get(status, 0)
        print(f"  {status:<10} {c:>5}  ({c / n:.0%})" if n else "")
    print(f"  prose: literal {literal_n}/{n}, modern {modern_n}/{n}")
    print(f"  words with unknown spans: {len(unknown_words)}")
    for w, spans in unknown_words[:15]:
        print(f"    {w}: {', '.join(spans)}")
    if len(unknown_words) > 15:
        print(f"    ... and {len(unknown_words) - 15} more (see {args.out})")
    if conflict_words:
        print(f"  conflicts: {', '.join(conflict_words)}")
    if degraded_words:
        print(f"  degraded/uncached: {', '.join(degraded_words)}")
    if rerank_overrides:
        print(f"  rerank overrode cost order: {', '.join(rerank_overrides)}")
    if errors:
        print(f"  ERRORS: {errors}")
    if gold:
        ok = len(gold) - len(gold_misses)
        print(f"  gold segmentations: {ok}/{len(gold)} correct")
        for w, want, got in gold_misses:
            print(f"    MISS {w}: wanted {'|'.join(want)}, got {'|'.join(got)}")
    print(f"per-word results: {args.out}")


if __name__ == "__main__":
    main()
