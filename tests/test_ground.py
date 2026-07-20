import json

import pytest

from app.db import connect, migrate
from app.ground import ground
from app.lexicon import load_lexicon
from app.retrieve import EtymologyRecord
from app.seed import seed
from app.segment import segment


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("db") / "test.db")
    migrate(conn)
    seed(conn)
    # Mirror production state: scripts/verify_citations.py has attached a
    # fetched Wiktionary URL to every affix row.
    with conn:
        conn.execute(
            "UPDATE affix SET citations ="
            " json_array('https://en.wiktionary.org/wiki/' || canonical)"
        )
    return conn


@pytest.fixture(scope="module")
def lex(db):
    return load_lexicon(db)


def record(word, text, url="https://kaikki.org/x.jsonl"):
    return EtymologyRecord(word, "adj", text, "kaikki_api", url)


def queue_surfaces(db):
    return {r["surface"] for r in db.execute("SELECT surface FROM review_queue")}


def test_monolithic_fully_grounded(db, lex):
    cand = segment("monolithic", lex)[0]
    recs = [record("monolithic",
                   "From French monolithique. By surface analysis, monolith + -ic.")]
    g = ground(db, "monolithic", cand, recs)
    assert g.status == "grounded"
    assert not g.conflicts
    by_surface = {m.surface: m for m in g.morphemes}
    assert by_surface["mono"].verified
    assert by_surface["mono"].origin == "Ancient Greek"
    assert by_surface["mono"].source_form == "monos"
    assert by_surface["mono"].meaning == "one, single, alone"
    assert by_surface["lith"].meaning == "stone"
    # Table rows carry their own fetched Wiktionary citations.
    assert any("wiktionary.org" in c for c in by_surface["mono"].citations)
    # -ic is corroborated by the fetched text, so the record URL attaches too.
    assert "https://kaikki.org/x.jsonl" in by_surface["ic"].citations
    # Uncorroborated table morphemes must NOT claim the record URL.
    assert "https://kaikki.org/x.jsonl" not in by_surface["mono"].citations


def test_blackboard_free_roots_corroborated(db, lex):
    cand = segment("blackboard", lex)[0]
    recs = [record("blackboard", "Compound of black + board.",
                   url="https://kaikki.org/blackboard.jsonl")]
    g = ground(db, "blackboard", cand, recs)
    assert g.status == "grounded"
    for m in g.morphemes:
        assert m.verified
        assert m.type == "word"  # free pieces are words, not table roots
        assert m.citations == ("https://kaikki.org/blackboard.jsonl",)
        assert m.meaning is None  # no authoritative meaning; never invented


def test_free_root_without_corroboration_is_unverified(db, lex):
    cand = segment("blackboard", lex)[0]
    g = ground(db, "blackboard", cand, [])
    assert g.status == "unverified"
    assert all(not m.verified for m in g.morphemes)
    assert g.status_note  # explains the downgrade


def test_strengths_partial_with_unknown_span(db, lex):
    cand = segment("strengths", lex)[0]  # strength (free) + s (unknown)
    recs = [record("strengths", "From strength + -s.")]
    g = ground(db, "strengths", cand, recs)
    assert g.status == "partial"
    by_surface = {m.surface: m for m in g.morphemes}
    assert by_surface["strength"].verified
    assert not by_surface["s"].verified
    assert by_surface["s"].type == "unknown"
    assert "s" in queue_surfaces(db)


def test_gibberish_unverified(db, lex):
    cand = segment("qzxvqx", lex)[0]
    g = ground(db, "qzxvqx", cand, [])
    assert g.status == "unverified"
    assert "qzxvqx" in queue_surfaces(db)


def test_conflict_caps_status_at_partial(db, lex):
    cand = segment("chronology", lex)[0]  # chrono + logy, both table rows
    recs = [record("chronology",
                   "From Latin chronologia. The element chrono is a Latin form.")]
    g = ground(db, "chronology", cand, recs)
    # Everything is table-verified, but the text attributes chrono to Latin
    # while the table says Ancient Greek -> conflict, capped at partial (§9).
    assert all(m.verified for m in g.morphemes)
    assert len(g.conflicts) == 1
    assert g.conflicts[0]["morpheme"] == "chrono"
    assert g.conflicts[0]["table_origin"] == "Ancient Greek"
    assert "latin" in g.conflicts[0]["text_mentions"]
    assert g.status == "partial"
    assert "conflict" in g.status_note.lower()


