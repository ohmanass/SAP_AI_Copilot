"""
Interface translated by the AI model, for the languages without a reviewed translation.

The English texts of the frontend (webapp/i18n/i18n.properties) are translated
once per language, in parallel batches, and cached in the database (table
ui_translations) with the hash of their English text: when a text changes,
only that text is translated again. Every user then shares the translation.

Each translation is checked before it is kept: same {n} placeholders, same
technical tokens (USAGE_PRICES, data/documents, commands...), no added line,
reasonable length. A text that fails the checks stays in English (and is not
asked again until its English text changes); a text whose batch failed (model
unavailable) stays in English for now and is asked again next time.
"""

import hashlib
import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.i18n import languages, properties

logger = logging.getLogger("sap_ai_copilot.i18n")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Copied next to the backend by deploy.ps1; read from the frontend sources locally
SOURCE_FILES = (
    PROJECT_ROOT / "ui_i18n" / "i18n.properties",
    PROJECT_ROOT / "frontend" / "webapp" / "i18n" / "i18n.properties",
)

BATCH_SIZE = 60
PARALLEL_BATCHES = 4

PROMPT = """
You translate the user interface texts of an SAP enterprise application
(SAP AI Copilot, an AI assistant for SAP S/4HANA) from English into {language}.

You receive a JSON object: keys are message ids, values are English texts.
Return a JSON object with exactly the same keys and the translated texts.

Rules:
- Natural, concise user interface wording, as in the {language} version of SAP Fiori apps.
- SAP business terms as SAP translates them in {language} (purchase order, sales order,
  goods receipt, supplier invoice, plant, posting date, three-way match...).
- The key tells the context: "dataCol." table column header, "dataStatus." document
  status, "activity." progress message, "tool." tool name, "kb" knowledge base.
- Keep unchanged: placeholders such as {{0}} and {{1}}, product and service names
  (SAP AI Copilot, SAP Generative AI Hub, SAP AI Core, SAP S/4HANA, SAP HANA Cloud,
  SAPUI5, SAP BTP, ChromaDB, OData, Markdown, PDF, Excel, Word), technical tokens
  (USAGE_PRICES, SAP_DATA_SOURCE=odata, .env, data/documents, commands, file names,
  file extensions), table names such as EKKO, numbers and emoji.
- Each placeholder keeps its meaning: in "{{0}} shown of {{1}}", {{0}} is the part and
  {{1}} the total, whatever the word order of {language} requires.
- Never add explanations, quotes or line breaks. Output the JSON object only.
"""

_PLACEHOLDER = re.compile(r"\{\d+\}")
# Tokens that must survive the translation unchanged
# Tokens that must survive the translation unchanged. ASCII boundaries, not \b: in
# Japanese or Chinese the next character is a letter too ("USAGE_PRICESを設定").
_A = "A-Za-z0-9_"
_PROTECTED = re.compile(
    r"\{\d+\}"                                          # placeholders
    rf"|(?<![{_A}])[A-Z][A-Z0-9]*_[A-Z0-9_]+(?![{_A}])"  # USAGE_PRICES, SAP_DATA_SOURCE
    rf"|(?<![{_A}./-])[{_A}.-]*[a-z0-9]/[{_A}./-]+"     # data/documents, evals/agent_eval.py
    rf"|(?<![{_A}])\.[a-z]{{2,4}}(?![{_A}])"            # .env, .docx
    rf"|(?<![{_A}])[A-Z]{{4}}(?![{_A}])"                # EKKO, EKPO, HANA, IBAN
    r"|\[masked"                                        # marker of masked values
    rf"|(?<![{_A}-])--[a-z]+"                           # command options
)

# Version of the checks: texts rejected by older checks are asked again
CHECKS_VERSION = "3"

_locks = {}
_locks_guard = threading.Lock()
_source_cache = {"path": None, "mtime": None, "texts": None}


class TranslationUnavailable(Exception):
    """No translation could be produced (model unavailable): message safe to show."""


# ----------------------------------------------------------------------
# English source
# ----------------------------------------------------------------------

def source_file() -> Path:
    for path in SOURCE_FILES:
        if path.is_file():
            return path
    raise FileNotFoundError("English interface texts (i18n.properties) not found")


def source_texts() -> dict:
    """English texts by key (re-read when the file changes)."""
    path = source_file()
    mtime = path.stat().st_mtime
    if _source_cache["path"] != path or _source_cache["mtime"] != mtime:
        _source_cache.update(path=path, mtime=mtime, texts=properties.parse(path.read_text(encoding="utf-8")))
    return _source_cache["texts"]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------

def displayed(text: str) -> str:
    """Text as the user reads it: MessageFormat '' is a single quote in texts with placeholders."""
    return text.replace("''", "'") if _PLACEHOLDER.search(text) else text


