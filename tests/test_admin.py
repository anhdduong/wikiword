"""Admin review flow. Tests in this module share one app and consume queue
entries in order (approve eats 'mono', promote eats 's', ...)."""

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.main import create_app
from app.seed import seed


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("admin") / "test.db"
    conn = connect(db_path)
    migrate(conn)
    seed(conn)
    conn.close()
    with TestClient(create_app(db_path)) as client:
        # Populate the review queue through real lookups.
        for word in ("monolithic", "strengths", "qzxvqx"):
            client.get("/lookup", params={"word": word})
        yield client, db_path


def entry_by_surface(client, surface):
    entries = client.get("/admin/queue").json()["entries"]
    return next((e for e in entries if e["surface"] == surface), None)


def test_queue_lists_entries_with_affix_info(api):
    client, _ = api
    mono = entry_by_surface(client, "mono")
    assert mono is not None
    assert mono["seen_in"] == "monolithic"
    assert mono["affix"]["canonical"] == "mono-"
    assert mono["affix"]["reviewed"] == 0
    unknown = entry_by_surface(client, "s")
    assert unknown is not None
    assert unknown["affix"] is None
    assert unknown["proposed"] == {"kind": "unknown"}


def test_approve_marks_reviewed_and_clears_cache(api):
    client, db_path = api
    mono = entry_by_surface(client, "mono")
    resp = client.post(f"/admin/queue/{mono['id']}/approve")
    assert resp.status_code == 200
    conn = connect(db_path)
    row = conn.execute(
        "SELECT reviewed FROM affix WHERE canonical = 'mono-'"
    ).fetchone()
    cached = conn.execute("SELECT COUNT(*) c FROM word_cache").fetchone()["c"]
    conn.close()
    assert row["reviewed"] == 1
    assert cached == 0  # curation invalidates the cache
    assert entry_by_surface(client, "mono") is None


def test_approve_unknown_entry_is_rejected(api):
    client, _ = api
    s = entry_by_surface(client, "s")
    assert client.post(f"/admin/queue/{s['id']}/approve").status_code == 400


def test_dismiss_removes_entry_only(api):
    client, db_path = api
    lith = entry_by_surface(client, "lith")
    assert client.post(f"/admin/queue/{lith['id']}/dismiss").status_code == 200
    assert entry_by_surface(client, "lith") is None
    conn = connect(db_path)
    row = conn.execute(
        "SELECT reviewed FROM affix WHERE canonical = 'lith'"
    ).fetchone()
    conn.close()
    assert row["reviewed"] == 0  # dismiss never touches the lexicon


def test_promote_creates_curated_affix_and_next_lookup_uses_it(api):
    client, db_path = api
    s = entry_by_surface(client, "s")
    resp = client.post(f"/admin/queue/{s['id']}/promote", json={
        "canonical": "-s",
        "type": "suffix",
        "origin_lang": "Old English",
        "source_form": "-as",
        "gloss": "plural marker",
        "forms": ["s", "es"],
    })
    assert resp.status_code == 200

    conn = connect(db_path)
    row = conn.execute("SELECT * FROM affix WHERE canonical = '-s'").fetchone()
    forms = {r["form"] for r in conn.execute(
        "SELECT form FROM affix_form WHERE affix_id = ?", (row["id"],)
    )}
    conn.close()
    assert row["reviewed"] == 1
    assert row["gloss"] == "plural marker"
    assert forms == {"s", "es"}
    assert entry_by_surface(client, "s") is None

    # The reloaded lexicon changes the analysis: strengths now ends in a
    # verified plural suffix instead of an unknown span.
    body = client.get("/lookup", params={"word": "strengths"}).json()
    s_morpheme = next(m for m in body["morphemes"] if m["surface"] == "s")
    assert s_morpheme["type"] == "suffix"
    assert s_morpheme["verified"] is True
    assert s_morpheme["meaning"] == "plural marker"
    assert body["status"] == "partial"  # 'strength' itself still uncorroborated


def test_promote_duplicate_conflicts(api):
    client, _ = api
    q = entry_by_surface(client, "qzxvqx")
    resp = client.post(f"/admin/queue/{q['id']}/promote", json={
        "canonical": "-s",
        "type": "suffix",
        "origin_lang": "Old English",
        "gloss": "plural marker",
        "forms": ["s"],
    })
    assert resp.status_code == 409
    assert entry_by_surface(client, "qzxvqx") is not None  # entry survives


def test_promote_invalid_type_rejected(api):
    client, _ = api
    q = entry_by_surface(client, "qzxvqx")
    resp = client.post(f"/admin/queue/{q['id']}/promote", json={
        "canonical": "x-",
        "type": "infix",
        "origin_lang": "Latin",
        "gloss": "nope",
        "forms": ["x"],
    })
    assert resp.status_code == 422


def test_missing_entry_404(api):
    client, _ = api
    assert client.post("/admin/queue/999999/approve").status_code == 404
    assert client.post("/admin/queue/999999/dismiss").status_code == 404
