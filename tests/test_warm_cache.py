from app import compose
from app.db import connect
from app.seed import seed
from scripts.warm_cache import warm


def make_db(tmp_path):
    db = tmp_path / "warm.db"
    from app.db import migrate

    conn = connect(db)
    migrate(conn)
    seed(conn)
    conn.close()
    return db


def quiet(*args):
    pass


def test_warm_caches_then_skips(tmp_path):
    db = make_db(tmp_path)
    words = ["monolithic", "blackboard"]
    first = warm(words, db, sleep_s=0, log=quiet)
    assert first["warmed"] == 2
    assert first["cached"] == 0
    second = warm(words, db, sleep_s=0, log=quiet)
    assert second == {"requested": 2, "cached": 2, "warmed": 0,
                      "degraded": 0, "errors": 0}


def test_warm_stops_on_consecutive_degraded(tmp_path, monkeypatch):
    # A non-definitive dictionary miss makes every response degraded
    # (served uncached) — the run must stop early instead of burning
    # through the whole list.
    monkeypatch.setattr(compose, "fetch_definition",
                        lambda word, http_get=None: (None, False))
    db = make_db(tmp_path)
    words = ["monolithic", "blackboard", "chronology", "unionize",
             "therapist", "telegram"]
    stats = warm(words, db, sleep_s=0, stop_after=3, log=quiet)
    assert stats["degraded"] == 3  # stopped at the third, not all six
    assert stats["warmed"] == 0
