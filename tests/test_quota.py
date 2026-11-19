"""Daily AI quota per user: calls counted for their user, requests refused once the quota is used."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.llm import usage
from app.storage import chat_store, database

REAL_RECORD = usage.record


@pytest.fixture
def store(tmp_path, monkeypatch):
    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    monkeypatch.setattr(usage, "record", REAL_RECORD)       # conftest disables recording
    monkeypatch.setenv("AI_DAILY_TOKEN_QUOTA", "1000")
    yield
    usage.flush_now()
    database.set_database(None)
    chat_store.reset()


def test_calls_count_for_their_user(store):
    usage.record("chat", "gpt-4o-mini", 300, 50, owner="alice")
    with usage.attributed_to("bob"):
        usage.record("title", "gpt-4o-mini", 100, 5)
        # A background task keeps the user of the request that started it
        with ThreadPoolExecutor(max_workers=1) as pool:
            usage.submit(pool, usage.record, "suggestions", "gpt-4o-mini", 200, 20).result()
    usage.record("status", "gpt-4o-mini", 1, 1)              # no user: global counters only

    alice, bob = usage.quota_status("alice"), usage.quota_status("bob")
    assert (alice["used"], alice["calls"], alice["remaining"]) == (350, 1, 650)
    assert (bob["used"], bob["calls"]) == (325, 2)
    assert usage.report(1)["total"]["calls"] == 4


def test_quota_reached_and_unlimited(store, monkeypatch):
    usage.record("chat", "gpt-4o-mini", 900, 100, owner="alice")
    assert usage.quota_status("alice")["exceeded"] is True
    monkeypatch.setenv("AI_DAILY_TOKEN_QUOTA", "0")
    status = usage.quota_status("alice")
    assert status["exceeded"] is False and status["limit"] is None and status["remaining"] is None
    monkeypatch.setenv("AI_DAILY_TOKEN_QUOTA", "not a number")
    assert usage.daily_quota() == usage.DEFAULT_DAILY_QUOTA


class Agent:
    module = None
    owner = "local"
    tools = []

    def ask_stream(self, message):
        yield {"type": "done", "response": "Hello", "sources": [], "tools_used": [], "tables": []}


def test_a_user_over_quota_is_refused_before_any_model_call(store, monkeypatch):
    from app import api

    calls = []
    monkeypatch.setattr(api, "get_session",
                        lambda session_id, owner, module=None: calls.append(owner) or (Agent(), threading.Lock()))
    client = TestClient(api.app)
    body = {"message": "Hi", "session_id": "quota-session-01"}

    assert client.post("/api/chat/stream", json=body).status_code == 200
    assert client.get("/api/usage/me").json()["used"] == 0

    usage.record("chat", "gpt-4o-mini", 1000, 0, owner="local")
    for path in ("/api/chat/stream", "/api/chat"):
        response = client.post(path, json=body)
        assert response.status_code == 429 and "1000 tokens" in response.json()["detail"]
    upload = client.post("/api/sessions/quota-session-01/attachments", files={"file": ("a.txt", b"x", "text/plain")})
    assert upload.status_code == 429
    assert calls == ["local"]                                  # refused before the agent
    assert client.get("/api/usage/me").json()["exceeded"] is True
