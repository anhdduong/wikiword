"""Pull Wiktionary's English affix lists into a draft CSV for curation.

Enumerates Category:English prefixes / suffixes via the MediaWiki API, then
fetches each entry's kaikki.org JSON for glosses, origin language, and
alternative forms. Rows use the seed/affixes.csv schema so curated rows can
be pasted straight in — but nothing here touches the database: the draft is
raw fetched material, and a human decides what becomes truth (rows merged
into affixes.csv still start reviewed=0, as always).

Resume-able: entries already curated in seed/affixes.csv or already present
in the draft are skipped, so an interrupted run picks up where it left off.

Grow the seed table in batches and re-run scripts/eval_batch.py after each
merge — every new form is a new way to split every word.

Usage: python -m scripts.pull_wiktionary_affixes [--type prefix|suffix]
       [--limit N] [--sleep S] [--out seed/draft_affixes.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

from app.retrieve import HttpGet, _default_http_get, kaikki_url

REPO = Path(__file__).parent.parent
SEED_CSV = REPO / "seed" / "affixes.csv"
DEFAULT_OUT = REPO / "seed" / "draft_affixes.csv"

API_BASE = "https://en.wiktionary.org/w/api.php"
CATEGORIES = {"prefix": "English prefixes", "suffix": "English suffixes"}
HEADER = ["canonical", "type", "origin_lang", "source_form", "gloss",
          "notes", "forms", "wikt_entry"]

# The segmenter only matches lowercase a-z, so titles with capitals,
# apostrophes, or diacritics can never be used and are skipped up front.
TITLE_RE = {
    "prefix": re.compile(r"^[a-z]+-$"),
    "suffix": re.compile(r"^-[a-z]+-?$"),
}

# Templates wiktextract emits for "derived from language X" claims.
DERIVATION_TEMPLATES = frozenset(
    "der bor inh lbor slbor uder ubor derived borrowed inherited".split()
)

LANG_NAMES = {
    "grc": "Ancient Greek", "la": "Latin", "LL.": "Late Latin",
    "ML.": "Medieval Latin", "NL.": "New Latin", "ang": "Old English",
    "enm": "Middle English", "fr": "French", "fro": "Old French",
    "frm": "Middle French", "non": "Old Norse", "de": "German",
    "gml": "Middle Low German", "nl": "Dutch", "it": "Italian",
    "es": "Spanish", "ar": "Arabic", "he": "Hebrew", "sa": "Sanskrit",
}


def _ascii(s: str) -> str:
    """Strip to ASCII, dropping accents (mónos -> monos, μόνος -> '')."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def category_members(
    category: str, http_get: HttpGet, sleep_s: float = 0.0
) -> list[str]:
    """Every page title in the Wiktionary category, following pagination."""
    titles: list[str] = []
    params = {
        "action": "query", "list": "categorymembers", "format": "json",
        "cmtitle": f"Category:{category}", "cmlimit": "500",
        "cmprop": "title", "cmtype": "page",
    }
    while True:
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        status, body = http_get(url)
        if status != 200:
            raise RuntimeError(f"Wiktionary API HTTP {status} for {category}")
        data = json.loads(body)
        titles += [m["title"] for m in data["query"]["categorymembers"]]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            return titles
        params["cmcontinue"] = cont
        if sleep_s:
            time.sleep(sleep_s)


def _origin(entry: dict) -> tuple[str, str]:
    """(origin_lang, source_form) from the first derivation template that
    names a language we can label; both empty when there is none."""
    for t in entry.get("etymology_templates") or []:
        if t.get("name") not in DERIVATION_TEMPLATES:
            continue
        args = t.get("args") or {}
        lang = LANG_NAMES.get(args.get("2", ""))
        if not lang:
            continue
        src = _ascii(args.get("3") or "").strip("*- ")
        if not src:
            # Non-Latin source terms romanize in the expansion's parens:
            # "Ancient Greek μόνος (mónos)" -> "monos".
            m = re.search(r"\(([^)]*)\)", _ascii(t.get("expansion") or ""))
            src = m.group(1).strip("*- ") if m else ""
        return lang, src
    return "", ""


