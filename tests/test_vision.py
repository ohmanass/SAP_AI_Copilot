"""Images and scanned PDF pages read by the AI model (the model is faked: no AI call)."""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.attachments import extract, vision
from app.attachments.extract import ExtractionError, extract_text
from app.storage import chat_store, database
from tests.test_attachments import pdf_bytes


def image_bytes(size=(800, 600), mode="RGB", fmt="PNG", color="white"):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, fmt)
    return output.getvalue()


def scanned_pdf(pages=1):
    images = [Image.new("RGB", (600, 800), "white") for _ in range(pages)]
    output = io.BytesIO()
    images[0].save(output, "PDF", save_all=True, append_images=images[1:])
    return output.getvalue()


@pytest.fixture
def model(monkeypatch):
    """Fake vision model: records the images it receives."""

    calls = []

    def fake(data_url):
        calls.append(data_url)
        return f"INVOICE INF-{len(calls)}\nTotal due: 2,646.00 USD\npassword: Welc0me!2024"

    monkeypatch.setattr(vision, "_ask_model", fake)
    return calls


def test_an_image_is_transcribed(model):
    text, pages, truncated = extract_text("invoice.png", image_bytes())
    assert text.startswith(extract.IMAGE_HEADER)
    assert "Total due: 2,646.00 USD" in text and pages is None and not truncated
    assert extract.transcribed_parts(text) == 1
    assert len(model) == 1 and model[0].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("name, mode, fmt", [
    ("scan.tif", "1", "TIFF"),          # black and white fax scan
    ("screen.bmp", "RGB", "BMP"),
    ("logo.gif", "P", "GIF"),
    ("photo.webp", "RGB", "WEBP"),
])
def test_image_formats_are_converted_for_the_model(model, name, mode, fmt):
    extract_text(name, image_bytes(mode=mode, fmt=fmt))
    assert model[0].split(";")[0] in ("data:image/png", "data:image/jpeg")


def test_large_images_are_scaled_down(model):
    extract_text("photo.jpg", image_bytes(size=(6000, 4000), fmt="JPEG"))
    sent = Image.open(io.BytesIO(base64.b64decode(model[0].split(",", 1)[1])))
    assert max(sent.size) == vision.MAX_SIDE


def test_tiny_and_broken_images_are_refused(model):
    with pytest.raises(ExtractionError, match="too small"):
        extract_text("icon.png", image_bytes(size=(16, 16)))
    with pytest.raises(ExtractionError, match="could not be read"):
        extract_text("broken.png", b"not an image at all")
    assert model == []


def test_a_scanned_pdf_is_transcribed_page_by_page(model):
    text, pages, _ = extract_text("scan.pdf", scanned_pdf(3))
    assert pages == 3 and len(model) == 3
    assert extract.transcribed_parts(text) == 3
    assert "[Page 2]\n[transcribed by the AI model from the scanned page]\nINVOICE INF-" in text


def test_text_pages_of_a_pdf_are_not_sent_to_the_model(model):
    text, pages, _ = extract_text("invoice.pdf", pdf_bytes(["Invoice 5100000001"]))
    assert model == [] and extract.transcribed_parts(text) == 0


def test_scanned_pages_are_limited(model, monkeypatch):
    monkeypatch.setattr(vision, "MAX_SCANNED_PAGES", 2)
    text, pages, _ = extract_text("long_scan.pdf", scanned_pdf(4))
    assert len(model) == 2 and pages == 4
    assert "2 more page(s) without text were not read" in text


def test_model_failure_gives_a_clear_error(monkeypatch):
    def broken(data_url):
        raise RuntimeError("deployment not found")

    monkeypatch.setattr(vision, "_ask_model", broken)
    with pytest.raises(ExtractionError, match="could not read the image"):
        extract_text("invoice.png", image_bytes())


def test_the_model_answer_is_cleaned_of_a_code_fence(monkeypatch):
    class Response:
        class choices:  # noqa: N801 - mimics the OpenAI response
            pass

    message = type("M", (), {"content": "```text\nINVOICE 1\n```"})
    Response.choices = [type("C", (), {"message": message})]
    import gen_ai_hub.proxy.native.openai as proxy
    monkeypatch.setattr(proxy.chat.completions, "create", lambda **kwargs: Response)
    assert vision._ask_model("data:image/png;base64,") == "INVOICE 1"


def test_attached_image_is_transcribed_masked_and_reported(model, tmp_path):
    from app import api

    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    try:
        client = TestClient(api.app)
        session = "vision-session-01"
        response = client.post(f"/api/sessions/{session}/attachments",
                               files={"file": ("invoice.jpg", image_bytes(fmt="JPEG"), "image/jpeg")})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["transcribed"] == 1 and body["masked"] == {"credential": 1}
        _, text = chat_store.get_attachment_text(session, "local", body["id"])
        assert "Total due: 2,646.00 USD" in text and "Welc0me" not in text
    finally:
        database.set_database(None)
        chat_store.reset()
