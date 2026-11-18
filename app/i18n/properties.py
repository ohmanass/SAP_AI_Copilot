"""
Java .properties format, as read by SAPUI5 resource bundles.

Only what the app's files use is supported: comments (# and !), key=value or
key:value, escapes (\\uXXXX, \\n, \\t, \\\\) and line continuations. dump()
writes ASCII only (every other character as \\uXXXX), so the bundle reads the
same whatever charset the browser assumes.
"""

import re

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f"}
_SEPARATOR = re.compile(r"(?<!\\)[=:]")


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            following = text[i + 1]
            if following == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", text[i + 2:i + 6]):
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(following, following))
            i += 2
            continue
        out.append(char)
        i += 1
    # Escaped surrogate pairs (emoji) back to one character
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16")


def _logical_lines(text: str):
    """Physical lines joined when they end with an odd number of backslashes."""
    pending = ""
    for raw in text.splitlines():
        line = raw.lstrip() if pending else raw
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2 == 1:
            pending += line[:-1]
            continue
        yield pending + line
        pending = ""
    if pending:
        yield pending


def parse(text: str) -> dict:
    """Texts by key, in file order."""
    entries = {}
    for line in _logical_lines(text.lstrip("﻿")):
        stripped = line.strip()
        if not stripped or stripped[0] in "#!":
            continue
        match = _SEPARATOR.search(stripped)
        key, value = (stripped[:match.start()], stripped[match.end():]) if match else (stripped, "")
        entries[_unescape(key.strip())] = _unescape(value.lstrip())
    return entries


def _escape(text: str, is_key: bool = False) -> str:
    out = []
    for index, char in enumerate(text):
        if char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char in "=:#!" and is_key:
            out.append("\\" + char)
        elif char == " " and (is_key or index == 0):
            out.append("\\ ")
        elif ord(char) > 126 or ord(char) < 32:
            # Characters outside the BMP (emoji) as their UTF-16 surrogate pair
            encoded = char.encode("utf-16-be")
            out.extend(f"\\u{int.from_bytes(encoded[i:i + 2], 'big'):04x}" for i in range(0, len(encoded), 2))
        else:
            out.append(char)
    return "".join(out)


def dump(entries: dict, header: str = "") -> str:
    lines = [f"# {line}" for line in header.splitlines()] if header else []
    lines += [f"{_escape(key, is_key=True)}={_escape(value)}" for key, value in entries.items()]
    return "\n".join(lines) + "\n"
