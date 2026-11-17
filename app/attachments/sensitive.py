"""
Masking of sensitive data in documents, before they are stored or sent to the model.

Credentials (passwords, secrets, API keys, tokens, private keys) and payment
card numbers are always masked. IBANs are masked in the shared knowledge base
only: in a private conversation they are useful (checking the bank details of
an invoice). The original values are never stored anywhere.

Detection, kept conservative so that SAP documents are not damaged:
  - tabular files (CSV, TSV, Excel): every value of a column whose header names
    a credential ("PASSWORD", "Mot de passe", "api_key"...)
  - "password: value" / "api_key=value" pairs (the value must look like a secret)
  - PEM private keys and JSON Web Tokens
  - card numbers: 13 to 19 digits, plausible first digit, valid Luhn checksum
  - IBANs: valid ISO 13616 checksum (mod 97)

mask() returns the masked text and {kind: count}.
"""

import re
from pathlib import PurePath

PRIVATE = "private"     # document attached to a conversation
SHARED = "shared"       # knowledge base, readable by every user

# Column headers / keys naming a credential (compared without spaces, "_", "-", ".")
CREDENTIAL_NAMES = {
    "password", "passwort", "passwd", "pwd", "pass", "motdepasse", "mdp", "kennwort",
    "secret", "clientsecret", "apikey", "apisecret", "apitoken", "accesstoken", "refreshtoken",
    "token", "authtoken", "bearertoken", "privatekey", "secretkey", "pin", "pincode",
}
CARD_NAMES = {"cardnumber", "creditcard", "creditcardnumber", "cardno", "pan", "cvv", "cvc", "cvv2"}
IBAN_NAMES = {"iban"}

TABULAR_EXTENSIONS = {".csv", ".tsv", ".xlsx"}
DELIMITERS = ("\t", ";", ",", "|")

KEY_VALUE = re.compile(
    r"(?i)\b(password|passwort|passwd|pwd|mot de passe|mdp|kennwort|secret|client[ _-]?secret|"
    r"api[ _-]?key|api[ _-]?secret|access[ _-]?token|refresh[ _-]?token|auth[ _-]?token|bearer)"
    r"(\s*(?:[:=]|\bis\b|\best\b)\s*)([\"']?)([^\s\"',;]+)\3"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b")


def _key(name):
    return re.sub(r"[\s_.\-]", "", str(name or "")).lower()


def _looks_secret(value):
    """A password-like value, not an ordinary word ("Password: must be 8 characters")."""
    if len(value) < 4:
        return False
    return bool(re.search(r"[\d\W_]", value)) or len(value) >= 12


def _luhn(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _iban_valid(compact):
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


class _Masker:

    def __init__(self, scope):
        self.scope = scope
        self.found = {}

    def hit(self, kind, label):
        self.found[kind] = self.found.get(kind, 0) + 1
        return f"[masked {label}]"

    # ---- tabular files: whole columns
    def columns(self, text):
        blocks = []
        for block in text.split("\n\n"):
            lines = block.split("\n")
            start = 1 if lines and lines[0].startswith("[Sheet ") else 0
            if len(lines) <= start + 1:
                blocks.append(block)
                continue
            header = lines[start]
            delimiter = max(DELIMITERS, key=header.count)
            if not header.count(delimiter):
                blocks.append(block)
                continue
            names = [_key(cell) for cell in header.split(delimiter)]
            kinds = {}
            for index, name in enumerate(names):
                if name in CREDENTIAL_NAMES:
                    kinds[index] = ("credential", "password" if "pass" in name or name in ("pwd", "mdp") else "secret")
                elif name in CARD_NAMES:
                    kinds[index] = ("card", "card data")
                elif name in IBAN_NAMES and self.scope == SHARED:
                    kinds[index] = ("iban", "IBAN")
            if kinds:
                for row in range(start + 1, len(lines)):
                    cells = lines[row].split(delimiter)
                    for index, (kind, label) in kinds.items():
                        if index < len(cells) and cells[index].strip():
                            cells[index] = self.hit(kind, label)
                    lines[row] = delimiter.join(cells)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    # ---- free text
    def patterns(self, text):
        text = PRIVATE_KEY.sub(lambda m: self.hit("credential", "private key"), text)
        text = JWT.sub(lambda m: self.hit("credential", "token"), text)

        def key_value(match):
            value = match.group(4)
            # Sentence punctuation after the value is not part of it
            trailing = re.search(r"[.)\]]+$", value)
            ending = trailing.group(0) if trailing and len(value) > len(trailing.group(0)) else ""
            value = value[:len(value) - len(ending)]
            if value.startswith("[masked") or not _looks_secret(value):
                return match.group(0)
            label = "password" if re.match(r"(?i)pass|pwd|mot|mdp|kenn", match.group(1)) else "secret"
            return match.group(1) + match.group(2) + self.hit("credential", label) + ending
        text = KEY_VALUE.sub(key_value, text)

        def card(match):
            digits = re.sub(r"\D", "", match.group(0))
            if 13 <= len(digits) <= 19 and digits[0] in "23456" and _luhn(digits):
                return self.hit("card", f"card number …{digits[-4:]}")
            return match.group(0)
        text = CARD.sub(card, text)

        if self.scope == SHARED:
            def iban(match):
                compact = match.group(1).replace(" ", "")
                if _iban_valid(compact):
                    return self.hit("iban", f"IBAN {compact[:2]}…{compact[-4:]}")
                return match.group(0)
            text = IBAN.sub(iban, text)
        return text


def mask(text, filename="", scope=PRIVATE):
    """(masked text, {kind: count}) for a document of this scope."""
    masker = _Masker(scope)
    if PurePath(filename or "").suffix.lower() in TABULAR_EXTENSIONS:
        text = masker.columns(text)
    text = masker.patterns(text)
    return text, masker.found
