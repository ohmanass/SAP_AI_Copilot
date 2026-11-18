"""AI usage tracking: counted calls, aggregation per day / feature / model, report and prices."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.llm import usage
from app.storage import chat_store, database


@pytest.fixture
def store(tmp_path, monkeypatch):
    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    # conftest disables recording: the real one is tested here
    monkeypatch.setattr(usage, "record", REAL_RECORD)
    monkeypatch.delenv("USAGE_PRICES", raising=False)
    yield
    usage.flush_now()
    database.set_database(None)
    chat_store.reset()


REAL_RECORD = usage.record


def tokens(prompt, completion=0):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


@pytest.fixture
def proxy(monkeypatch):
    """Fake SAP Generative AI Hub: non-streamed, streamed and embedding calls."""
    import gen_ai_hub.proxy.native.openai as client
    seen = {}

    def create(**parameters):
        seen.update(parameters)
        if parameters.get("stream"):
            def chunks():
                yield SimpleNamespace(choices=[SimpleNamespace()], usage=None)
                yield SimpleNamespace(choices=[], usage=tokens(1200, 80))
            return chunks()
        return SimpleNamespace(model="gpt-4o-mini-2024-07-18", usage=tokens(500, 40), choices=[])

    monkeypatch.setattr(client.chat.completions, "create", create)
    monkeypatch.setattr(client.embeddings, "create",
                        lambda **parameters: SimpleNamespace(usage=tokens(300), data=[]))
    return seen


def test_calls_are_counted_and_aggregated(store, proxy):
    usage.chat_completion("title", model_name="gpt-4o-mini", messages=[])
    usage.chat_completion("title", model_name="gpt-4o-mini", messages=[])
    list(usage.chat_completion("chat", model_name="gpt-4o-mini", messages=[], stream=True))
    usage.embeddings("embedding", input=["a"], model_name="text-embedding-3-large")

    assert proxy["stream_options"] == {"include_usage": True}       # usage of a streamed answer
    report = usage.report(7)
    features = {f["feature"]: f for f in report["by_feature"]}
    assert features["title"]["calls"] == 2 and features["title"]["prompt_tokens"] == 1000
    assert features["chat"]["prompt_tokens"] == 1200 and features["chat"]["completion_tokens"] == 80
    assert features["embedding"]["total_tokens"] == 300
    assert report["total"]["calls"] == 4 and report["total"]["total_tokens"] == 1000 + 80 + 1280 + 300
    assert len(report["by_day"]) == 7 and report["by_day"][-1]["calls"] == 4
    assert report["total"]["cost"] is None and report["currency"] is None    # no price configured

    with chat_store.database().cursor() as cursor:
        assert cursor.execute("SELECT COUNT(*) AS n FROM ai_usage").fetchone()["n"] == 3   # one row per feature/model


def test_a_stopped_stream_is_still_counted(store, proxy):
    stream = usage.chat_completion("chat", model_name="gpt-4o-mini", messages=[], stream=True)
    next(stream)
    stream.close()          # the user stopped the answer
    assert usage.report(1)["total"]["calls"] == 1


def test_costs_are_estimated_only_with_configured_prices(store, proxy, monkeypatch):
    monkeypatch.setenv("USAGE_PRICES", '{"currency": "EUR", "gpt-4o-mini": [0.15, 0.6], "text-embedding-3-large": [0.13]}')
    usage.chat_completion("title", model_name="gpt-4o-mini", messages=[])
    usage.embeddings("embedding", input=["a"], model_name="text-embedding-3-large")
    report = usage.report(1)
    assert report["currency"] == "EUR"
    assert report["total"]["cost"] == pytest.approx((500 * 0.15 + 40 * 0.6 + 300 * 0.13) / 1_000_000)


def test_invalid_prices_give_no_estimate(monkeypatch):
    monkeypatch.setenv("USAGE_PRICES", "not json")
    assert usage.prices() is None


def test_a_database_error_never_breaks_a_call(proxy, monkeypatch):
    monkeypatch.setattr(usage, "record", REAL_RECORD)

    class Broken:
        def cursor(self):
            raise RuntimeError("database down")

    monkeypatch.setattr(chat_store, "database", lambda: Broken())
    response = usage.chat_completion("title", model_name="gpt-4o-mini", messages=[])
    assert response.usage.prompt_tokens == 500
    usage.flush_now()       # logged, not raised


def test_usage_api(store, proxy):
    from app import api

    usage.chat_completion("suggestions", model_name="gpt-4o-mini", messages=[])
    body = TestClient(api.app).get("/api/usage", params={"days": 14}).json()
    assert body["days"] == 14 and body["by_feature"][0]["feature"] == "suggestions"
