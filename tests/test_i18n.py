"""
Interface languages: reviewed translations (complete and consistent), .properties
format, translation by the AI model (faked: no AI call) and its API.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.i18n import languages, properties, translator
from app.storage import chat_store, database

WEBAPP = Path(__file__).resolve().parent.parent / "frontend" / "webapp"
I18N = WEBAPP / "i18n"
PLACEHOLDER = re.compile(r"\{\d+\}")


def bundle(code):
    name = "i18n.properties" if code == languages.SOURCE else f"i18n_{code}.properties"
    return properties.parse((I18N / name).read_text(encoding="utf-8"))


ENGLISH = bundle(languages.SOURCE)
TRANSLATED = [code for code in languages.REVIEWED if code != languages.SOURCE]


# ---------------------------------------------------------------------------
# Reviewed translations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", TRANSLATED)
def test_reviewed_translation_has_every_text_and_nothing_else(code):
    texts = bundle(code)
    assert sorted(set(ENGLISH) - set(texts)) == [], "missing"
    assert sorted(set(texts) - set(ENGLISH)) == [], "obsolete"
    assert [key for key, value in texts.items() if not value.strip()] == []


@pytest.mark.parametrize("code", TRANSLATED)
def test_reviewed_translation_keeps_the_placeholders(code):
    texts = bundle(code)
    wrong = [key for key in ENGLISH if sorted(PLACEHOLDER.findall(ENGLISH[key])) != sorted(PLACEHOLDER.findall(texts[key]))]
    assert wrong == []


@pytest.mark.parametrize("code", languages.REVIEWED)
def test_no_lone_straight_quote_in_texts_with_placeholders(code):
    """In MessageFormat a single ' starts a quoted section: "{0}" after it would not be replaced."""
    texts = bundle(code)
    wrong = [key for key, value in texts.items()
             if PLACEHOLDER.search(value) and re.search(r"(?<!')'(?!')", value)]
    assert wrong == []


def test_the_reviewed_languages_are_the_same_everywhere():
    files = sorted(["en"] + [p.stem.split("_", 1)[1] for p in I18N.glob("i18n_*.properties")])
    assert files == sorted(languages.REVIEWED)

    language_js = (WEBAPP / "util" / "Language.js").read_text(encoding="utf-8")
    listed = re.search(r"const REVIEWED = \[([^\]]*)\]", language_js).group(1)
    assert sorted(re.findall(r'"([\w-]+)"', listed)) == sorted(languages.REVIEWED)

    manifest = json.loads((WEBAPP / "manifest.json").read_text(encoding="utf-8"))
    supported = manifest["sap.ui5"]["models"]["i18n"]["settings"]["supportedLocales"]
    assert sorted(code or "en" for code in supported) == sorted(languages.REVIEWED)
    assert manifest["sap.app"]["i18n"]["supportedLocales"] == supported


def test_machine_languages_never_duplicate_a_reviewed_one():
    assert not set(languages.MACHINE) & set(languages.REVIEWED)
    assert languages.is_machine("ja") and languages.is_machine("pt-BR")
    for code in ("fr", "xx", "ja/../en", "JA", ""):
        assert not languages.is_machine(code)


# ---------------------------------------------------------------------------
# .properties format
# ---------------------------------------------------------------------------

def test_properties_round_trip():
    texts = {"a.b": "Café 👍 {0} = 50 % : OK", "with space": " leading", "path": "C:\\temp\\n", "line": "1\n2"}
    dumped = properties.dump(texts, header="Generated")
    assert dumped.isascii() and dumped.startswith("# Generated\n")
    assert properties.parse(dumped) == texts


def test_properties_comments_continuations_and_separators():
    text = "\ufeff# comment\n! other\nkey1 = value \\\n    continued\nkey2:colon\nkey3\n\nkey4=\\u00e9t\\u00e9\n"
    assert properties.parse(text) == {"key1": "value continued", "key2": "colon", "key3": "", "key4": "été"}


# ---------------------------------------------------------------------------
# Translation by the AI model
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    database.set_database(database.SQLiteDatabase(tmp_path / "history.db"))
    chat_store.reset()
    chat_store.initialize()
    source = tmp_path / "i18n.properties"
    source.write_text(
        "hello=Hello\n"
        "shown={0} shown of {1}\n"
        "prices=Set USAGE_PRICES to estimate costs\n"
        "quote={0} The assistant only uses this workspace''s tools.\n",
        encoding="utf-8")
    monkeypatch.setattr(translator, "SOURCE_FILES", (source,))
    yield source
    database.set_database(None)
    chat_store.reset()


class FakeModel:
    """Translates into "Japanese" by tagging each text; records the batches it received."""

    def __init__(self, broken=()):
        self.batches = []
        self.broken = broken

    def __call__(self, language_name, texts):
        self.batches.append(dict(texts))
        return {key: (f"BAD {value}" if key in self.broken else f"[ja] {value}") for key, value in texts.items()}


