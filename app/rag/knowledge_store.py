"""
Knowledge base of the RAG: documents, chunks and embeddings.

  Registry   one row per document (name, module, origin, author, content hash, text),
             in the database of the history (SQLite locally, SAP HANA Cloud on BTP)
  Vectors    SAP HANA Cloud Vector Engine (REAL_VECTOR, cosine similarity) when a
             HANA schema is bound, ChromaDB locally
  Embeddings SAP Generative AI Hub (EMBEDDING_MODEL_NAME, text-embedding-3-large)

Two kinds of documents:
  bundled   data/documents/*.txt, shipped with the app and synchronised at startup
            (embedded again only when their content changed)
  uploaded  added from the UI (Knowledge Base page or an attached document), for one
            module or for every workspace; only their author can remove them

Uploaded documents are reference material for the agent, never instructions.
"""

import hashlib
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.rag.document_loader import DOCUMENTS_PATH, split_text

logger = logging.getLogger("sap_ai_copilot.knowledge")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"
COLLECTION_NAME = "sap_knowledge"
EMBEDDING_BATCH = 64
MAX_KB_CHARS = 400_000

_sync_lock = threading.Lock()


class KnowledgeError(Exception):
    """User-safe error with an HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def embedding_model():
    return os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-large")


def embed(texts):
    """Embeddings of a list of texts (batched calls to SAP Generative AI Hub)."""
    from gen_ai_hub.proxy.native.openai import embeddings

    vectors = []
    for start in range(0, len(texts), EMBEDDING_BATCH):
        response = embeddings.create(input=texts[start:start + EMBEDDING_BATCH], model_name=embedding_model())
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
    return vectors


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


TABULAR_EXTENSIONS = {".csv", ".tsv", ".xlsx"}


def split_rows(text, chunk_size=800):
    """
    Tabular text (CSV, TSV, Excel sheets) split by rows, the header repeated in
    every chunk: each chunk stays readable on its own ("VALID_UNTIL" above its values).
    """

    chunks = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        # Excel: "[Sheet name]" then the header row
        header_size = 2 if lines[0].startswith("[Sheet ") and len(lines) > 1 else 1
        header, rows = "\n".join(lines[:header_size]), lines[header_size:]
        if not rows:
            chunks.append(header)
            continue
        current = []
        for row in rows:
            if current and len(header) + sum(len(r) + 1 for r in current) + len(row) + 1 > chunk_size:
                chunks.append(header + "\n" + "\n".join(current))
                current = []
            current.append(row)
        chunks.append(header + "\n" + "\n".join(current))
    return chunks


def split_document(name, text):
    if Path(name).suffix.lower() in TABULAR_EXTENSIONS:
        return split_rows(text)
    return split_text(text)


# ----------------------------------------------------------------------
# Vector stores
# ----------------------------------------------------------------------

class ChromaVectors:
    """Local vector store (data/chroma_db)."""

    label = "ChromaDB"

    def __init__(self, path=None):
        import chromadb

        self._client = chromadb.PersistentClient(path=str(path or CHROMA_PATH))
        self._collection = self._client.get_or_create_collection(COLLECTION_NAME)

    def replace(self, source, chunks, vectors):
        self.delete(source)
        if chunks:
            self._collection.upsert(
                ids=[f"{source}_{index}" for index in range(len(chunks))],
                documents=chunks,
                embeddings=vectors,
                metadatas=[{"source": source, "chunk_index": index} for index in range(len(chunks))]
            )

    def delete(self, source):
        self._collection.delete(where={"source": source})

    def search(self, vector, n_results, sources=None):
        if sources is not None and not sources:
            return []
        parameters = {"query_embeddings": [vector], "n_results": n_results}
        if sources:
            parameters["where"] = {"source": {"$in": list(sources)}}
        result = self._collection.query(**parameters)
        return [
            {
                "source": metadata.get("source"),
                "chunk": metadata.get("chunk_index"),
                "distance": round(distance, 4),
                "content": content
            }
            for content, metadata, distance in zip(
                result["documents"][0], result["metadatas"][0], result["distances"][0]
            )
        ]

    def counts(self):
        counts = {}
        for metadata in self._collection.get(include=["metadatas"])["metadatas"] or []:
            source = (metadata or {}).get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts


class HanaVectors:
    """SAP HANA Cloud Vector Engine (table kb_chunks of the bound HANA schema)."""

    label = "SAP HANA Cloud Vector Engine"

    def __init__(self, database):
        self._database = database

    @staticmethod
    def _vector(values):
        # TO_REAL_VECTOR accepts the "[x, y, ...]" text form
        return "[" + ",".join(repr(float(value)) for value in values) + "]"

    def replace(self, source, chunks, vectors):
        with self._database.cursor() as cursor:
            cursor.execute("DELETE FROM kb_chunks WHERE source = ?", (source,))
            if chunks:
                cursor.executemany(
                    "INSERT INTO kb_chunks (chunk_id, source, chunk_index, content, embedding)"
                    " VALUES (?, ?, ?, ?, TO_REAL_VECTOR(?))",
                    [(f"{source}_{index}", source, index, chunk, self._vector(vector))
                     for index, (chunk, vector) in enumerate(zip(chunks, vectors))]
                )

    def delete(self, source):
        with self._database.cursor() as cursor:
            cursor.execute("DELETE FROM kb_chunks WHERE source = ?", (source,))

    def search(self, vector, n_results, sources=None):
        if sources is not None and not sources:
            return []
        n_results = max(1, min(int(n_results), 20))
        where, params = "", [self._vector(vector)]
        if sources:
            where = " WHERE source IN (" + ", ".join("?" for _ in sources) + ")"
            params.extend(sources)
        with self._database.cursor() as cursor:
            rows = cursor.execute(
                f"SELECT TOP {n_results} source, chunk_index, content,"
                " COSINE_SIMILARITY(embedding, TO_REAL_VECTOR(?)) AS similarity"
                f" FROM kb_chunks{where} ORDER BY similarity DESC",
                params
            ).fetchall()
        return [
            {
                "source": row["source"],
                "chunk": row["chunk_index"],
                # Same meaning as a distance: lower is closer
                "distance": round(1 - float(row["similarity"]), 4),
                "content": row["content"]
            }
            for row in rows
        ]

    def counts(self):
        with self._database.cursor() as cursor:
            rows = cursor.execute("SELECT source, COUNT(*) AS chunks FROM kb_chunks GROUP BY source").fetchall()
        return {row["source"]: int(row["chunks"]) for row in rows}


def _registry():
    from app.storage import chat_store
    return chat_store.database()


_vectors = None
_vectors_lock = threading.Lock()


def get_vectors():
    global _vectors
    with _vectors_lock:
        if _vectors is None:
            from app.storage.database import HanaDatabase

            database = _registry()
            _vectors = HanaVectors(database) if isinstance(database, HanaDatabase) else ChromaVectors()
        return _vectors


def set_vectors(vectors):
    """Replace the vector store (tests)."""
    global _vectors
    with _vectors_lock:
        _vectors = vectors


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

SUMMARY_COLUMNS = "name, module, origin, size_bytes, chars, chunks, content_hash, uploaded_by, created_at"


def list_documents():
    with _registry().cursor() as cursor:
        return cursor.execute(f"SELECT {SUMMARY_COLUMNS} FROM kb_documents ORDER BY name").fetchall()


def get_document(name):
    with _registry().cursor() as cursor:
        return cursor.execute(f"SELECT {SUMMARY_COLUMNS} FROM kb_documents WHERE name = ?", (name,)).fetchone()


def sources_for_module(module_key):
    """Documents searched in a module workspace: its own and those added for every workspace."""
    with _registry().cursor() as cursor:
        rows = cursor.execute(
            "SELECT name FROM kb_documents WHERE module = ? OR (module IS NULL AND origin = 'uploaded')"
            " ORDER BY name",
            (module_key,)
        ).fetchall()
    return [row["name"] for row in rows]


def _index(name, text, module, origin, uploaded_by, size_bytes):
    """Chunks, embeds and stores a document (vectors first: a failed embedding leaves nothing behind)."""

    chunks = split_document(name, text)
    if not chunks:
        raise KnowledgeError("The document contains no text to index.", 422)
    vectors = embed(chunks)
    get_vectors().replace(name, chunks, vectors)

    with _registry().cursor() as cursor:
        cursor.execute("DELETE FROM kb_documents WHERE name = ?", (name,))
        cursor.execute(
            "INSERT INTO kb_documents"
            " (name, module, origin, size_bytes, chars, chunks, content_hash, text, uploaded_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, module, origin, size_bytes, len(text), len(chunks), _hash(text), text, uploaded_by, _now())
        )
    return get_document(name)


def add_document(name, text, module, uploaded_by, size_bytes=None):
    """Add an uploaded document to the knowledge base."""

    text = (text or "").strip()
    if not text:
        raise KnowledgeError("The document contains no text to index.", 422)
    if len(text) > MAX_KB_CHARS:
        raise KnowledgeError(f"The document is longer than {MAX_KB_CHARS:,} characters.".replace(",", " "), 413)
    if get_document(name) is not None:
        raise KnowledgeError(f"A document named '{name}' is already in the knowledge base.", 409)
    if (DOCUMENTS_PATH / name).exists():
        raise KnowledgeError(f"'{name}' is the name of a document shipped with the application.", 409)
    return _index(name, text, module, "uploaded", uploaded_by, size_bytes or len(text.encode("utf-8")))


def delete_document(name, user):
    """Remove an uploaded document (only its author can)."""

    document = get_document(name)
    if document is None:
        raise KnowledgeError("Document not found.", 404)
    if document["origin"] != "uploaded":
        raise KnowledgeError("Documents shipped with the application cannot be removed here.", 403)
    if document["uploaded_by"] != user:
        raise KnowledgeError("Only the person who added this document can remove it.", 403)

    get_vectors().delete(name)
    with _registry().cursor() as cursor:
        cursor.execute("DELETE FROM kb_documents WHERE name = ?", (name,))


def bundled_module(name):
    """Module whose workspace documentation includes a bundled file (None: general only)."""
    from app.modules import MODULES

    for module in MODULES:
        if name in module.doc_sources:
            return module.key
    return None


def sync_bundled():
    """
    Index data/documents/*.txt: new or changed files are embedded, unchanged ones
    are skipped, removed files are dropped. Returns {name: action}.
    """

    with _sync_lock:
        actions = {}
        files = {path.name: path for path in sorted(DOCUMENTS_PATH.glob("*.txt"))}
        counts = get_vectors().counts()
        registered = {row["name"]: row for row in list_documents()}

        for name, path in files.items():
            text = path.read_text(encoding="utf-8")
            row = registered.get(name)
            if row and row["content_hash"] == _hash(text) and counts.get(name) == row["chunks"]:
                actions[name] = "unchanged"
                continue
            _index(name, text, bundled_module(name), "bundled", None, path.stat().st_size)
            actions[name] = "indexed"

        for name, row in registered.items():
            if row["origin"] == "bundled" and name not in files:
                get_vectors().delete(name)
                with _registry().cursor() as cursor:
                    cursor.execute("DELETE FROM kb_documents WHERE name = ?", (name,))
                actions[name] = "removed"
        return actions


def sync_bundled_in_background():
    """Startup: never blocks nor breaks the API (AI or database may be unavailable)."""

    def run():
        try:
            actions = sync_bundled()
            logger.info("Knowledge base synchronised: %s", actions)
        except Exception:
            logger.exception("Knowledge base synchronisation failed")

    threading.Thread(target=run, name="kb-sync", daemon=True).start()


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------

def search(query, n_results=3, sources=None):
    """Closest chunks to a question, optionally limited to some documents."""
    n_results = max(1, min(int(n_results or 3), 10))
    return get_vectors().search(embed([query])[0], n_results, sources)
