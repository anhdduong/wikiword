import sqlite3

import pytest

from app.db import connect, migrate
from app.seed import seed


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    return conn


@pytest.fixture
def seeded(db):
    seed(db)
    return db


def lookup_forms(conn, form):
    """All (canonical, type, gloss) rows reachable from a surface form."""
    return [
        (r["canonical"], r["type"], r["gloss"])
        for r in conn.execute(
            "SELECT a.canonical, a.type, a.gloss FROM affix_form f"
            " JOIN affix a ON a.id = f.affix_id WHERE f.form = ?",
            (form,),
        )
    ]


def test_migrate_is_idempotent(db):
    assert migrate(db) == []  # second run applies nothing


def test_tables_exist(db):
    names = {
        r["name"]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"affix", "affix_form", "word_cache", "review_queue"} <= names


def test_type_constraint(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO affix (canonical, type, origin_lang, gloss)"
            " VALUES ('x-', 'infix', 'Latin', 'nope')"
        )


def test_status_constraint(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO word_cache (word, status, payload, model_version)"
            " VALUES ('x', 'bogus', '{}', 'v1')"
        )


def test_seed_is_idempotent(seeded):
    before = seeded.execute("SELECT COUNT(*) c FROM affix").fetchone()["c"]
    forms_before = seeded.execute("SELECT COUNT(*) c FROM affix_form").fetchone()["c"]
    stats = seed(seeded)
    assert stats["inserted"] == 0
    assert seeded.execute("SELECT COUNT(*) c FROM affix").fetchone()["c"] == before
    assert (
        seeded.execute("SELECT COUNT(*) c FROM affix_form").fetchone()["c"]
        == forms_before
    )


def test_reseed_preserves_curation_state(seeded):
    seeded.execute(
        "UPDATE affix SET reviewed = 1, citations = '[\"https://example.org\"]'"
        " WHERE canonical = 'mono-'"
    )
    seeded.commit()
    seed(seeded)
    row = seeded.execute(
        "SELECT reviewed, citations FROM affix WHERE canonical = 'mono-'"
    ).fetchone()
    assert row["reviewed"] == 1
    assert row["citations"] == '["https://example.org"]'


def test_allomorphs_resolve_to_one_concept(seeded):
    # mono-/mon- are the same concept; in-/im-/il-/ir- resolve to in- 'not'
    assert lookup_forms(seeded, "mono") == lookup_forms(seeded, "mon")
    for form in ("im", "il", "ir"):
        assert ("in-", "prefix", "not, without") in lookup_forms(seeded, form)


def test_same_canonical_two_senses(seeded):
    # in- 'not' and in- 'into' are distinct rows sharing surface forms
    senses = {
        gloss for (_, _, gloss) in lookup_forms(seeded, "in")
    }
    assert len(senses) == 2


# Every affix morpheme the §3.6 segmentation test words rely on must be
# resolvable from the seed. (Free-word roots like 'black', 'union',
# 'establish' come from the milestone-2 wordlist, not this table.)
SECTION_3_6_COVERAGE = [
    # monolithic
    ("mono", "prefix"), ("lith", "root"), ("ic", "suffix"),
    # chronology (chron + o + logy, linking vowel)
    ("chron", "root"), ("logy", "suffix"),
    # photosynthesis
    ("photo", "root"), ("syn", "prefix"), ("thes", "root"),
    # unionize / un+ion+ize
    ("un", "prefix"), ("ion", "suffix"), ("ize", "suffix"),
    # therapist (correct split: therap + ist)
    ("therap", "root"), ("ist", "suffix"),
    # antidisestablishmentarianism
    ("anti", "prefix"), ("dis", "prefix"), ("ment", "suffix"),
    ("arian", "suffix"), ("ism", "suffix"),
]


@pytest.mark.parametrize("form,type_", SECTION_3_6_COVERAGE)
def test_section_3_6_coverage(seeded, form, type_):
    assert any(t == type_ for (_, t, _) in lookup_forms(seeded, form)), (
        f"seed missing {type_} resolvable from surface form {form!r}"
    )
