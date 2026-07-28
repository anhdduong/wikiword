import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.main import create_app
from app.seed import seed
from app.version import model_version


@pytest.fixture(scope="session")
def api(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("api") / "test.db"
    conn = connect(db_path)
    migrate(conn)
    seed(conn)
    conn.close()
    with TestClient(create_app(db_path)) as client:
        yield client, db_path


def test_lookup_returns_candidates(api):
    client, _ = api
    resp = client.get("/lookup", params={"word": "monolithic"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["word"] == "monolithic"
    # All three morphemes are affix-table rows -> grounded (table is truth).
    assert body["status"] == "grounded"
    assert body["model_version"] == model_version()
    assert body["status_note"]
    top = body["candidates"][0]
    assert [p["surface"] for p in top["pieces"]] == ["mono", "lith", "ic"]
    assert [p["kind"] for p in top["pieces"]] == ["prefix", "root", "suffix"]
    assert top["pieces"][0]["span"] == [0, 4]
    morphemes = body["morphemes"]
    assert [m["surface"] for m in morphemes] == ["mono", "lith", "ic"]
    assert morphemes[0]["origin"] == "Ancient Greek"
    assert morphemes[0]["meaning"] == "one, single, alone"
    assert all(m["verified"] for m in morphemes)
    assert body["conflicts"] == []


def test_lookup_writes_cache(api):
    client, db_path = api
    client.get("/lookup", params={"word": "chronology"})
    conn = connect(db_path)
    row = conn.execute(
        "SELECT status, model_version FROM word_cache WHERE word = 'chronology'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "grounded"
    assert row["model_version"] == model_version()


def test_lookup_serves_from_cache(api):
    client, db_path = api
    client.get("/lookup", params={"word": "telegraph"})
    # Poison the cached payload; if the next call returns the sentinel, it
    # was served from cache rather than recomputed.
    conn = connect(db_path)
    with conn:
        conn.execute(
            "UPDATE word_cache SET payload = ? WHERE word = 'telegraph'",
            (json.dumps({"sentinel": True}),),
        )
    conn.close()
    assert client.get("/lookup", params={"word": "telegraph"}).json() == {
        "sentinel": True
    }


def test_stale_model_version_recomputes(api):
    client, db_path = api
    client.get("/lookup", params={"word": "democracy"})
    conn = connect(db_path)
    with conn:
        conn.execute(
            "UPDATE word_cache SET payload = '{\"sentinel\": true}',"
            " model_version = 'obsolete' WHERE word = 'democracy'"
        )
    body = client.get("/lookup", params={"word": "democracy"}).json()
    assert "sentinel" not in body
    assert body["model_version"] == model_version()
    row = conn.execute(
        "SELECT model_version FROM word_cache WHERE word = 'democracy'"
    ).fetchone()
    conn.close()
    assert row["model_version"] == model_version()


def test_word_is_normalized(api):
    client, _ = api
    body = client.get("/lookup", params={"word": "  MonoLithic "}).json()
    assert body["word"] == "monolithic"


@pytest.mark.parametrize("bad", ["", "  ", "mono lithic", "mono-lithic", "mono1", "x" * 41])
def test_invalid_words_rejected(api, bad):
    client, _ = api
    assert client.get("/lookup", params={"word": bad}).status_code == 400


def test_missing_param_rejected(api):
    client, _ = api
    assert client.get("/lookup").status_code == 422


def test_front_end_served_when_built(api):
    from pathlib import Path

    if not (Path(__file__).parent.parent / "front" / "dist").is_dir():
        pytest.skip("front end not built")
    client, _ = api
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<div id="app">' in resp.text


def test_gibberish_still_answers(api):
    client, _ = api
    body = client.get("/lookup", params={"word": "qzxvqx"}).json()
    assert body["candidates"][0]["pieces"][0]["kind"] == "unknown"


def test_free_roots_without_corroboration_are_unverified(api):
    # Retrieval is stubbed to [] in tests, so blackboard's free roots have
    # no corroboration and status degrades honestly.
    client, _ = api
    body = client.get("/lookup", params={"word": "blackboard"}).json()
    assert body["status"] == "unverified"
    assert all(not m["verified"] for m in body["morphemes"])
    assert "0 of 2 morphemes verified" in body["status_note"]


def test_corroborated_etymology_attaches_citation(api, monkeypatch):
    from app import main as main_module
    from app.retrieve import EtymologyRecord

    client, _ = api
    url = "https://kaikki.org/dictionary/English/meaning/t/te/telegram.jsonl"
    monkeypatch.setattr(
        main_module.retrieve_module, "retrieve",
        lambda conn, word, **kw: [EtymologyRecord(
            word, "noun", "From tele- (far) + -gram (written character).",
            "kaikki_api", url,
        )],
    )
    body = client.get("/lookup", params={"word": "telegram"}).json()
    assert body["status"] == "grounded"
    assert body["etymology"][0]["url"] == url
    tele = next(m for m in body["morphemes"] if m["surface"] == "tele")
    assert url in tele["citations"]


def test_lookup_populates_review_queue(api):
    client, db_path = api
    client.get("/lookup", params={"word": "hypothermia"})
    conn = connect(db_path)
    surfaces = {r["surface"] for r in conn.execute(
        "SELECT surface FROM review_queue WHERE seen_in = 'hypothermia'"
    )}
    conn.close()
    # hypo|therm|ia — hypo and therm are still reviewed=0 in affixes.csv, so
    # they queue; -ia is curated now and must not. (hydrophobia no longer
    # works here either: hydro and phob are curated too.)
    assert {"hypo", "therm"} <= surfaces
    assert "ia" not in surfaces


def test_llm_disabled_falls_back_to_cost_order(api):
    client, _ = api
    body = client.get("/lookup", params={"word": "blackboard"}).json()
    assert body["chosen_index"] == 0
    assert body["rerank"] is None
    assert [m["surface"] for m in body["morphemes"]] == [
        p["surface"] for p in body["candidates"][0]["pieces"]
    ]
    assert "LLM calls disabled" in body["status_note"]
    assert body["literal_meaning"] is None
    assert body["modern_usage"] is None


def test_rerank_choice_selects_segmentation(api, monkeypatch):
    from app import assemble as assemble_module
    from app import llm
    from app import rerank as rerank_module

    client, db_path = api
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(
        rerank_module, "rerank",
        lambda word, cands, call=None: rerank_module.RerankResult(1, "second is right"),
    )
    monkeypatch.setattr(
        assemble_module, "assemble",
        lambda word, morphemes, texts, call=None: "to make into a union",
    )
    from app import compose as compose_module
    monkeypatch.setattr(
        compose_module, "fetch_definition",
        lambda word, **kw: ("(verb) To organize workers.", True),
    )
    body = client.get("/lookup", params={"word": "unionize"}).json()
    assert body["chosen_index"] == 1
    assert [m["surface"] for m in body["morphemes"]] == [
        p["surface"] for p in body["candidates"][1]["pieces"]
    ]
    assert body["rerank"]["reason"] == "second is right"
    assert body["literal_meaning"] == "to make into a union"
    # modern_usage comes from the dictionary even when the LLM is enabled.
    assert body["modern_usage"] == "(verb) To organize workers."
    conn = connect(db_path)
    row = conn.execute(
        "SELECT payload FROM word_cache WHERE word = 'unionize'"
    ).fetchone()
    conn.close()
    assert row is not None  # successful rerank+assemble is cached
    cached = json.loads(row["payload"])
    assert cached["chosen_index"] == 1
    assert cached["modern_usage"] == "(verb) To organize workers."


def test_rerank_failure_serves_but_does_not_cache(api, monkeypatch):
    from app import llm
    from app import rerank as rerank_module

    client, db_path = api
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(
        rerank_module, "rerank", lambda word, cands, call=None: None
    )
    body = client.get("/lookup", params={"word": "photosynthesis"}).json()
    assert body["chosen_index"] == 0
    assert body["rerank"] is None
    assert "uncached" in body["status_note"]
    conn = connect(db_path)
    row = conn.execute(
        "SELECT word FROM word_cache WHERE word = 'photosynthesis'"
    ).fetchone()
    conn.close()
    assert row is None  # degraded response must not be frozen


def test_assemble_failure_serves_but_does_not_cache(api, monkeypatch):
    from app import assemble as assemble_module
    from app import llm
    from app import rerank as rerank_module

    client, db_path = api
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(
        rerank_module, "rerank",
        lambda word, cands, call=None: rerank_module.RerankResult(0, "top is fine"),
    )
    monkeypatch.setattr(
        assemble_module, "assemble", lambda word, morphemes, texts, call=None: None
    )
    body = client.get("/lookup", params={"word": "neuropathology"}).json()
    assert body["literal_meaning"] is None
    assert "prose assembly failed" in body["status_note"]
    conn = connect(db_path)
    row = conn.execute(
        "SELECT word FROM word_cache WHERE word = 'neuropathology'"
    ).fetchone()
    conn.close()
    assert row is None


def test_no_llm_composes_deterministic_prose(api, monkeypatch):
    from app import compose as compose_module

    client, db_path = api
    monkeypatch.setattr(
        compose_module, "fetch_definition",
        lambda word, **kw: ("(noun) An inscription encoding a date.", True),
    )
    body = client.get("/lookup", params={"word": "chronogram"}).json()
    assert body["literal_meaning"] == (
        'chrono "time" + gram "written thing, letter"'
    )
    assert body["modern_usage"] == "(noun) An inscription encoding a date."
    assert "composed from affix glosses" in body["status_note"]
    assert "LLM calls disabled" in body["status_note"]
    conn = connect(db_path)
    row = conn.execute(
        "SELECT payload FROM word_cache WHERE word = 'chronogram'"
    ).fetchone()
    conn.close()
    assert row is not None  # fully definitive answer: cache it
    assert json.loads(row["payload"])["modern_usage"].startswith("(noun)")


def test_definition_shows_even_without_glosses(api, monkeypatch):
    # flashback: two free-word roots, no table glosses — the literal sense
    # can't compose, but the fetched definition is independently grounded.
    from app import compose as compose_module

    client, _ = api
    monkeypatch.setattr(
        compose_module, "fetch_definition",
        lambda word, **kw: ("(noun) A scene set earlier than the story.", True),
    )
    body = client.get("/lookup", params={"word": "flashback"}).json()
    assert body["literal_meaning"] is None
    assert body["modern_usage"] == "(noun) A scene set earlier than the story."
    assert "literal sense omitted" in body["status_note"]
    assert "Merriam-Webster" in body["status_note"]


def test_dictionary_failure_serves_but_does_not_cache(api, monkeypatch):
    from app import compose as compose_module

    client, db_path = api
    monkeypatch.setattr(
        compose_module, "fetch_definition", lambda word, **kw: (None, False)
    )
    body = client.get("/lookup", params={"word": "thermograph"}).json()
    assert '"heat"' in body["literal_meaning"]  # glosses still compose
    assert body["modern_usage"] is None
    assert "uncached" in body["status_note"]
    conn = connect(db_path)
    row = conn.execute(
        "SELECT word FROM word_cache WHERE word = 'thermograph'"
    ).fetchone()
    conn.close()
    assert row is None  # transient dictionary failure must not be frozen


def test_no_facts_means_null_prose_and_still_cached(api):
    # sunflower: both pieces are free words with no authoritative meaning,
    # so the literal sense is omitted honestly — and that IS the complete
    # answer: cache it. (fetch_definition is stubbed to a definitive miss.)
    # strengths no longer works here: its plural -s is a glossed table row.
    client, db_path = api
    body = client.get("/lookup", params={"word": "sunflower"}).json()
    assert body["literal_meaning"] is None
    assert body["modern_usage"] is None
    assert "literal sense omitted" in body["status_note"]
    conn = connect(db_path)
    row = conn.execute(
        "SELECT word FROM word_cache WHERE word = 'sunflower'"
    ).fetchone()
    conn.close()
    assert row is not None


def insert_negative_etymology(db_path, word):
    conn = connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO etymology (word, etymology_text, source)"
            " VALUES (?, NULL, 'none')", (word,),
        )
    conn.close()


def test_unrecognized_word_flags_misspelling(api):
    # "therapis" segments plausibly, but it's not in the wordlist and every
    # source definitively lacks it (negative-cache row): flag it, suggest
    # the real word, and never invent a modern usage for it.
    client, db_path = api
    insert_negative_etymology(db_path, "therapis")
    resp = client.get("/lookup", params={"word": "therapis"})
    body = resp.json()
    assert "possible misspelling" in body["status_note"]
    assert body["unrecognized"] is True
    assert "therapist" in body["suggestions"]
    assert body["modern_usage"] is None


def test_unfetched_word_is_not_flagged_as_misspelling(api):
    # No negative-cache row (e.g. transport failure): absence of evidence
    # is not evidence of absence, so no misspelling flag.
    client, _ = api
    resp = client.get("/lookup", params={"word": "qzxvqx"})
    body = resp.json()
    assert "misspelling" not in (body["status_note"] or "")
    assert body["suggestions"] == []


def test_real_word_is_not_flagged(api):
    client, _ = api
    resp = client.get("/lookup", params={"word": "blackboard"})
    body = resp.json()
    assert "misspelling" not in (body["status_note"] or "")
    assert body["unrecognized"] is False
    assert body["suggestions"] == []
