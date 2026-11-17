"""
Text extraction from documents attached to a conversation.

Only text is extracted: no macro, script or embedded object is ever executed.
Supported:
  text      .txt .md .csv .tsv .json .xml .log .sql .abap .yaml .yml .html .htm
  PDF       .pdf  (pypdf text layer; scanned pages are transcribed by the AI model, see vision.py)
  Word      .docx (Office Open XML, read with the standard library)
  Excel     .xlsx (Office Open XML, read with the standard library; one block per sheet)
  images    .png .jpg .jpeg .webp .gif .bmp .tif .tiff (transcribed by the AI model, see vision.py)
"""

import io
import re
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

from app.attachments import vision

MAX_FILE_BYTES = 10 * 1024 * 1024        # 10 MB per file
MAX_TEXT_CHARS = 400_000                 # text kept per document
MAX_XLSX_ROWS_PER_SHEET = 5_000
MAX_UNZIPPED_BYTES = 150 * 1024 * 1024   # Office files are zip archives: guard against zip bombs

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log", ".sql", ".abap",
    ".yaml", ".yml", ".html", ".htm"
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx"} | vision.IMAGE_EXTENSIONS

# Marks the text read from an image by the AI model (the agent and the user see it)
TRANSCRIBED = "transcribed by the AI model"
IMAGE_HEADER = f"[Image {TRANSCRIBED}: check key values against the original]"

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class ExtractionError(Exception):
    """Message safe to show to the user."""


def extension(filename: str) -> str:
    return PurePath(filename or "").suffix.lower()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _pdf(data: bytes):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Empty owner password is common for "protected" but readable PDFs
            if not reader.decrypt(""):
                raise ExtractionError("The PDF is password-protected.")
        texts = [(page.extract_text() or "").strip() for page in reader.pages]
        # Pages without text layer: scanned pages, read from their image
        scans = {}
        for index, text in enumerate(texts):
            if not text and len(scans) < vision.MAX_SCANNED_PAGES:
                image = _page_image(reader.pages[index])
                if image:
                    scans[index] = image
    except ExtractionError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as error:
        raise ExtractionError("The PDF could not be read.") from error

    if scans:
        for index, transcription in zip(scans, _transcribe(list(scans.values()))):
            if transcription:
                texts[index] = f"[{TRANSCRIBED} from the scanned page]\n{transcription}"
    skipped = sum(1 for text in texts if not text) if len(scans) == vision.MAX_SCANNED_PAGES else 0

    pages = [f"[Page {number}]\n{text}" for number, text in enumerate(texts, start=1)]
    if skipped:
        pages.append(f"[{skipped} more page(s) without text were not read: "
                     f"at most {vision.MAX_SCANNED_PAGES} scanned pages are read per PDF]")
    if not any(texts):
        raise ExtractionError("The PDF contains no text and no readable scanned page.")
    return "\n\n".join(pages), len(texts)


def _page_image(page):
    """Largest image of a PDF page (the scan), as bytes, or None."""

    best = None
    try:
        for image in page.images:
            data = image.data
            if best is None or len(data) > len(best):
                best = data
    except Exception:       # unsupported image encoding: the page stays without text
        return None
    return best


def _transcribe(images):
    try:
        return vision.transcribe(images)
    except vision.VisionError as error:
        raise ExtractionError(str(error)) from error


def _image(data: bytes):
    transcription = _transcribe([data])[0]
    if not transcription:
        raise ExtractionError("The image is too small to contain readable content.")
    return f"{IMAGE_HEADER}\n{transcription}", None


def transcribed_parts(text: str) -> int:
    """Number of images or scanned pages of a document read by the AI model."""

    return (text or "").count(TRANSCRIBED)


def _open_office(data: bytes):
    archive = zipfile.ZipFile(io.BytesIO(data))
    if sum(info.file_size for info in archive.infolist()) > MAX_UNZIPPED_BYTES:
        archive.close()
        raise ExtractionError("The document is too large once uncompressed.")
    return archive


def _xml(archive, name):
    try:
        return ElementTree.fromstring(archive.read(name))
    except KeyError:
        return None


