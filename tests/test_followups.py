"""Follow-up questions suggested under an answer (the model is faked: no AI call)."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.agent import followups
from app.agent.agent import SAPAgent
from app.modules import get_module
from app.storage import chat_store, database

SESSION = "followups-session-01"


def test_model_output_is_parsed_and_cleaned():
    raw = '```json\n["Show PO 4500208417", "  - List   overdue orders ", "show po 4500208417", 42, "x"]\n```'
    assert followups.parse(raw) == ["Show PO 4500208417", "List overdue orders"]


def test_at_most_three_short_suggestions_never_the_question_again():
    raw = json.dumps(["Show overdue purchase orders?", "A", "B" * 200, "First one", "Second one", "Third one", "Fourth"])
    assert followups.parse(raw, "Show overdue purchase orders") == ["First one", "Second one", "Third one"]


def test_a_suggestion_with_an_invented_document_number_is_dropped():
    raw = '["Show PO 4500208417", "Show purchase order 12345", "Check invoices of CorpInMotion"]'
    known = "10 overdue orders: 4500208417, 4500208429"
    assert followups.parse(raw, known_text=known) == ["Show PO 4500208417", "Check invoices of CorpInMotion"]


def test_the_sap_data_of_the_answer_is_given_to_the_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(followups, "_ask_model",
                        lambda content: seen.setdefault("content", content) and '["Analyse PO 4500208429"]')
    table = {"kind": "overdue_orders", "title_params": [], "attributes": [],
             "columns": [{"key": "po_number"}, {"key": "supplier"}],
             "rows": [{"po_number": "4500208417", "supplier": "CorpInMotion"},
                      {"po_number": "4500208429", "supplier": "CorpInMotion"}]}
    answer = "10 orders are overdue."
    assert followups.generate("Overdue POs?", answer, [], tables=[table]) == ["Analyse PO 4500208429"]
    assert "po_number=4500208429" in seen["content"]


@pytest.mark.parametrize("raw", ["", "Sure! Here are some ideas", "{}", "[]", "not json ["])
def test_unusable_output_gives_no_suggestion(raw):
    assert followups.parse(raw) == []


def test_a_suggestion_with_a_secret_is_dropped():
    assert followups.parse('["Log in with password: Welc0me!2024", "Show PO 4500000010"]') == ["Show PO 4500000010"]


def test_the_model_only_gets_the_tools_of_the_workspace(monkeypatch):
    seen = {}
    monkeypatch.setattr(followups, "_ask_model", lambda content: seen.setdefault("content", content) and "[]")
    abap = SAPAgent(module=get_module("abap"))
    followups.generate("What is a CDS view?", "A CDS view is...", abap.tools, "ABAP - ABAP Development")
    assert "search_sap_documentation" in seen["content"]
    assert "get_purchase_order" not in seen["content"]


def test_failure_gives_no_suggestion():
    # conftest: the model raises in the offline tests
    assert followups.generate("Show overdue purchase orders", "There are 10.", []) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class Agent:
    module = None
    owner = "local"
    tools = []

    def ask_stream(self, message):
        yield {"type": "token", "content": "10 orders are overdue, the oldest is 4500208417."}
        yield {"type": "done", "response": "10 orders are overdue, the oldest is 4500208417.", "sources": [], "tools_used": [], "tables": []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import api

    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    monkeypatch.setattr(api, "get_session", lambda session_id, owner, module=None: (Agent(), threading.Lock()))
    yield TestClient(api.app)
    database.set_database(None)
    chat_store.reset()


def stream(client):
    lines = client.post("/api/chat/stream", json={"message": "Overdue orders?", "session_id": SESSION}).text
    return [json.loads(line) for line in lines.splitlines()]


def test_suggestions_come_after_the_answer_with_its_message_id(client, monkeypatch):
    monkeypatch.setattr(followups, "_ask_model", lambda content: '["Show PO 4500208417", "Who is the supplier?"]')
    events = stream(client)
    done = next(e for e in events if e["type"] == "done")
    assert events[-1] == {"type": "suggestions", "message_id": done["message_id"],
                          "items": ["Show PO 4500208417", "Who is the supplier?"]}
    assert [e["type"] for e in events].index("done") < len(events) - 1


def test_no_suggestion_event_when_there_is_none(client, monkeypatch):
    monkeypatch.setattr(followups, "_ask_model", lambda content: "[]")
    assert "suggestions" not in [e["type"] for e in stream(client)]
