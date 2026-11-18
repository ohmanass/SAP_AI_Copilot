"""Feedback on the assistant's answers: storage, API, isolation between users, review."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.storage import chat_store, database, feedback_store

SESSION = "feedback-session-01"


@pytest.fixture
def store(tmp_path):
    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    yield
    database.set_database(None)
    chat_store.reset()


def exchange(session=SESSION, owner="local", question="Analyse PO 4500000010.", answer="The PO is overdue.",
             module="mm"):
    return chat_store.save_exchange(session_id=session, owner=owner, user_message=question,
                                    assistant_message=answer, sources=[], tools_used=["get_purchase_order"],
                                    module=module)


def test_save_exchange_returns_the_answer_id_and_history_shows_feedback(store):
    first = exchange()
    second = exchange(question="And the supplier?", answer="EV Parts.")
    assert second > first

    feedback_store.set_feedback(SESSION, "local", second, -1, "Wrong supplier")
    messages = chat_store.get_conversation(SESSION, "local")["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1]["id"] == first and messages[1]["feedback"] is None
    assert messages[3]["id"] == second and messages[3]["feedback"] == {"rating": -1, "comment": "Wrong supplier"}


def test_feedback_copies_the_question_and_can_be_changed_and_removed(store):
    answer = exchange()
    feedback_store.set_feedback(SESSION, "local", answer, 1)
    feedback_store.set_feedback(SESSION, "local", answer, -1, "  Missing the open quantity  ")

    review = feedback_store.review("local", reviewer=False)
    assert review["stats"] == {"total": 1, "positive": 0, "negative": 1, "satisfaction": 0}
    item = review["items"][0]
    assert item["question"] == "Analyse PO 4500000010." and item["answer"] == "The PO is overdue."
    assert item["comment"] == "Missing the open quantity" and item["module"] == "mm"
    assert item["tools_used"] == ["get_purchase_order"] and item["own"] is True

    assert feedback_store.delete_feedback(SESSION, "local", answer) is True
    assert feedback_store.review("local", reviewer=False)["stats"]["total"] == 0


def test_only_answers_of_the_users_own_conversations_can_be_rated(store):
    answer = exchange(owner="alice")
    user_message = answer - 1
    assert feedback_store.set_feedback(SESSION, "bob", answer, 1) is None          # someone else's answer
    assert feedback_store.set_feedback(SESSION, "alice", user_message, 1) is None  # a question, not an answer
    assert feedback_store.set_feedback("other-session-1", "alice", answer, 1) is None
    assert feedback_store.delete_feedback(SESSION, "bob", answer) is False


def test_review_scope_users_and_reviewers(store):
    feedback_store.set_feedback("session-alice-01", "alice", exchange("session-alice-01", "alice"), 1)
    feedback_store.set_feedback("session-bob-0001", "bob", exchange("session-bob-0001", "bob"), -1, "Too vague")

    alice = feedback_store.review("alice", reviewer=False)
    assert alice["scope"] == "mine" and alice["stats"]["total"] == 1
    assert [i["rating"] for i in alice["items"]] == [1] and alice["items"][0]["user"] is None

    everyone = feedback_store.review("alice", reviewer=True)
    assert everyone["scope"] == "all"
    assert everyone["stats"] == {"total": 2, "positive": 1, "negative": 1, "satisfaction": 50}
    by_user = {i["user"]: i for i in everyone["items"]}
    assert by_user["bob"]["own"] is False and by_user["alice"]["own"] is True

    negative = feedback_store.review("alice", reviewer=True, rating=-1)["items"]
    assert [i["comment"] for i in negative] == ["Too vague"]


def test_credentials_in_a_comment_are_masked(store):
    answer = exchange()
    saved = feedback_store.set_feedback(SESSION, "local", answer, -1, "It asked for my password: Welc0me!2024")
    assert "Welc0me" not in saved["comment"] and "[masked" in saved["comment"]


def test_feedback_is_deleted_with_its_conversation(store):
    feedback_store.set_feedback(SESSION, "local", exchange(), -1)
    chat_store.delete_conversation(SESSION, "local")
    assert feedback_store.review("local", reviewer=True)["stats"]["total"] == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_feedback_api(store):
    from app import api

    client = TestClient(api.app)
    answer = exchange()
    url = f"/api/sessions/{SESSION}/messages/{answer}/feedback"

    assert client.put(url, json={"rating": 2}).status_code == 422
    assert client.put(f"/api/sessions/{SESSION}/messages/999999/feedback", json={"rating": 1}).status_code == 404

    response = client.put(url, json={"rating": -1, "comment": "Wrong table"})
    assert response.status_code == 200
    assert response.json() == {"message_id": answer, "rating": -1, "comment": "Wrong table"}

    history = client.get(f"/api/sessions/{SESSION}").json()["messages"]
    assert history[1]["feedback"] == {"rating": -1, "comment": "Wrong table"}

    review = client.get("/api/feedback", params={"rating": -1}).json()
    assert review["scope"] == "all"                      # local development: single user, reviewer
    assert review["items"][0]["comment"] == "Wrong table"

    assert client.delete(url).status_code == 200
    assert client.delete(url).status_code == 404


def test_reviewer_flag_comes_from_the_validated_token():
    class Request:
        def __init__(self, **state):
            self.state = type("State", (), state)()

    assert auth.is_reviewer(Request()) is True                                      # local: no login
    assert auth.is_reviewer(Request(user="sap.default/alice", reviewer=False)) is False
    assert auth.is_reviewer(Request(user="sap.default/alice", reviewer=True)) is True


def test_streamed_answer_carries_its_message_id(store, monkeypatch):
    from app import api

    class Agent:
        module = None
        owner = "local"
        tools = []

        def ask_stream(self, message):
            yield {"type": "token", "content": "Hello"}
            yield {"type": "done", "response": "Hello", "sources": [], "tools_used": [], "tables": []}

    monkeypatch.setattr(api, "get_session", lambda session_id, owner, module=None: (Agent(), threading.Lock()))
    client = TestClient(api.app)
    lines = client.post("/api/chat/stream", json={"message": "Hi", "session_id": SESSION}).text.splitlines()
    done = next(event for event in map(json.loads, lines) if event["type"] == "done")
    assert done["type"] == "done" and isinstance(done["message_id"], int)
    assert chat_store.get_conversation(SESSION, "local")["messages"][1]["id"] == done["message_id"]