def test_interface_is_translated_once_and_cached(store, monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(translator, "_ask_model", model)

    texts = translator.bundle("ja")
    assert texts["hello"] == "[ja] Hello" and texts["shown"] == "[ja] {0} shown of {1}"
    # The model reads '' as the quote the user sees; the bundle gets a typographic apostrophe
    assert model.batches[0]["quote"] == "{0} The assistant only uses this workspace's tools."
    assert texts["quote"] == "[ja] {0} The assistant only uses this workspace’s tools."

    assert translator.bundle("ja") == texts
    assert len(model.batches) == 1                      # cached: no second call


def test_only_changed_texts_are_translated_again(store, monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(translator, "_ask_model", model)
    translator.bundle("ja")

    store.write_text(store.read_text(encoding="utf-8").replace("hello=Hello", "hello=Hi") + "new=New\n",
                     encoding="utf-8")
    translator._source_cache["mtime"] = None
    texts = translator.bundle("ja")
    assert model.batches[-1] == {"hello": "Hi", "new": "New"}
    assert texts["hello"] == "[ja] Hi" and texts["new"] == "[ja] New"


def test_translations_failing_the_checks_stay_in_english(store, monkeypatch):
    class Careless(FakeModel):
        def __call__(self, language_name, texts):
            answer = super().__call__(language_name, texts)
            answer["shown"] = "{0} affichés"                         # placeholder lost
            answer["prices"] = "Définissez USAGE_PRIX"                # technical token translated
            return answer

    model = Careless()
    monkeypatch.setattr(translator, "_ask_model", model)
    texts = translator.bundle("ja")
    assert texts["shown"] == "{0} shown of {1}"
    assert texts["prices"] == "Set USAGE_PRICES to estimate costs"
    assert texts["hello"] == "[ja] Hello"

    translator.bundle("ja")
    assert len(model.batches) == 1                      # rejected texts are not asked again...

    monkeypatch.setattr(translator, "CHECKS_VERSION", "next")
    translator.bundle("ja")
    assert set(model.batches[-1]) == {"shown", "prices"}   # ...until the checks change


def test_a_failed_batch_is_retried_and_a_total_failure_is_reported(store, monkeypatch):
    def down(language_name, texts):
        raise RuntimeError("AI Core unavailable")

    monkeypatch.setattr(translator, "_ask_model", down)
    with pytest.raises(translator.TranslationUnavailable):
        translator.bundle("ja")

    model = FakeModel()
    monkeypatch.setattr(translator, "_ask_model", model)
    assert translator.bundle("ja")["hello"] == "[ja] Hello"


@pytest.mark.parametrize("source, translation, expected", [
    ("Set USAGE_PRICES to estimate costs", "USAGE_PRICESを設定してコストを見積もる", "USAGE_PRICESを設定してコストを見積もる"),
    ("Replaced by “[masked …]”; IBANs too", "Sostituiti con “[masked ...]”; anche gli IBAN", "Sostituiti con “[masked ...]”; anche gli IBAN"),
    ("Start it with: uvicorn app.api:app --reload --port 8000", "Démarrez avec : uvicorn app.api:app --port 8000", None),
    ("Explain EKKO and EKPO.", "Explique EKKO.", None),
    ("{0} rows", "{0} {0} lignes", None),
    ("Short", "x" * 100, None),
    ("One line", "Two\nlines", None),
    ("{0} isn't here", "{0} n'est pas là", "{0} n’est pas là"),
])
def test_checks_of_a_translation(source, translation, expected):
    assert translator.checked(source, translation) == expected


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_language_catalogue_api():
    from app import api

    body = TestClient(api.app).get("/api/i18n/languages").json()
    assert body["reviewed"] == list(languages.REVIEWED) and "ja" in body["machine"]


def test_translated_bundle_api(store, monkeypatch):
    from app import api

    monkeypatch.setattr(translator, "_ask_model", FakeModel())
    client = TestClient(api.app)

    response = client.get("/api/i18n/ja.properties")
    assert response.status_code == 200 and response.text.isascii()
    assert properties.parse(response.text)["hello"] == "[ja] Hello"

    etag = response.headers["etag"]
    assert client.get("/api/i18n/ja.properties", headers={"If-None-Match": etag}).status_code == 304

    for path in ("fr", "xx", "..%2Fsecret"):
        assert client.get(f"/api/i18n/{path}.properties").status_code == 404


def test_translated_bundle_api_when_the_model_is_down(store):
    from app import api

    # conftest: the model raises in the offline tests
    response = TestClient(api.app).get("/api/i18n/ja.properties")
    assert response.status_code == 503 and "translate" in response.json()["detail"]
