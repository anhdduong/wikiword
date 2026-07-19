"""Fetch each seed row's Wiktionary entry and attach verified citation URLs.

Per the grounding rules, only URLs actually fetched (HTTP 200) get attached
as citations. Entries that 404 are reported and left without a citation —
fix the wikt_entry column in seed/affixes.csv and re-run.

Usage: python -m scripts.verify_citations [db_path]
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request

from app.db import DEFAULT_DB_PATH, connect
from app.seed import SEED_CSV

USER_AGENT = "wikiword-seed-verifier/0.1 (etymology tool; one-time seed check)"
DELAY_S = 0.3


def check_url(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main(db_path=DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    with open(SEED_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("wikt_entry")]

    ok, missing, skipped = 0, [], 0
    for row in rows:
        db_row = conn.execute(
            "SELECT id, citations FROM affix WHERE canonical=? AND type=? AND gloss=?",
            (row["canonical"], row["type"], row["gloss"]),
        ).fetchone()
        if db_row is None:
            continue
        url = "https://en.wiktionary.org/wiki/" + urllib.parse.quote(row["wikt_entry"])
        citations = json.loads(db_row["citations"])
        if url in citations:
            skipped += 1
            continue
        status = check_url(url)
        if status == 200:
            citations.append(url)
            with conn:
                conn.execute(
                    "UPDATE affix SET citations=? WHERE id=?",
                    (json.dumps(citations), db_row["id"]),
                )
            ok += 1
        else:
            missing.append((row["canonical"], row["wikt_entry"], status))
        time.sleep(DELAY_S)

    print(f"verified {ok}, already-present {skipped}, missing {len(missing)}")
    for canonical, entry, status in missing:
        print(f"  {status} {canonical}: {entry}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH)