def _gloss_and_note(entries: list[dict]) -> tuple[str, str]:
    """Up to two distinct sense glosses; alt-of-only entries instead get a
    note naming the main row their surface belongs to."""
    glosses: list[str] = []
    alt_targets: list[str] = []
    for entry in entries:
        for sense in entry.get("senses") or []:
            alts = (sense.get("alt_of") or []) + (sense.get("form_of") or [])
            if alts:
                alt_targets += [a["word"] for a in alts if a.get("word")]
                continue
            for g in sense.get("glosses") or []:
                g = g.strip().rstrip(".")
                if g and g not in glosses:
                    glosses.append(g)
    note = ""
    if not glosses and alt_targets:
        note = (f"alternative form of {alt_targets[0]} — consider adding to"
                " that row's forms instead")
    return "; ".join(glosses[:2]), note


def _forms(title: str, entries: list[dict]) -> str:
    forms = [title.strip("-")]
    for entry in entries:
        for f in entry.get("forms") or []:
            if "alternative" not in (f.get("tags") or []):
                continue
            s = _ascii(f.get("form") or "").strip("-").lower()
            if re.fullmatch(r"[a-z]+", s) and s not in forms:
                forms.append(s)
    return "|".join(forms)


def draft_row(title: str, typ: str, body: bytes) -> list[str] | None:
    """One affixes.csv-shaped row from a kaikki JSONL body, or None when
    the entry carries nothing usable."""
    entries = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries = [e for e in entries if e.get("pos") == typ] or entries
    if not entries:
        return None
    gloss, note = _gloss_and_note(entries)
    if not gloss and not note:
        return None
    origin, src = _origin(entries[0])
    return [title, typ, origin, src, gloss, note, _forms(title, entries), title]


def known_rows(*csv_paths: Path) -> set[tuple[str, str]]:
    known: set[tuple[str, str]] = set()
    for p in csv_paths:
        if not p.exists():
            continue
        with p.open(newline="") as fh:
            for row in csv.DictReader(fh):
                known.add((row["canonical"], row["type"]))
    return known


def pull(
    types: list[str],
    out: Path = DEFAULT_OUT,
    seed_csv: Path = SEED_CSV,
    http_get: HttpGet = _default_http_get,
    sleep_s: float = 0.2,
    limit: int | None = None,
    log=print,
) -> dict[str, int]:
    out = Path(out)
    known = known_rows(seed_csv, out)
    stats = {"titles": 0, "known": 0, "no_kaikki": 0, "no_facts": 0,
             "written": 0}
    new_file = not out.exists()
    with out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(HEADER)
        for typ in types:
            titles = category_members(CATEGORIES[typ], http_get, sleep_s)
            titles = [t for t in titles if TITLE_RE[typ].fullmatch(t)]
            if limit is not None:
                titles = titles[:limit]
            log(f"{CATEGORIES[typ]}: {len(titles)} usable titles")
            for title in titles:
                stats["titles"] += 1
                if (title, typ) in known:
                    stats["known"] += 1
                    continue
                status, body = http_get(kaikki_url(title))
                if sleep_s:
                    time.sleep(sleep_s)
                if status != 200:
                    stats["no_kaikki"] += 1
                    continue
                row = draft_row(title, typ, body)
                if row is None:
                    stats["no_facts"] += 1
                    continue
                writer.writerow(row)
                fh.flush()  # interrupt-safe: written rows survive for resume
                known.add((title, typ))
                stats["written"] += 1
    return stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--type", choices=["prefix", "suffix"],
                    help="pull one category only (default: both)")
    ap.add_argument("--limit", type=int, help="max titles per category")
    ap.add_argument("--sleep", type=float, default=0.2,
                    help="seconds between requests (default 0.2)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    types = [args.type] if args.type else ["prefix", "suffix"]
    stats = pull(types, out=args.out, sleep_s=args.sleep, limit=args.limit)
    print(f"{stats['written']} draft rows -> {args.out}"
          f" ({stats['known']} already curated/drafted,"
          f" {stats['no_kaikki']} without kaikki entries,"
          f" {stats['no_facts']} without usable facts)")


if __name__ == "__main__":
    main()