def test_agreeing_text_is_not_a_conflict(db, lex):
    cand = segment("chronology", lex)[0]
    recs = [record("chronology",
                   "From chrono- (relating to time), from Ancient Greek khronos"
                   " + -logy (study of).")]
    g = ground(db, "chronology", cand, recs)
    assert not g.conflicts
    assert g.status == "grounded"


def test_etymology_tree_lineage_is_not_a_conflict(db, lex):
    # wiktextract's "Etymology tree" block lists lineage stages like
    # "English photo-"; those are not origin claims and must not conflict
    # with the table's "Ancient Greek". Only prose sentences count.
    cand = segment("chronology", lex)[0]
    recs = [record("chronology",
                   "Etymology tree\n"
                   "Ancient Greek χρόνος (khrónos)bor.\n"
                   "English chrono-\n"
                   "English chronology\n"
                   "From chrono- + -logy, after New Latin chronologia from"
                   " Ancient Greek khronos.")]
    g = ground(db, "chronology", cand, recs)
    assert not g.conflicts
    assert g.status == "grounded"


def test_unreviewed_morphemes_enter_review_queue(db, lex):
    before = queue_surfaces(db)
    cand = segment("hypothermia", lex)[0]  # hypo + therm + ia, all reviewed=0
    ground(db, "hypothermia", cand, [])
    after = queue_surfaces(db)
    assert {"hypo", "therm", "ia"} <= after - before or {"hypo", "therm", "ia"} <= after
    row = connect_row(db, "therm")
    proposed = json.loads(row["proposed"])
    assert proposed["gloss"] == "heat"
    assert row["seen_in"] == "hypothermia"


def connect_row(db, surface):
    return db.execute(
        "SELECT * FROM review_queue WHERE surface = ?", (surface,)
    ).fetchone()


def test_review_queue_dedupes_by_surface(db, lex):
    cand = segment("hypothermia", lex)[0]
    ground(db, "hypothermia", cand, [])
    ground(db, "hypothermia", cand, [])
    count = db.execute(
        "SELECT COUNT(*) c FROM review_queue WHERE surface = 'therm'"
    ).fetchone()["c"]
    assert count == 1


def test_multi_sense_morpheme_notes_ambiguity(db, lex):
    # 'in' maps to two table senses (not / into); ungrounded text can't
    # disambiguate, so the morpheme carries a note instead of a silent pick.
    cand = segment("incredible", lex)[0]  # in + cred + ible
    g = ground(db, "incredible", cand, [])
    m = next(m for m in g.morphemes if m.surface == "in")
    assert m.verified
    assert m.meaning in ("not, without", "in, into, toward")
    assert "senses" in (m.notes or "")


ALONE_TEXT = ('From Middle English allone, from the Old English phrase '
              'eall an, equivalent to al- ("all") + one.')


def test_homograph_sense_prefers_text_origin(db, lex):
    # "al" is both Latin ad- and Old English al-; the prose names Old
    # English, so that sense must win — and the conflict disappears.
    cand = segment("alone", lex)[0]
    g = ground(db, "alone", cand, [record("alone", ALONE_TEXT)])
    al = next(m for m in g.morphemes if m.surface == "al")
    assert al.origin == "Old English"
    assert al.meaning == "all"
    assert not g.conflicts
    assert g.status == "grounded"  # "one" is corroborated by the text too


def test_homograph_sense_follows_latin_text(db, lex):
    cand = segment("alone", lex)[0]
    g = ground(db, "alone", cand, [record(
        "alone", "From Latin ad (toward); al- here is an assimilated ad-."
    )])
    al = next(m for m in g.morphemes if m.surface == "al")
    assert al.origin == "Latin"
    assert not g.conflicts
