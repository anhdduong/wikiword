"""Etymology retrieval (plan milestone 4): local table first, then
kaikki.org per-word JSON, then the Free Dictionary API.

Grounding rules honored here:
- Only URLs actually fetched become citation candidates (source_url).
- Definitive empty answers are negative-cached (etymology_text NULL) so we
  don't hammer the sources; network errors are never cached, so transient
  failures retry on the next lookup.

`http_get` is injectable for tests: (url) -> (status_code, body_bytes).
It must raise OSError/URLError on transport failure.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

USER_AGENT = "wikiword/0.1 (etymology lookup tool)"
TIMEOUT_S = 15

HttpGet = Callable[[str], tuple[int, bytes]]


@dataclass(frozen=True)
class EtymologyRecord:
    word: str
    pos: str | None
    text: str
    source: str  # kaikki_dump | kaikki_api | free_dictionary
    url: str | None


def prose(etymology_text: str) -> str:
    """The prose portion of a kaikki etymology_text.

    Newer wiktextract prepends an 'Etymology tree' block (one short lineage
    node per line) before the actual prose. Stored texts keep the raw form —
    the tree lines are useful corroboration for grounding — but only the
    prose should be shown to users or fed to llm_assemble.
    """
    lines = etymology_text.splitlines()
    if not lines or lines[0].strip() != "Etymology tree":
        return etymology_text.strip()
    kept = [
        line for line in lines[1:]
        if len(line) > 60
        or line.startswith(("From ", "Borrowed", "Coined", "By surface"))
    ]
    if kept:
        return " ".join(kept).strip()
    return "\n".join(lines[1:]).strip()


def _default_http_get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def kaikki_url(word: str) -> str:
    return (
        "https://kaikki.org/dictionary/English/meaning/"
        f"{word[0]}/{word[:2]}/{word}.jsonl"
    )


def free_dictionary_url(word: str) -> str:
    return f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"


def _rows_to_records(rows) -> list[EtymologyRecord]:
    return [
        EtymologyRecord(r["word"], r["pos"], r["etymology_text"],
                        r["source"], r["source_url"])
        for r in rows
        if r["etymology_text"]
    ]


def _parse_kaikki(word: str, body: bytes, url: str) -> list[EtymologyRecord]:
    records = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = (entry.get("etymology_text") or "").strip()
        if text:
            records.append(
                EtymologyRecord(word, entry.get("pos"), text, "kaikki_api", url)
            )
    return records


def _parse_free_dictionary(word: str, body: bytes, url: str) -> list[EtymologyRecord]:
    try:
        entries = json.loads(body)
    except json.JSONDecodeError:
        return []
    records = []
    if isinstance(entries, list):
        for entry in entries:
            text = (entry.get("origin") or "").strip()
            if text:
                records.append(
                    EtymologyRecord(word, None, text, "free_dictionary", url)
                )
    return records


def _dedupe(records: list[EtymologyRecord]) -> list[EtymologyRecord]:
    seen: set[tuple] = set()
    out = []
    for r in records:
        key = (r.pos, r.text)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _store(conn: sqlite3.Connection, word: str,
           records: list[EtymologyRecord], negative_source: str | None) -> None:
    with conn:
        if records:
            conn.executemany(
                "INSERT INTO etymology (word, pos, etymology_text, source,"
                " source_url) VALUES (?, ?, ?, ?, ?)",
                [(r.word, r.pos, r.text, r.source, r.url) for r in records],
            )
        elif negative_source:
            conn.execute(
                "INSERT INTO etymology (word, pos, etymology_text, source,"
                " source_url) VALUES (?, NULL, NULL, ?, NULL)",
                (word, negative_source),
            )


def retrieve(
    conn: sqlite3.Connection,
    word: str,
    http_get: HttpGet = _default_http_get,
    allow_network: bool = True,
) -> list[EtymologyRecord]:
    """Etymology records with non-empty prose for `word` (may be empty).

    Any cached row for the word — including a negative entry — resolves the
    lookup locally.
    """
    word = word.strip().lower()
    rows = conn.execute(
        "SELECT word, pos, etymology_text, source, source_url FROM etymology"
        " WHERE word = ? AND lang = 'English'",
        (word,),
    ).fetchall()
    if rows:
        return _rows_to_records(rows)
    if not allow_network:
        return []

    try:
        url = kaikki_url(word)
        status, body = http_get(url)
        if status == 200:
            records = _dedupe(_parse_kaikki(word, body, url))
            _store(conn, word, records, negative_source="kaikki_api")
            return records
        # A 5xx from kaikki is transient: still try the fallback, but don't
        # let an empty fallback answer negative-cache what kaikki might know.
        kaikki_definitive = status == 404

        url = free_dictionary_url(word)
        status, body = http_get(url)
        if status == 200:
            records = _dedupe(_parse_free_dictionary(word, body, url))
            _store(conn, word, records,
                   negative_source="free_dictionary" if kaikki_definitive else None)
            return records
        if status == 404 and kaikki_definitive:
            # Both sources answered and neither knows the word.
            _store(conn, word, [], negative_source="none")
        return []
    except (OSError, urllib.error.URLError):
        return []  # transport failure: no caching, retry next time
