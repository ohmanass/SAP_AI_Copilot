"""Conversation titles managed by the model (the model is faked: no AI call)."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.agent import titles
from app.storage import chat_store, database

SESSION = "titles-session-01"
EXCHANGE = {"question": "Show blocked invoices", "answer": "46 invoices of Infinity Inc are blocked.", "attachments": []}


@pytest.mark.parametrize("raw, expected", [
    ("CDS view sur EKKO et EKPO", "CDS view sur EKKO et EKPO"),
    ('Title: "Overdue purchase orders."', "Overdue purchase orders"),
    ("**Commande 4500000010 en retard**\nSecond line", "Commande 4500000010 en retard"),
    ("« Rapprochement à trois »", "Rapprochement à trois"),
    ("one two three four five six seven eight nine ten", "One two three four five six seven eight…"),
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
    assert titles.generate([EXCHANGE]) is None
    assert titles.generate([]) is None


def test_the_model_reads_the_answers_the_documents_and_the_current_title(monkeypatch):
    seen = {}
    monkeypatch.setattr(titles, "_ask_model", lambda text: seen.setdefault("text", text) and "Invoice check")
    exchanges = [dict(EXCHANGE, attachments=["INF-457.pdf"])] + [
        {"question": f"Question {i}", "answer": f"Answer {i}", "attachments": []} for i in range(8)]
    assert titles.generate(exchanges, "Blocked invoices") == "Invoice check"
    text = seen["text"]
    assert text.startswith("Current title: Blocked invoices")
    assert "Answer 7" in text and "Question 0" not in text      # the latest exchanges only
    assert "Question 2" in text and len([line for line in text.splitlines() if line.startswith("User:")]) == 6


@pytest.mark.parametrize("exchanges, source, expected", [
    (1, "provisional", True), (2, "model", True), (3, "model", False), (4, "model", True),
    (16, "model", True), (17, "model", False), (32, "model", False), (1, "user", False), (2, "user", False),
])
def test_when_the_title_is_written_or_reviewed(exchanges, source, expected):
    assert titles.due(exchanges, source) is expected


# ---------------------------------------------------------------------------
# Storage and API
# ---------------------------------------------------------------------------

class Agent:
    module = None
    owner = "local"
    tools = []

    def ask_stream(self, message):
        yield {"type": "token", "content": "46 invoices of Infinity Inc are blocked."}
        yield {"type": "done", "response": "46 invoices of Infinity Inc are blocked.", "sources": [],
               "tools_used": [], "tables": []}


@pytest.fixture
def store(tmp_path):
    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    yield
    database.set_database(None)
    chat_store.reset()


@pytest.fixture
def client(store, monkeypatch):
    from app import api

    monkeypatch.setattr(api, "get_session", lambda session_id, owner, module=None: (Agent(), threading.Lock()))
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Blocked invoices of Infinity Inc")
    return TestClient(api.app)


def stream(client, message="Show blocked invoices please"):
    lines = client.post("/api/chat/stream", json={"message": message, "session_id": SESSION}).text.splitlines()
    return [json.loads(line) for line in lines]


def state():
    return chat_store.title_state(SESSION, "local")


def test_first_exchange_gets_the_model_title_from_question_and_answer(client):
    events = stream(client)
    assert [e["type"] for e in events][-2:] == ["done", "title"]
    assert events[-1] == {"type": "title", "session_id": SESSION, "title": "Blocked invoices of Infinity Inc"}
    assert state() == {"title": "Blocked invoices of Infinity Inc", "source": "model", "exchanges": 1}


def test_the_title_is_reviewed_as_the_conversation_goes_on(client, monkeypatch):
    seen = []
    stream(client)
    monkeypatch.setattr(titles, "_ask_model", lambda text: seen.append(text) or "Blocked invoices of Infinity Inc")
    assert "title" not in [e["type"] for e in stream(client, "And the oldest one?")]   # unchanged: no event
    assert seen[0].startswith("Current title: Blocked invoices of Infinity Inc")

    stream(client, "Third question")                                   # exchange 3: not reviewed
    assert len(seen) == 1
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Payment blocks and three-way match")
    events = stream(client, "Now the three-way match")                 # exchange 4: reviewed, changed
    assert events[-1]["title"] == "Payment blocks and three-way match"
    assert state()["exchanges"] == 4


def test_a_title_chosen_by_the_user_is_never_changed(client, monkeypatch):
    stream(client)
    chat_store.rename_conversation(SESSION, "local", "My own title")
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Something else")
    assert "title" not in [e["type"] for e in stream(client, "Second question")]
    assert state() == {"title": "My own title", "source": "user", "exchanges": 2}


def test_renamed_while_the_model_writes_the_title(client, monkeypatch):
    def rename_meanwhile(text):
        chat_store.rename_conversation(SESSION, "local", "My own title")
        return "Blocked invoices of Infinity Inc"

    monkeypatch.setattr(titles, "_ask_model", rename_meanwhile)
    assert "title" not in [e["type"] for e in stream(client)]
    assert state()["title"] == "My own title"


def test_automatic_title_hands_the_title_back_to_the_model(client, monkeypatch):
    stream(client)
    chat_store.rename_conversation(SESSION, "local", "My own title")
    monkeypatch.setattr(titles, "_ask_model", lambda text: "Infinity Inc blocked invoices")
    response = client.post(f"/api/sessions/{SESSION}/title")
    assert response.json() == {"session_id": SESSION, "title": "Infinity Inc blocked invoices"}
    assert state()["source"] == "model"
    assert client.post("/api/sessions/other-session-01/title").status_code == 404


def test_model_failure_keeps_the_provisional_title(client, monkeypatch):
    def broken(text):
        raise RuntimeError("AI Core unavailable")

    monkeypatch.setattr(titles, "_ask_model", broken)
    assert stream(client)[-1]["type"] == "done"
    assert state() == {"title": "Show blocked invoices please", "source": "provisional", "exchanges": 1}
    assert client.post(f"/api/sessions/{SESSION}/title").status_code == 503


def test_conversations_stored_before_are_titled_once(store, monkeypatch):
    for session in ("old-session-0001", "old-session-0002"):
        chat_store.save_exchange(session_id=session, owner="alice", user_message="créer moi une cds view stp",
                                 assistant_message="Voici la CDS view sur EKKO.", sources=[], tools_used=[])
    with chat_store.database().cursor() as cursor:
        cursor.execute("UPDATE conversations SET title_source = NULL")
    chat_store.rename_conversation("old-session-0002", "alice", "Mon titre")     # chosen by the user: kept

    monkeypatch.setattr(titles, "_ask_model", lambda text: "CDS view sur EKKO")
    assert titles.backfill() == 1
    assert chat_store.title_state("old-session-0001", "alice")["title"] == "CDS view sur EKKO"
    assert chat_store.title_state("old-session-0002", "alice")["title"] == "Mon titre"
    assert titles.backfill() == 0                                                  # once
