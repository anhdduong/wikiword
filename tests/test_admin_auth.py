"""HTTP Basic gating of /admin, and its interaction with CORS.

These two features are configured independently — one env var each — but they
are not independent in effect, and the failure they produce together is
invisible from the server side: every response looks correct to curl while the
browser refuses to make the request at all. Hence a module of its own.

The app is built per-test because both features are read from the environment
inside create_app().
"""

import pytest
from fastapi.testclient import TestClient

from app.db import connect, migrate
from app.main import create_app
from app.seed import seed

ORIGIN = "https://wikiword.vercel.app"
USER, PASSWORD = "curator", "s3cret"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("admin_auth") / "test.db"
    conn = connect(path)
    migrate(conn)
    seed(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("WIKIWORD_ADMIN_USER", USER)
    monkeypatch.setenv("WIKIWORD_ADMIN_PASS", PASSWORD)
    monkeypatch.setenv("WIKIWORD_CORS_ORIGINS", ORIGIN)
    with TestClient(create_app(db_path)) as c:
        yield c


def test_admin_requires_credentials(client):
    resp = client.get("/admin/queue")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


def test_admin_accepts_correct_credentials(client):
    resp = client.get("/admin/queue", auth=(USER, PASSWORD))
    assert resp.status_code == 200
    assert "entries" in resp.json()


def test_wrong_password_is_rejected(client):
    assert client.get("/admin/queue", auth=(USER, "wrong")).status_code == 401


def test_lookup_is_not_gated(client):
    assert client.get("/lookup", params={"word": "monolithic"}).status_code == 200


def test_preflight_is_not_gated(client):
    """A browser sends the CORS preflight *without* credentials — it has no way
    to attach them. If the auth middleware answers OPTIONS with a 401 the
    preflight fails, and every mutation (approve/promote/dismiss) from the
    split-origin front end dies before it is ever sent. Letting OPTIONS
    through discloses nothing: the real POST that follows is still gated (see
    test_admin_requires_credentials)."""
    resp = client.options(
        "/admin/approve",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_rejection_carries_cors_headers(client):
    """A 401 without Access-Control-Allow-Origin reaches the page as an opaque
    network error, not a status code — so the UI cannot tell "not signed in"
    from "server unreachable" and shows a JSON parse error instead. Both this
    and the preflight above hold only while CORSMiddleware is registered
    *after* the auth middleware, i.e. outside it."""
    resp = client.get("/admin/queue", headers={"Origin": ORIGIN})
    assert resp.status_code == 401
    assert resp.headers["access-control-allow-origin"] == ORIGIN
