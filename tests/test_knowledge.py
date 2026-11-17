"""
Knowledge base: registry, embeddings, vector search, shipped documents and
documents added from the UI.

Embeddings are simulated (hashed words, no SAP AI Core call). The vector store
is ChromaDB (temporary folder); the same tests also run on the SAP HANA Cloud
Vector Engine when HANA_TEST_KEY_FILE points to a "hana" service key.
"""

import hashlib
import json
import math
import os
import re

import pytest
from fastapi.testclient import TestClient

from app.rag import knowledge_store
from app.storage import chat_store, database

ALICE = "sap.default/alice@example.com"
BOB = "sap.default/bob@example.com"

ENGINES = ["chroma"]
if os.getenv("HANA_TEST_KEY_FILE"):
    ENGINES.append("hana")


def fake_embedding(text, dimensions=64):
    """Bag of hashed words, normalised: texts sharing words are close."""
    vector = [0.0] * dimensions
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % dimensions] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture(params=ENGINES)
def kb(request, tmp_path, monkeypatch):
    if request.param == "chroma":
        engine = database.SQLiteDatabase(tmp_path / "history.db")
        database.set_database(engine)
        chat_store.reset()
        chat_store.initialize()
        knowledge_store.set_vectors(knowledge_store.ChromaVectors(tmp_path / "chroma"))
    else:
        with open(os.environ["HANA_TEST_KEY_FILE"], encoding="utf-8") as file:
            key = json.load(file)
        engine = database.HanaDatabase(key.get("credentials", key))
        database.set_database(engine)
        chat_store.reset()
        chat_store.initialize()
        knowledge_store.set_vectors(knowledge_store.HanaVectors(engine))

    docs = tmp_path / "documents"
    docs.mkdir()
    monkeypatch.setattr(knowledge_store, "DOCUMENTS_PATH", docs)
    monkeypatch.setattr(knowledge_store, "embed", lambda texts: [fake_embedding(t) for t in texts])
    monkeypatch.setattr(knowledge_store, "bundled_module",
                        lambda name: {"pytest_mm.txt": "mm", "pytest_fi.txt": "fi"}.get(name))

    yield docs

    # Remove everything the test wrote (shared HANA schema)
    for row in knowledge_store.list_documents():
        if row["name"].startswith("pytest_"):
            knowledge_store.get_vectors().delete(row["name"])
            with chat_store.database().cursor() as cursor:
                cursor.execute("DELETE FROM kb_documents WHERE name = ?", (row["name"],))
    knowledge_store.set_vectors(None)
    database.set_database(None)
    chat_store.reset()


MM_TEXT = "EKKO stores the purchase order header.\n\nEKPO stores the purchase order items."
FI_TEXT = "The GR/IR clearing account balances goods receipts and supplier invoices."


def test_shipped_documents_are_synchronised(kb):
    (kb / "pytest_mm.txt").write_text(MM_TEXT, encoding="utf-8")
    (kb / "pytest_fi.txt").write_text(FI_TEXT, encoding="utf-8")

    assert knowledge_store.sync_bundled() == {"pytest_fi.txt": "indexed", "pytest_mm.txt": "indexed"}
    assert knowledge_store.sync_bundled() == {"pytest_fi.txt": "unchanged", "pytest_mm.txt": "unchanged"}

    document = knowledge_store.get_document("pytest_mm.txt")
    assert (document["origin"], document["module"], document["chunks"]) == ("bundled", "mm", 1)
    assert knowledge_store.sources_for_module("fi") == ["pytest_fi.txt"]

    # Changed file: indexed again; removed file: dropped
    (kb / "pytest_mm.txt").write_text(MM_TEXT + "\n\nEKET stores the schedule lines.", encoding="utf-8")
    (kb / "pytest_fi.txt").unlink()
    assert knowledge_store.sync_bundled() == {"pytest_mm.txt": "indexed", "pytest_fi.txt": "removed"}
    assert knowledge_store.get_document("pytest_fi.txt") is None
    assert "pytest_fi.txt" not in knowledge_store.get_vectors().counts()


def test_search_returns_the_closest_chunks_of_the_allowed_documents(kb):
    (kb / "pytest_mm.txt").write_text(MM_TEXT, encoding="utf-8")
    (kb / "pytest_fi.txt").write_text(FI_TEXT, encoding="utf-8")
    knowledge_store.sync_bundled()

    best = knowledge_store.search("Which account balances goods receipts and supplier invoices?", 2,
                                  sources=["pytest_mm.txt", "pytest_fi.txt"])
    assert best[0]["source"] == "pytest_fi.txt"
    assert best[0]["distance"] < best[1]["distance"]
    assert set(best[0]) == {"source", "chunk", "distance", "content"}

    only_mm = knowledge_store.search("goods receipts supplier invoices", 3, sources=["pytest_mm.txt"])
    assert {r["source"] for r in only_mm} == {"pytest_mm.txt"}
    assert knowledge_store.search("anything", 3, sources=[]) == []


