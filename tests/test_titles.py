"""Conversation titles written by the model (the model is faked: no AI call)."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.agent import titles
from app.storage import chat_store, database

SESSION = "titles-session-01"


@pytest.mark.parametrize("raw, expected", [
    ("CDS view sur EKKO et EKPO", "CDS view sur EKKO et EKPO"),
    ('Title: "Overdue purchase orders."', "Overdue purchase orders"),
    ("**Commande 4500000010 en retard**\nSecond line", "Commande 4500000010 en retard"),
    ("« Rapprochement à trois »", "Rapprochement à trois"),
    ("one two three four five six seven eight nine ten", "One two three four five six seven eight"),
    ("utilisateur valable jusqu'au 2030.12.18", "Utilisateur valable jusqu'au 2030.12.18"),
    ("   ", None),
    (".", None),
])
def test_model_output_is_cleaned(raw, expected):
    assert titles.clean(raw) == expected


def test_a_secret_never_reaches_the_title():
    assert "Welc0me" not in titles.clean("Reset password: Welc0me!2024")


def test_failure_gives_no_title():
    # conftest: the model raises in the offline tests
    assert titles.generate("Show overdue purchase orders.") is None


def test_attachment_names_are_given_to_the_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(titles, "_ask_model", lambda text: seen.setdefault("text", text) and "Invoice check")
    assert titles.generate("Analyse the attached document.", ["INF-457.pdf"]) == "Invoice check"
    assert "INF-457.pdf" in seen["text"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class Agent:
    module = None
    owner = "local"
    tools = []

    def ask_stream(self, message):
        yield {"type": "token", "content": "Here they are."}
        yield {"type": "done", "response": "Here they are.", "sources": [], "tools_used": [], "tables": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import api

    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    monkeypatch.setattr(api, "get_session", lambda session_id, owner, module=None: (Agent(), threading.Lock()))
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Overdue purchase orders")
    yield TestClient(api.app)
    database.set_database(None)
    chat_store.reset()


def stream(client, message, session=SESSION):
    lines = client.post("/api/chat/stream", json={"message": message, "session_id": session}).text.splitlines()
    return [json.loads(line) for line in lines]


def title_of(session=SESSION):
    return chat_store.get_conversation(session, "local")["title"]


def test_new_conversation_gets_the_model_title_after_the_answer(client):
    events = stream(client, "Show me the purchase orders that are overdue please")
    assert [e["type"] for e in events][-2:] == ["done", "title"]
    assert events[-1] == {"type": "title", "session_id": SESSION, "title": "Overdue purchase orders"}
    assert title_of() == "Overdue purchase orders"


def test_only_the_first_message_gives_a_title(client, monkeypatch):
    stream(client, "Show overdue purchase orders")
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Something else")
    events = stream(client, "And the oldest one?")
    assert "title" not in [e["type"] for e in events]
    assert title_of() == "Overdue purchase orders"


def test_a_title_chosen_by_the_user_is_never_replaced(client, monkeypatch):
    def rename_meanwhile(text):
        # The user renames the conversation while the model writes its title
        chat_store.save_exchange(session_id=SESSION, owner="local", user_message="x", assistant_message="y",
                                 sources=[], tools_used=[])
        chat_store.rename_conversation(SESSION, "local", "My own title")
        return "Overdue purchase orders"

    monkeypatch.setattr(titles, "_ask_model", rename_meanwhile)
    events = stream(client, "Show overdue purchase orders")
    assert "title" not in [e["type"] for e in events]
    assert title_of() == "My own title"


def test_model_failure_keeps_the_provisional_title(client, monkeypatch):
    def broken(text):
        raise RuntimeError("AI Core unavailable")

    monkeypatch.setattr(titles, "_ask_model", broken)
    events = stream(client, "Show overdue purchase orders")
    assert events[-1]["type"] == "done"
    assert title_of() == "Show overdue purchase orders"