def _docx(data: bytes):
    with _open_office(data) as archive:
        root = _xml(archive, "word/document.xml")
    if root is None:
        raise ExtractionError("The Word document could not be read.")

    lines = []
    body = root.find(f"{WORD_NS}body")
    for block in (body if body is not None else []):
        if block.tag == f"{WORD_NS}p":
            lines.append("".join(t.text or "" for t in block.iter(f"{WORD_NS}t")))
        elif block.tag == f"{WORD_NS}tbl":
            for row in block.iter(f"{WORD_NS}tr"):
                cells = ["".join(t.text or "" for t in cell.iter(f"{WORD_NS}t")).strip()
                         for cell in row.findall(f"{WORD_NS}tc")]
                lines.append(" | ".join(cells))
    return "\n".join(lines).strip(), None


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    index = 0
    for char in (letters.group(0) if letters else "A"):
        index = index * 26 + ord(char) - 64
    return index - 1


def _xlsx(data: bytes):
    with _open_office(data) as archive:
        shared = []
        strings = _xml(archive, "xl/sharedStrings.xml")
        if strings is not None:
            shared = ["".join(t.text or "" for t in item.iter(f"{SHEET_NS}t"))
                      for item in strings.findall(f"{SHEET_NS}si")]

        workbook = _xml(archive, "xl/workbook.xml")
        relations = _xml(archive, "xl/_rels/workbook.xml.rels")
        if workbook is None or relations is None:
            raise ExtractionError("The Excel workbook could not be read.")
        targets = {rel.get("Id"): rel.get("Target") for rel in relations.findall(f"{PKG_REL_NS}Relationship")}

        blocks = []
        sheets = workbook.find(f"{SHEET_NS}sheets")
        for sheet in (sheets if sheets is not None else []):
            target = targets.get(sheet.get(f"{REL_NS}id"), "")
            path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            root = _xml(archive, path)
            if root is None:
                continue
            lines = []
            for row_number, row in enumerate(root.iter(f"{SHEET_NS}row")):
                if row_number >= MAX_XLSX_ROWS_PER_SHEET:
                    lines.append(f"[... rows after {MAX_XLSX_ROWS_PER_SHEET} not included]")
                    break
                values = {}
                for cell in row.findall(f"{SHEET_NS}c"):
                    kind = cell.get("t")
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{SHEET_NS}t"))
                    else:
                        raw = cell.find(f"{SHEET_NS}v")
                        value = raw.text if raw is not None and raw.text is not None else ""
                        if kind == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                    values[_column_index(cell.get("r"))] = value
                if any(v.strip() for v in values.values()):
                    width = max(values) + 1
                    lines.append("\t".join(values.get(i, "") for i in range(width)))
            blocks.append(f"[Sheet {sheet.get('name')}]\n" + "\n".join(lines))
    return "\n\n".join(blocks).strip(), None


def extract_text(filename: str, data: bytes):
    """
    Text of a document: (text, pages or None, truncated).
    Raises ExtractionError with a user-safe message.
    """

    ext = extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(
            f"Unsupported file type '{ext or 'none'}'. "
            "Supported: PDF, Word (.docx), Excel (.xlsx), images and text files."
        )
    if not data:
        raise ExtractionError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ExtractionError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")

    try:
        if ext == ".pdf":
            text, pages = _pdf(data)
        elif ext == ".docx":
            text, pages = _docx(data)
        elif ext == ".xlsx":
            text, pages = _xlsx(data)
        elif ext in vision.IMAGE_EXTENSIONS:
            text, pages = _image(data)
        else:
            text, pages = _decode(data), None
    except zipfile.BadZipFile as error:
        raise ExtractionError("The file is damaged or is not a real Office document.") from error
    except ElementTree.ParseError as error:
        raise ExtractionError("The document structure could not be read.") from error

    text = text.replace("\x00", "").strip()
    if not text:
        raise ExtractionError("No text could be extracted from the file.")

    truncated = len(text) > MAX_TEXT_CHARS
    return text[:MAX_TEXT_CHARS], pages, truncated