def test_uploaded_documents(kb):
    (kb / "pytest_mm.txt").write_text(MM_TEXT, encoding="utf-8")
    knowledge_store.sync_bundled()

    added = knowledge_store.add_document("pytest_tolerances.pdf", "Price tolerance PP is 5 percent.", "fi", ALICE, 1234)
    assert (added["origin"], added["module"], added["uploaded_by"], added["size_bytes"]) == ("uploaded", "fi", ALICE, 1234)
    assert knowledge_store.sources_for_module("fi") == ["pytest_tolerances.pdf"]
    assert knowledge_store.search("price tolerance", 1, sources=["pytest_tolerances.pdf"])[0]["source"] == \
        "pytest_tolerances.pdf"

    with pytest.raises(knowledge_store.KnowledgeError) as duplicate:
        knowledge_store.add_document("pytest_tolerances.pdf", "Other", "fi", BOB)
    assert duplicate.value.status == 409
    with pytest.raises(knowledge_store.KnowledgeError) as shipped:
        knowledge_store.add_document("pytest_mm.txt", "Other", None, BOB)
    assert shipped.value.status == 409
    with pytest.raises(knowledge_store.KnowledgeError) as empty:
        knowledge_store.add_document("pytest_empty.txt", "   ", None, BOB)
    assert empty.value.status == 422

    # Only the author removes an uploaded document; shipped documents stay
    with pytest.raises(knowledge_store.KnowledgeError) as other:
        knowledge_store.delete_document("pytest_tolerances.pdf", BOB)
    assert other.value.status == 403
    with pytest.raises(knowledge_store.KnowledgeError) as bundled:
        knowledge_store.delete_document("pytest_mm.txt", ALICE)
    assert bundled.value.status == 403
    knowledge_store.delete_document("pytest_tolerances.pdf", ALICE)
    assert knowledge_store.get_document("pytest_tolerances.pdf") is None
    assert "pytest_tolerances.pdf" not in knowledge_store.get_vectors().counts()

    # A failed embedding stores nothing
    def broken(texts):
        raise RuntimeError("AI Core down")
    knowledge_store.embed = broken
    with pytest.raises(RuntimeError):
        knowledge_store.add_document("pytest_broken.txt", "Some text", None, ALICE)
    assert knowledge_store.get_document("pytest_broken.txt") is None


def test_module_workspace_searches_its_added_documents(kb):
    from app.agent.agent import SAPAgent
    from app.modules import MODULES

    knowledge_store.add_document("pytest_fi_note.txt", FI_TEXT, "fi", ALICE)
    agent = SAPAgent.__new__(SAPAgent)          # no LLM client needed for this check
    agent.module = next(m for m in MODULES if m.key == "fi")
    sources = agent._module_sources()
    assert "sap_fi.txt" in sources and "pytest_fi_note.txt" in sources


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_add_list_and_delete(kb):
    from app import api

    client = TestClient(api.app)
    api.app.dependency_overrides[api.current_user] = lambda: ALICE
    try:
        response = client.post("/api/knowledge-base/documents", data={"module": "fi"},
                               files={"file": ("pytest_note.txt", FI_TEXT.encode(), "text/plain")})
        assert response.status_code == 200, response.text
        assert response.json()["module"] == "fi"

        assert client.post("/api/knowledge-base/documents", data={"module": "xx"},
                           files={"file": ("pytest_x.txt", b"x", "text/plain")}).status_code == 422
        assert client.post("/api/knowledge-base/documents", data={"module": "fi"},
                           files={"file": ("pytest_note.txt", b"again", "text/plain")}).status_code == 409

        listed = {d["name"]: d for d in client.get("/api/knowledge-base").json()["documents"]}
        assert listed["pytest_note.txt"]["can_delete"] is True
        assert listed["pytest_note.txt"]["origin"] == "uploaded"
        assert "uploaded_by" not in listed["pytest_note.txt"]

        # Attached document of Alice's conversation -> knowledge base
        session = "pytest-kb-session-01"
        attachment = chat_store.add_attachment(session, ALICE, "pytest_attached.txt", 10, None, MM_TEXT, False)
        moved = client.post("/api/knowledge-base/documents/from-attachment",
                            json={"session_id": session, "attachment_id": attachment["id"], "module": "mm"})
        assert moved.status_code == 200 and moved.json()["module"] == "mm"

        api.app.dependency_overrides[api.current_user] = lambda: BOB
        listed = {d["name"]: d for d in client.get("/api/knowledge-base").json()["documents"]}
        assert listed["pytest_note.txt"]["can_delete"] is False
        assert client.delete("/api/knowledge-base/documents/pytest_note.txt").status_code == 403
        # Bob cannot add Alice's attached document
        assert client.post("/api/knowledge-base/documents/from-attachment",
                           json={"session_id": session, "attachment_id": attachment["id"]}).status_code == 404

        api.app.dependency_overrides[api.current_user] = lambda: ALICE
        assert client.delete("/api/knowledge-base/documents/pytest_note.txt").status_code == 200
        assert client.delete("/api/knowledge-base/documents/pytest_attached.txt").status_code == 200
        chat_store.delete_attachment(session, ALICE, attachment["id"])
    finally:
        api.app.dependency_overrides.clear()
