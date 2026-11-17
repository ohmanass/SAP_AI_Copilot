"""Masking of sensitive data in attached documents and in the shared knowledge base."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.attachments.sensitive import PRIVATE, SHARED, mask
from app.storage import chat_store, database

ROOT = Path(__file__).resolve().parent.parent

USERS_CSV = (
    "USERNAME;USERGROUP;VALID_FROM;VALID_UNTIL;PASSWORD\n"
    "user_echec;ECHEC;2025.12.10;2030.12.18;Error\n"
    "user_b;SALES;2024.01.01;2026.01.01;S3cr3t!\n"
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_password_column_of_a_csv_is_masked_and_nothing_else():
    text, found = mask(USERS_CSV, "data_user.csv", SHARED)
    assert found == {"credential": 2}
    lines = text.splitlines()
    assert lines[0] == "USERNAME;USERGROUP;VALID_FROM;VALID_UNTIL;PASSWORD"
    assert lines[1] == "user_echec;ECHEC;2025.12.10;2030.12.18;[masked password]"
    assert "S3cr3t!" not in text and "Error" not in text


@pytest.mark.parametrize("header", ["Mot de passe", "pwd", "API_KEY", "client-secret", "Token", "PIN"])
def test_credential_column_names(header):
    text, found = mask(f"user,{header}\nalice,value-123\n", "list.csv")
    assert found == {"credential": 1} and "value-123" not in text


def test_excel_sheets_are_masked_per_sheet():
    sheets = "[Sheet Users]\nNAME\tPassword\nalice\tP@ss1\n\n[Sheet Groups]\nGROUP\tOWNER\nadmins\talice"
    text, found = mask(sheets, "book.xlsx")
    assert found == {"credential": 1}
    assert "[Sheet Groups]\nGROUP\tOWNER\nadmins\talice" in text and "P@ss1" not in text


def test_key_value_secrets_in_text():
    text, found = mask(
        "Login admin, password: Welc0me!2024. Password policy: must be 8 characters.\n"
        "api_key=sk-abcdef123456\nLe mot de passe est Az3rty#1", "notes.txt")
    assert found == {"credential": 3}
    assert "password: [masked password]. Password policy: must be 8 characters." in text
    assert "api_key=[masked secret]" in text
    assert "Welc0me" not in text and "sk-abcdef" not in text and "Az3rty" not in text


def test_private_keys_and_tokens():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlLXZhbHVl"
    text, found = mask(f"key:\n{pem}\nheader: Authorization {jwt}", "config.txt")
    assert found == {"credential": 2}
    assert "MIIEow" not in text and jwt not in text


def test_card_numbers_need_a_valid_checksum():
    text, found = mask("Paid with 4111 1111 1111 1111; ref 4111 1111 1111 1112; material 000000000000000123",
                       "receipt.txt")
    assert found == {"card": 1}
    assert "[masked card number …1111]" in text
    assert "4111 1111 1111 1112" in text and "000000000000000123" in text


def test_ibans_are_masked_only_in_the_shared_knowledge_base():
    invoice = "Bank: DE89 3704 0044 0532 0130 00 (Commerzbank). Wrong: DE00 3704 0044 0532 0130 00"
    private, found_private = mask(invoice, "invoice.pdf", PRIVATE)
    assert private == invoice and found_private == {}
    shared, found_shared = mask(invoice, "invoice.pdf", SHARED)
    assert found_shared == {"iban": 1}
    assert "[masked IBAN DE…3000]" in shared and "DE00 3704" in shared     # invalid checksum kept


def test_sap_documents_are_not_changed():
    """No false positive on the shipped SAP documentation."""
    for path in (ROOT / "data" / "documents").glob("*.txt"):
        text = path.read_text(encoding="utf-8")
        assert mask(text, path.name, SHARED) == (text, {}), path.name


def test_sap_numbers_are_not_masked():
    text = "PO 4500043954 item 10, invoice 5109900184, material MZ-TG-A41, amount 165200.00 USD, plant 1010"
    assert mask(text, "notes.txt", SHARED) == (text, {})


# ---------------------------------------------------------------------------
# Storage, knowledge base and API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import api
    from app.rag import knowledge_store
    from tests.test_knowledge import fake_embedding

    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    knowledge_store.set_vectors(knowledge_store.ChromaVectors(tmp_path / "chroma"))
    monkeypatch.setattr(knowledge_store, "embed", lambda texts: [fake_embedding(t) for t in texts])
    yield TestClient(api.app)
    knowledge_store.set_vectors(None)
    database.set_database(None)
    chat_store.reset()


def test_attached_documents_are_stored_masked(client):
    session = "sensitive-session-01"
    response = client.post(f"/api/sessions/{session}/attachments",
                           files={"file": ("data_user.csv", USERS_CSV.encode(), "text/csv")})
    assert response.status_code == 200
    assert response.json()["masked"] == {"credential": 2}

    summary, text = chat_store.get_attachment_text(session, "local", response.json()["id"])
    assert "Error" not in text and "S3cr3t!" not in text
    assert summary["masked"] == {"credential": 2}

    from app.attachments import reader
    assert "sensitive values masked: 2 credential" in reader.note([summary])


def test_knowledge_base_documents_are_stored_masked(client):
    from app.rag import knowledge_store

    response = client.post("/api/knowledge-base/documents", data={"module": ""},
                           files={"file": ("data_user.csv", USERS_CSV.encode(), "text/csv")})
    assert response.status_code == 200, response.text
    assert response.json()["masked"] == {"credential": 2}

    with chat_store.database().cursor() as cursor:
        stored = cursor.execute("SELECT text FROM kb_documents WHERE name = 'data_user.csv'").fetchone()["text"]
    assert "Error" not in stored and "S3cr3t!" not in stored
    chunks = knowledge_store.search("password of user_echec", 3)
    assert chunks and all("S3cr3t!" not in c["content"] and ";Error" not in c["content"] for c in chunks)

    listed = {d["name"]: d for d in client.get("/api/knowledge-base").json()["documents"]}
    assert listed["data_user.csv"]["masked"] == {"credential": 2}


def test_attachment_added_to_the_knowledge_base_keeps_its_masking_and_masks_ibans(client):
    session = "sensitive-session-02"
    invoice = "Invoice INF-1\npassword: Welc0me!2024\nBank DE89 3704 0044 0532 0130 00"
    upload = client.post(f"/api/sessions/{session}/attachments",
                         files={"file": ("invoice.txt", invoice.encode(), "text/plain")}).json()
    assert upload["masked"] == {"credential": 1}           # IBAN kept in the private conversation

    added = client.post("/api/knowledge-base/documents/from-attachment",
                        json={"session_id": session, "attachment_id": upload["id"], "module": "fi"})
    assert added.status_code == 200, added.text
    assert added.json()["masked"] == {"credential": 1, "iban": 1}


def test_documents_stored_before_the_masking_are_masked_at_startup(client):
    from app.rag import knowledge_store

    # As stored by the previous version: text in clear, masked = NULL
    knowledge_store._index("old_users.csv", USERS_CSV, None, "uploaded", "local", 100)
    session = "sensitive-session-03"
    old = chat_store.add_attachment(session, "local", "old.txt", 10, None, "password: Welc0me!2024", False)
    with chat_store.database().cursor() as cursor:
        cursor.execute("UPDATE kb_documents SET masked = NULL")
        cursor.execute("UPDATE attachments SET masked = NULL")

    assert knowledge_store.mask_unchecked_documents() == ["old_users.csv"]
    assert chat_store.mask_unchecked_attachments() == 1

    document = knowledge_store.get_document("old_users.csv")
    assert document["masked"] == {"credential": 2}
    chunks = knowledge_store.search("user_echec password", 3, sources=["old_users.csv"])
    assert chunks and "S3cr3t!" not in chunks[0]["content"] and "[masked password]" in chunks[0]["content"]
    summary, text = chat_store.get_attachment_text(session, "local", old["id"])
    assert "Welc0me" not in text and summary["masked"] == {"credential": 1}

    # Checked once: nothing to do the next time
    assert knowledge_store.mask_unchecked_documents() == []
    assert chat_store.mask_unchecked_attachments() == 0
