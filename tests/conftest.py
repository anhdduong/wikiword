import pytest

from app import llm


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    """Tests must never reach the Anthropic API, even when the developer's
    environment has credentials. Individual tests re-enable with fakes."""
    monkeypatch.setattr(llm, "is_enabled", lambda: False)

    def blocked(*args, **kwargs):
        raise RuntimeError("real LLM call blocked in tests")

    monkeypatch.setattr(llm, "call_structured", blocked)


@pytest.fixture(autouse=True)
def no_network_retrieval(request, monkeypatch):
    """API-level tests must not hit kaikki/Free Dictionary. app.main goes
    through this seam; retrieval unit tests inject their own http_get and
    are exempt."""
    if request.fspath.basename == "test_retrieve.py":
        return
    from app import main as main_module

    monkeypatch.setattr(
        main_module.retrieve_module, "retrieve",
        lambda conn, word, **kw: [],
    )


@pytest.fixture(autouse=True)
def no_network_definitions(request, monkeypatch):
    """The deterministic prose fallback fetches a dictionary definition;
    stub it as a definitive miss everywhere except its own unit tests."""
    if request.fspath.basename == "test_compose.py":
        return
    from app import compose

    monkeypatch.setattr(
        compose, "fetch_definition", lambda word, **kw: (None, True)
    )
