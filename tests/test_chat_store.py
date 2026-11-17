"""
Chat history on both engines.

SQLite always runs. SAP HANA Cloud runs only when HANA_TEST_KEY_FILE points to
the JSON service key of a "hana" schema service (cf service-key ...): the test
data uses its own conversation ids and is deleted afterwards.
"""

import json
import os
import sqlite3
import uuid

import pytest

from app.storage import chat_store, database

ENGINES = ["sqlite"]
if os.getenv("HANA_TEST_KEY_FILE"):
    ENGINES.append("hana")


@pytest.fixture(params=ENGINES)
def store(request, tmp_path):
    if request.param == "sqlite":
        engine = database.SQLiteDatabase(tmp_path / "chat_history.db")
    else:
        with open(os.environ["HANA_TEST_KEY_FILE"], encoding="utf-8") as file:
            key = json.load(file)
        engine = database.HanaDatabase(key.get("credentials", key))
    database.set_database(engine)
    chat_store.reset()
    chat_store.initialize()
    created = []
    yield chat_store, created
    for session_id in created:
        chat_store.delete_conversation(session_id)
    database.set_database(None)
    chat_store.reset()


def new_session(created):
    session_id = f"pytest-{uuid.uuid4()}"
    created.append(session_id)
    return session_id


def test_conversation_lifecycle(store):
    store, created = store
    session_id = new_session(created)
    table = {"kind": "purchase_order", "rows": [{"item": "10", "quantity": 5}]}

    store.save_exchange(session_id, "Analyse la commande 4500000001 ✅", "Réponse « détaillée »",
                        [{"source": "sap_mm.txt", "chunk": 1}], ["get_purchase_order"], module="mm", tables=[table])
    store.save_exchange(session_id, "Second question", "Second answer", [], [])

    conversation = store.get_conversation(session_id)
    assert conversation["title"] == "Analyse la commande 4500000001 ✅"
    assert conversation["module"] == "mm"
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conversation["messages"][1]["content"] == "Réponse « détaillée »"
    assert conversation["messages"][1]["tables"] == [table]
    assert conversation["messages"][1]["tools_used"] == ["get_purchase_order"]

    listed = {c["session_id"]: c for c in store.list_conversations()}
    assert listed[session_id]["message_count"] == 4

    assert store.rename_conversation(session_id, "  Nouveau   titre ")
    assert store.get_conversation(session_id)["title"] == "Nouveau titre"

    assert store.delete_conversation(session_id)
    assert store.get_conversation(session_id) is None
    assert not store.delete_conversation(session_id)


def test_long_texts_and_attachments(store):
    store, created = store
    session_id = new_session(created)
    long_text = ("Ligne de facture – montant 1 000,00 € ✓\n" * 3000)   # > 100 000 characters

    first = store.add_attachment(session_id, "facture.pdf", 2048, 3, long_text, False)
    second = store.add_attachment(session_id, "notes.txt", 10, None, "short", True)
    assert second["id"] > first["id"]
    assert (first["chars"], first["pages"], second["truncated"]) == (len(long_text), 3, True)

    summary, text = store.get_attachment_text(session_id, first["id"])
    assert text == long_text and summary["name"] == "facture.pdf"
    assert [a["name"] for a in store.list_attachments(session_id)] == ["facture.pdf", "notes.txt"]
    assert [a["name"] for a in store.list_attachments(session_id, [second["id"]])] == ["notes.txt"]

    store.save_exchange(session_id, "Lis la facture", "OK", [], ["read_attached_document"], attachments=[first])
    assert store.get_conversation(session_id)["messages"][0]["attachments"][0]["id"] == first["id"]

    assert store.delete_attachment(session_id, second["id"])
    assert not store.delete_attachment(session_id, second["id"])
    store.delete_conversation(session_id)
    assert store.list_attachments(session_id) == []


def test_status(store):
    store, _ = store
    info = store.describe()
    assert info["available"] is True
    assert info["backend"] in ("SQLite", "SAP HANA Cloud")
    assert isinstance(info["conversations"], int)


def test_old_sqlite_files_are_migrated(tmp_path):
    """A history file created by the first versions keeps its data and gains the new columns."""
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE conversations (session_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE messages (message_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                               role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
                               tools_used TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
        CREATE TABLE attachments (attachment_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                                  name TEXT NOT NULL, size_bytes INTEGER NOT NULL, pages INTEGER,
                                  text TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        INSERT INTO conversations VALUES ('old-session', 'Old', '2026-01-01', '2026-01-01');
        INSERT INTO messages (session_id, role, content, created_at) VALUES ('old-session', 'user', 'Hi', '2026-01-01');
        INSERT INTO attachments (session_id, name, size_bytes, text, created_at)
            VALUES ('old-session', 'a.txt', 3, 'abc', '2026-01-01');
    """)
    connection.close()

    database.set_database(database.SQLiteDatabase(path))
    chat_store.reset()
    try:
        chat_store.initialize()
        conversation = chat_store.get_conversation("old-session")
        assert conversation["module"] is None
        assert conversation["messages"][0]["tables"] == [] and conversation["messages"][0]["attachments"] == []
        assert chat_store.list_attachments("old-session")[0]["chars"] == 3
    finally:
        database.set_database(None)
        chat_store.reset()


def test_unreachable_database_does_not_block_the_startup():
    class Down:
        def initialize(self):
            raise database.StoreUnavailable("down")

        def describe(self):
            return {"backend": "SAP HANA Cloud", "location": "X"}

        def cursor(self):
            raise database.StoreUnavailable("down")

    database.set_database(Down())
    chat_store.reset()
    try:
        chat_store.initialize()                      # logs, does not raise
        assert chat_store.describe()["available"] is False
        with pytest.raises(database.StoreUnavailable):
            chat_store.list_conversations()
    finally:
        database.set_database(None)
        chat_store.reset()
