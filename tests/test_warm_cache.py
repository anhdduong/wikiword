from app import llm
from app import rerank as rerank_module
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
    # Every llm_assemble call fails (e.g. quota exhaustion) — every response
    # is degraded (served uncached) — the run must stop early instead of
    # burning through the whole list.
    from app import assemble as assemble_module

    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(
        rerank_module, "rerank",
        lambda word, cands, call=None: rerank_module.RerankResult(0, "top is fine"),
    )
    monkeypatch.setattr(
        assemble_module, "assemble", lambda word, morphemes, texts, call=None: None
    )
    db = make_db(tmp_path)
    words = ["monolithic", "chronology", "unionize", "therapist", "telegram"]
    stats = warm(words, db, sleep_s=0, stop_after=3, log=quiet)
    assert stats["degraded"] == 3  # stopped at the third, not all five
    assert stats["warmed"] == 0


def test_zero_call_successes_do_not_reset_the_stop_counter(tmp_path, monkeypatch):
    # "that" warms without any assemble call at all (no morpheme has a
    # verified meaning, so assemble is never attempted) — it must not be
    # treated as proof the quota is alive.
    from app import assemble as assemble_module

    bad = {"monolithic", "chronology", "therapist"}
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(
        rerank_module, "rerank",
        lambda word, cands, call=None: rerank_module.RerankResult(0, "top is fine"),
    )
    monkeypatch.setattr(
        assemble_module, "assemble",
        lambda word, morphemes, texts, call=None: (
            None if word in bad else "ok literal meaning"
        ),
    )
    db = make_db(tmp_path)
    words = ["monolithic", "that", "chronology", "therapist"]
    stats = warm(words, db, sleep_s=0, stop_after=3, log=quiet)
    assert stats["degraded"] == 3  # stopped at therapist despite "that"
    assert stats["warmed"] == 1  # "that" itself still got cached