def checked(source: str, translation) -> str | None:
    """The translation ready for the bundle, or None when it must not be used."""

    if not isinstance(translation, str):
        return None
    text = translation.strip()
    if not text or "\n" in text and "\n" not in source:
        return None
    if len(text) > max(40, 4 * len(source)):
        return None
    # Same placeholders; every protected token of the source still there (a translation
    # may add one: "IBANs" becomes "IBAN" in Italian)
    if sorted(_PLACEHOLDER.findall(source)) != sorted(_PLACEHOLDER.findall(text)):
        return None
    if Counter(_PROTECTED.findall(displayed(source))) - Counter(_PROTECTED.findall(text)):
        return None
    if _PLACEHOLDER.search(source):
        # A straight quote opens a quoted section in MessageFormat: typographic apostrophe instead
        text = text.replace("'", "’")
    return text


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------

def _ask_model(language_name: str, texts: dict) -> dict:
    from app.llm import usage

    response = usage.chat_completion(
        "translation",
        model_name=_model_name(),
        temperature=0,
        max_tokens=8000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT.format(language=language_name).strip()},
            {"role": "user", "content": json.dumps(texts, ensure_ascii=False)}
        ]
    )
    answer = json.loads(response.choices[0].message.content or "{}")
    return answer if isinstance(answer, dict) else {}


def _model_name():
    import os
    return os.getenv("GENAI_MODEL_NAME", "gpt-4o-mini")


def _translate(code: str, texts: dict) -> dict:
    """{key: checked translation or None (stays English)}; keys of failed batches are left out."""

    language_name = languages.MACHINE[code]
    keys = list(texts)
    batches = [{key: displayed(texts[key]) for key in keys[i:i + BATCH_SIZE]}
               for i in range(0, len(keys), BATCH_SIZE)]

    def run(batch):
        try:
            answer = _ask_model(language_name, batch)
        except Exception:
            logger.warning("Translation batch into %s failed", code, exc_info=True)
            return {}
        return {key: checked(texts[key], answer.get(key)) for key in batch}

    results = {}
    with ThreadPoolExecutor(max_workers=PARALLEL_BATCHES, thread_name_prefix=f"i18n-{code}") as pool:
        for translated in pool.map(run, batches):
            results.update(translated)
    rejected = [key for key, value in results.items() if value is None]
    if rejected:
        logger.info("%s text(s) kept in English for %s: %s", len(rejected), code, rejected[:10])
    return results


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------

def _database():
    from app.storage import chat_store
    return chat_store.database()


def _cached(code: str) -> dict:
    with _database().cursor() as cursor:
        rows = cursor.execute(
            "SELECT msg_key, source_hash, translated FROM ui_translations WHERE language = ?", (code,)
        ).fetchall()
    return {row["msg_key"]: (row["source_hash"], row["translated"]) for row in rows}


def _store(code: str, texts: dict, translations: dict):
    with _database().cursor() as cursor:
        for key, translated in translations.items():
            values = (_stored_hash(texts[key], translated), translated)
            cursor.execute(
                "UPDATE ui_translations SET source_hash = ?, translated = ? WHERE language = ? AND msg_key = ?",
                (*values, code, key)
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO ui_translations (language, msg_key, source_hash, translated) VALUES (?, ?, ?, ?)",
                    (code, key, *values)
                )


def _stored_hash(text: str, translated) -> str:
    """Hash kept with a row: a rejected text also records the checks that rejected it."""
    return _hash(text) if translated is not None else f"{_hash(text)}#{CHECKS_VERSION}"


def _lock(code: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(code, threading.Lock())


def bundle(code: str) -> dict:
    """
    Every interface text in this language (English where no checked translation
    exists). Texts never translated, or changed in English, are translated first.
    """

    if not languages.is_machine(code):
        raise ValueError(f"No translation by the model for {code!r}")
    texts = source_texts()

    def missing(cache):
        return {key: text for key, text in texts.items()
                if key not in cache or cache[key][0] != _stored_hash(text, cache[key][1])}

    cache = _cached(code)
    if missing(cache):
        # One translation at a time per language: concurrent requests wait and reuse it
        with _lock(code):
            cache = _cached(code)
            todo = missing(cache)
            if todo:
                translations = _translate(code, todo)
                if not translations and len(todo) == len(texts):
                    raise TranslationUnavailable("The AI model could not translate the interface. Try again later.")
                _store(code, texts, translations)
                cache = _cached(code)

    return {key: (cache.get(key, (None, None))[1] or text) for key, text in texts.items()}


def bundle_file(code: str) -> str:
    """The bundle as a .properties file for SAPUI5."""
    return properties.dump(bundle(code), header=f"SAP AI Copilot interface in {languages.MACHINE[code]}, "
                                                "translated by the AI model (app/i18n/translator.py)")
