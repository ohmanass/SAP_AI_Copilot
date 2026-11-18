"""
Interface languages offered in the Settings.

REVIEWED: translated by hand, shipped with the frontend (webapp/i18n/i18n_<code>.properties).
MACHINE: translated once by the AI model on demand (app.i18n.translator), cached in the database.
The frontend shows each language in its own name (Intl.DisplayNames); the
English name below is what the model is told to translate into.
"""

import re

SOURCE = "en"
REVIEWED = ("en", "fr", "de", "es")

MACHINE = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "el": "Greek",
    "et": "Estonian",
    "fi": "Finnish",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "ms": "Malay",
    "nb": "Norwegian Bokmål",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "pt-BR": "Brazilian Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian (Latin script)",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
}

_CODE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


def is_machine(code: str) -> bool:
    return bool(code) and _CODE.match(code) is not None and code in MACHINE


def catalogue() -> dict:
    """Languages for the Settings (codes only: the browser names them in their own language)."""
    return {"source": SOURCE, "reviewed": list(REVIEWED), "machine": sorted(MACHINE)}
