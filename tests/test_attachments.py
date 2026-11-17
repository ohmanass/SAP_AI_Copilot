"""Attached documents: text extraction, storage, reading by the agent and API endpoints."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.attachments import extract, reader
from app.attachments.extract import ExtractionError, extract_text
from app.storage import chat_store

SESSION = "test-session-0001"


# ---------------------------------------------------------------------------
# Office documents built in memory
# ---------------------------------------------------------------------------

def docx_bytes(paragraphs, table=None):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


def xlsx_bytes(rows):
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    strings = sorted({v for row in rows for v in row if isinstance(v, str)})
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml",
                         f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                         f'<sheet name="Invoices" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/sharedStrings.xml",
                         f'<sst xmlns="{main}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml",
                         f'<worksheet xmlns="{main}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    return buffer.getvalue()


def pdf_bytes(pages):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(width=300, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
        })
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def test_text_files_are_decoded():
    assert extract_text("notes.md", "Commande 4500000001 : réception".encode("utf-8"))[0] == \
        "Commande 4500000001 : réception"
    # Windows-1252 file (Excel CSV export)
    assert extract_text("export.csv", "Fournisseur;Montant\nCafé;12".encode("cp1252"))[0] == "Fournisseur;Montant\nCafé;12"


def test_pdf_text_by_page():
    text, pages, truncated = extract_text("invoice.pdf", pdf_bytes(["Invoice 5100000001", "Total 1000 EUR"]))
    assert pages == 2 and not truncated
    assert "[Page 1]" in text and "Invoice 5100000001" in text
    assert "[Page 2]" in text and "Total 1000 EUR" in text


def test_pdf_without_text_layer():
    with pytest.raises(ExtractionError, match="scanned"):
        extract_text("scan.pdf", pdf_bytes([""]))


def test_docx_paragraphs_and_tables():
    data = docx_bytes(["Purchase order 4500000001", "Delivery in 2 weeks"], table=[["Item", "Qty"], ["10", "100"]])
    text, pages, _ = extract_text("po.docx", data)
    assert pages is None
    assert text.splitlines() == ["Purchase order 4500000001", "Delivery in 2 weeks", "Item | Qty", "10 | 100"]


def test_xlsx_sheets_as_tab_separated_rows():
    text, _, _ = extract_text("invoices.xlsx", xlsx_bytes([["Invoice", "Amount"], ["5100000001", 1000.5]]))
    assert text.splitlines() == ["[Sheet Invoices]", "Invoice\tAmount", "5100000001\t1000.5"]


@pytest.mark.parametrize("name, data, message", [
    ("virus.exe", b"MZ", "Unsupported file type"),
    ("empty.txt", b"", "empty"),
    ("fake.docx", b"not a zip", "damaged"),
    ("big.txt", b"x" * (extract.MAX_FILE_BYTES + 1), "larger than"),
])
def test_rejected_files(name, data, message):
    with pytest.raises(ExtractionError, match=message):
        extract_text(name, data)


def test_zip_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr(extract, "MAX_UNZIPPED_BYTES", 1000)
    with pytest.raises(ExtractionError, match="uncompressed"):
        extract_text("bomb.docx", docx_bytes(["A" * 5000]))


def test_long_text_is_truncated(monkeypatch):
    monkeypatch.setattr(extract, "MAX_TEXT_CHARS", 10)
    text, _, truncated = extract_text("long.txt", b"0123456789ABCDEF")
    assert (text, truncated) == ("0123456789", True)


# ---------------------------------------------------------------------------
# Storage and reading by the agent
# ---------------------------------------------------------------------------

@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "CHAT_DB_PATH", tmp_path / "chat_history.db")
    chat_store.initialize()
    return chat_store


def test_note_lists_documents():
    assert reader.note([]) == ""
    note = reader.note([{"id": 3, "name": "invoice.pdf", "pages": 2, "chars": 12345},
                        {"id": 4, "name": "notes.txt", "pages": None, "chars": 80}])
    assert note == "\n\n[Attached documents: #3 invoice.pdf (2 pages, 12 345 characters); #4 notes.txt (80 characters)]"


def test_documents_are_read_part_by_part(chat_db, monkeypatch):
    monkeypatch.setattr(reader, "PART_CHARS", 10)
    doc = chat_db.add_attachment(SESSION, "long.txt", 25, None, "A" * 10 + "B" * 10 + "C" * 5, False)

    first = reader.read(SESSION, doc["id"])
    assert (first["part"], first["parts"], first["text"]) == (1, 3, "A" * 10)
    assert "part=2" in first["next"]
    last = reader.read(SESSION, doc["id"], 3)
    assert last["text"] == "C" * 5 and "next" not in last
    assert "does not exist" in reader.read(SESSION, doc["id"], 4)["error"]


def test_documents_of_another_conversation_are_not_readable(chat_db):
    doc = chat_db.add_attachment(SESSION, "secret.txt", 6, None, "secret", False)
    assert "not attached" in reader.read("other-session-0001", doc["id"])["error"]
    assert reader.read("other-session-0001")["documents"] == []
    assert reader.read(SESSION)["documents"][0]["name"] == "secret.txt"


def test_attachments_are_saved_with_the_message_and_deleted_with_the_conversation(chat_db):
    doc = chat_db.add_attachment(SESSION, "invoice.pdf", 100, 1, "Invoice", False)
    chat_db.save_exchange(SESSION, "Check this invoice", "Done", [], [], attachments=[doc])
    messages = chat_db.get_conversation(SESSION)["messages"]
    assert messages[0]["attachments"][0]["name"] == "invoice.pdf"
    assert messages[1]["attachments"] == []

    chat_db.delete_conversation(SESSION)
    assert chat_db.list_attachments(SESSION) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
def client(chat_db):
    from app import api
    return TestClient(api.app)


def test_upload_list_and_delete(client):
    response = client.post(f"/api/sessions/{SESSION}/attachments",
                           files={"file": ("C:\\fakepath\\notes.txt", b"Hello SAP", "text/plain")})
    assert response.status_code == 200
    document = response.json()
    assert (document["name"], document["chars"], document["size_bytes"]) == ("notes.txt", 9, 9)

    assert client.get(f"/api/sessions/{SESSION}/attachments").json()["attachments"][0]["id"] == document["id"]
    assert client.delete(f"/api/sessions/{SESSION}/attachments/{document['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{SESSION}/attachments/{document['id']}").status_code == 404


def test_file_names_are_cleaned():
    from app.api import _clean_filename
    assert _clean_filename("C:\\fakepath\\facture\x07 2026.pdf") == "facture 2026.pdf"
    assert _clean_filename("../../etc/passwd") == "passwd"
    assert _clean_filename("") == "document"
    assert len(_clean_filename("a" * 500 + ".txt")) == 200


def test_upload_errors(client):
    bad = client.post(f"/api/sessions/{SESSION}/attachments", files={"file": ("tool.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422 and "Unsupported" in bad.json()["detail"]
    invalid_id = client.post("/api/sessions/bad id!/attachments", files={"file": ("a.txt", b"x", "text/plain")})
    assert invalid_id.status_code == 422
