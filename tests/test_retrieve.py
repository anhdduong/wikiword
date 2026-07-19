import json

import pytest

from app.db import connect, migrate
from app.retrieve import free_dictionary_url, kaikki_url, retrieve
from scripts.ingest_kaikki import ingest

KAIKKI_BODY = (
    json.dumps({"pos": "adj", "etymology_text":
                "From French monolithique. By surface analysis, monolith + -ic."})
    + "\n"
    + json.dumps({"pos": "noun", "etymology_text":
                  "From French monolithique. By surface analysis, monolith + -ic."})
    + "\n"
    + json.dumps({"pos": "verb"})  # no etymology on this entry
).encode()

FREEDICT_BODY = json.dumps(
    [{"word": "zorp", "origin": "From Old Zorpish zorpaz.", "meanings": []}]
).encode()


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


class FakeHttp:
    """Programmable http_get: url -> (status, body). Records calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def test_prose_strips_etymology_tree():
    from app.retrieve import prose

    # Real kaikki shape (abridged): tree block then a long prose line.
    tree_text = (
        "Etymology tree\n"
        "Ancient Greek χρόνος (khrónos)bor.\n"
        "English chrono-\n"
        "English chronology\n"
        "From chrono- (“relating to time”) + -logy (“study of”),"
        " after New Latin chronologia from Ancient Greek."
    )
    assert prose(tree_text).startswith("From chrono-")
    assert "Etymology tree" not in prose(tree_text)
    # Texts without a tree block pass through untouched.
    plain = "From French monolithique. By surface analysis, monolith + -ic."
    assert prose(plain) == plain


def test_url_layout():
    assert kaikki_url("monolithic") == (
        "https://kaikki.org/dictionary/English/meaning/m/mo/monolithic.jsonl"
    )
    assert kaikki_url("a").endswith("/a/a/a.jsonl")


def test_kaikki_hit_returns_and_caches(db):
    http = FakeHttp({kaikki_url("monolithic"): (200, KAIKKI_BODY)})
    records = retrieve(db, "monolithic", http_get=http)
    # Two entries share identical (pos-distinct) prose; dedupe is on
    # (pos, text) so adj and noun both survive, the empty verb doesn't.
    assert [r.pos for r in records] == ["adj", "noun"]
    assert all("monolith + -ic" in r.text for r in records)
    assert all(r.source == "kaikki_api" for r in records)
    assert all(r.url == kaikki_url("monolithic") for r in records)

    # Second call is fully local.
    again = retrieve(db, "monolithic", http_get=http)
    assert len(http.calls) == 1
    assert [r.text for r in again] == [r.text for r in records]


def test_kaikki_word_without_etymology_negative_caches(db):
    body = json.dumps({"pos": "noun"}).encode()
    http = FakeHttp({kaikki_url("blorp"): (200, body)})
    assert retrieve(db, "blorp", http_get=http) == []
    assert retrieve(db, "blorp", http_get=http) == []
    assert len(http.calls) == 1  # negative-cached, no refetch
    row = db.execute("SELECT source, etymology_text FROM etymology"
                     " WHERE word='blorp'").fetchone()
    assert row["source"] == "kaikki_api"
    assert row["etymology_text"] is None


def test_free_dictionary_fallback(db):
    http = FakeHttp({
        kaikki_url("zorp"): (404, b""),
        free_dictionary_url("zorp"): (200, FREEDICT_BODY),
    })
    records = retrieve(db, "zorp", http_get=http)
    assert len(records) == 1
    assert records[0].source == "free_dictionary"
    assert records[0].text == "From Old Zorpish zorpaz."
    assert records[0].url == free_dictionary_url("zorp")
    retrieve(db, "zorp", http_get=http)
    assert len(http.calls) == 2  # cached after the fallback answered


def test_unknown_everywhere_negative_caches(db):
    http = FakeHttp({
        kaikki_url("qzxvqx"): (404, b""),
        free_dictionary_url("qzxvqx"): (404, b""),
    })
    assert retrieve(db, "qzxvqx", http_get=http) == []
    assert retrieve(db, "qzxvqx", http_get=http) == []
    assert len(http.calls) == 2
    row = db.execute("SELECT source FROM etymology WHERE word='qzxvqx'").fetchone()
    assert row["source"] == "none"


def test_transient_kaikki_error_never_negative_caches(db):
    # kaikki 500 + empty freedict answer must NOT freeze a negative result.
    http = FakeHttp({
        kaikki_url("chronology"): (500, b""),
        free_dictionary_url("chronology"): (200, b"[]"),
    })
    assert retrieve(db, "chronology", http_get=http) == []
    assert db.execute("SELECT COUNT(*) c FROM etymology").fetchone()["c"] == 0
    # Next call retries from scratch.
    retrieve(db, "chronology", http_get=http)
    assert len(http.calls) == 4


def test_network_exception_not_cached(db):
    http = FakeHttp({kaikki_url("monolithic"): OSError("offline")})
    assert retrieve(db, "monolithic", http_get=http) == []
    assert db.execute("SELECT COUNT(*) c FROM etymology").fetchone()["c"] == 0


def test_allow_network_false_is_local_only(db):
    http = FakeHttp({})
    assert retrieve(db, "monolithic", http_get=http, allow_network=False) == []
    assert http.calls == []


def test_ingest_dump(db, tmp_path):
    dump = tmp_path / "sample.jsonl"
    lines = [
        {"word": "monolithic", "lang_code": "en", "pos": "adj",
         "etymology_text": "From French monolithique."},
        {"word": "monolithic", "lang_code": "en", "pos": "noun",
         "etymology_text": "From the adjective."},
        {"word": "monolitico", "lang_code": "es", "pos": "adj",
         "etymology_text": "Del frances."},          # wrong language
        {"word": "blorp", "lang_code": "en", "pos": "noun"},  # no etymology
    ]
    dump.write_text("\n".join(json.dumps(x) for x in lines))

    stats = ingest(db, dump)
    assert stats == {"lines": 4, "words": 1, "rows": 2}

    # Ingested words resolve locally with no network.
    records = retrieve(db, "monolithic", http_get=FakeHttp({}))
    assert len(records) == 2
    assert all(r.source == "kaikki_dump" and r.url is None for r in records)

    # Re-ingest is idempotent.
    ingest(db, dump)
    count = db.execute("SELECT COUNT(*) c FROM etymology"
                       " WHERE word='monolithic'").fetchone()["c"]
    assert count == 2
