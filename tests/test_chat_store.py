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

OWNER = "sap.default/alice@example.com"
OTHER = "sap.default/bob@example.com"

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
        chat_store.delete_conversation(session_id, OWNER)
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

    store.save_exchange(session_id, OWNER, "Analyse la commande 4500000001 ✅", "Réponse « détaillée »",
                        [{"source": "sap_mm.txt", "chunk": 1}], ["get_purchase_order"], module="mm", tables=[table])
    store.save_exchange(session_id, OWNER, "Second question", "Second answer", [], [])

    conversation = store.get_conversation(session_id, OWNER)
    assert conversation["title"] == "Analyse la commande 4500000001 ✅"
    assert conversation["module"] == "mm"
    assert [m["role"] for m in conversation["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conversation["messages"][1]["content"] == "Réponse « détaillée »"
    assert conversation["messages"][1]["tables"] == [table]
    assert conversation["messages"][1]["tools_used"] == ["get_purchase_order"]

    listed = {c["session_id"]: c for c in store.list_conversations(OWNER)}
    assert listed[session_id]["message_count"] == 4

    assert store.rename_conversation(session_id, OWNER, "  Nouveau   titre ")
    assert store.get_conversation(session_id, OWNER)["title"] == "Nouveau titre"

    assert store.delete_conversation(session_id, OWNER)
    assert store.get_conversation(session_id, OWNER) is None
    assert not store.delete_conversation(session_id, OWNER)


def test_long_texts_and_attachments(store):
    store, created = store
    session_id = new_session(created)
    long_text = ("Ligne de facture – montant 1 000,00 € ✓\n" * 3000)   # > 100 000 characters

    first = store.add_attachment(session_id, OWNER, "facture.pdf", 2048, 3, long_text, False)
    second = store.add_attachment(session_id, OWNER, "notes.txt", 10, None, "short", True)
    assert second["id"] > first["id"]
    assert (first["chars"], first["pages"], second["truncated"]) == (len(long_text), 3, True)

    summary, text = store.get_attachment_text(session_id, OWNER, first["id"])
    assert text == long_text and summary["name"] == "facture.pdf"
    assert [a["name"] for a in store.list_attachments(session_id, OWNER)] == ["facture.pdf", "notes.txt"]
    assert [a["name"] for a in store.list_attachments(session_id, OWNER, [second["id"]])] == ["notes.txt"]

    store.save_exchange(session_id, OWNER, "Lis la facture", "OK", [], ["read_attached_document"], attachments=[first])
    assert store.get_conversation(session_id, OWNER)["messages"][0]["attachments"][0]["id"] == first["id"]

    assert store.delete_attachment(session_id, OWNER, second["id"])
    assert not store.delete_attachment(session_id, OWNER, second["id"])
    store.delete_conversation(session_id, OWNER)
    assert store.list_attachments(session_id, OWNER) == []


def test_users_only_see_their_own_history(store):
    store, created = store
    session_id = new_session(created)
    store.save_exchange(session_id, OWNER, "Alice's question", "Answer", [], [])
    document = store.add_attachment(session_id, OWNER, "alice.txt", 5, None, "alice", False)

    assert store.session_available(session_id, OWNER)
    assert not store.session_available(session_id, OTHER)
    assert store.session_available(f"pytest-new-{uuid.uuid4()}", OTHER)

    assert store.get_conversation(session_id, OTHER) is None
    assert session_id not in {c["session_id"] for c in store.list_conversations(OTHER)}
    assert not store.rename_conversation(session_id, OTHER, "Hijacked")
    assert not store.delete_conversation(session_id, OTHER)
    assert store.list_attachments(session_id, OTHER) == []
    assert store.get_attachment_text(session_id, OTHER, document["id"]) is None
    assert not store.delete_attachment(session_id, OTHER, document["id"])

    # Another user cannot add messages to Alice's conversation either
    with pytest.raises(Exception):
        store.save_exchange(session_id, OTHER, "Intrusion", "Answer", [], [])
    conversation = store.get_conversation(session_id, OWNER)
    assert conversation["title"] == "Alice's question"
    assert [m["content"] for m in conversation["messages"]] == ["Alice's question", "Answer"]
    assert store.list_attachments(session_id, OWNER)[0]["name"] == "alice.txt"


def test_status(store):
    store, _ = store
    info = store.describe(OWNER)
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
        conversation = chat_store.get_conversation("old-session", "local")
        assert conversation["module"] is None
        assert conversation["messages"][0]["tables"] == [] and conversation["messages"][0]["attachments"] == []
        assert chat_store.list_attachments("old-session", "local")[0]["chars"] == 3
        # Conversations of the first versions belong to the local user
        assert [c["session_id"] for c in chat_store.list_conversations("local")] == ["old-session"]
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
        assert chat_store.describe(OWNER)["available"] is False
        with pytest.raises(database.StoreUnavailable):
            chat_store.list_conversations(OWNER)
    finally:
        database.set_database(None)
        chat_store.reset()
