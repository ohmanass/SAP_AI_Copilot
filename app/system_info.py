"""
Read-only diagnostics for the SAP AI Copilot frontend:
knowledge base inventory and system / AI / vector store status.

Never return secrets: only booleans, counts and non-sensitive names.
"""

import os
import time
from datetime import datetime, timezone

import chromadb
from dotenv import load_dotenv

from app.database.connection import DB_PATH, get_connection
from app.rag.document_loader import DOCUMENTS_PATH, split_text
from app.rag.vector_store import CHROMA_PATH
from app.sap_data import get_data_source

load_dotenv()

COLLECTION_NAME = "sap_knowledge"

REQUIRED_AI_CORE_VARIABLES = (
    "AICORE_CLIENT_ID",
    "AICORE_CLIENT_SECRET",
    "AICORE_AUTH_URL",
    "AICORE_BASE_URL",
    "AICORE_RESOURCE_GROUP"
)

DOCUMENT_TYPES = {
    ".txt": "Plain text",
    ".md": "Markdown",
    ".pdf": "PDF",
    ".docx": "Word document"
}


def chat_model_name():
    return os.getenv("GENAI_MODEL_NAME", "gpt-4o-mini")


def embedding_model_name():
    return os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-large")


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _indexed_chunks_by_source():
    """Return ({source: chunk_count}, total) from ChromaDB without creating anything."""

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_collection(COLLECTION_NAME)

    metadatas = collection.get(include=["metadatas"])["metadatas"] or []

    counts = {}
    for metadata in metadatas:
        source = (metadata or {}).get("source", "unknown")
        counts[source] = counts.get(source, 0) + 1

    return counts, collection.count()


def get_knowledge_base():

    vector_store = {
        "available": True,
        "collection": COLLECTION_NAME,
        "total_chunks": 0,
        "error": None
    }

    try:
        indexed, total = _indexed_chunks_by_source()
        vector_store["total_chunks"] = total
    except Exception as error:
        indexed = {}
        vector_store["available"] = False
        vector_store["error"] = type(error).__name__

    documents = []
    seen_sources = set()

    if DOCUMENTS_PATH.exists():
        for file_path in sorted(DOCUMENTS_PATH.iterdir()):

            if not file_path.is_file():
                continue

            stat = file_path.stat()
            extension = file_path.suffix.lower()
            indexed_chunks = indexed.get(file_path.name, 0)

            # Expected chunk count with the same splitter as the indexer
            expected_chunks = None
            if extension == ".txt":
                expected_chunks = len(
                    split_text(file_path.read_text(encoding="utf-8"))
                )

            if indexed_chunks == 0:
                status = "not_indexed"
            elif expected_chunks is not None and indexed_chunks != expected_chunks:
                status = "outdated"
            else:
                status = "indexed"

            seen_sources.add(file_path.name)
            documents.append({
                "name": file_path.name,
                "type": DOCUMENT_TYPES.get(extension, extension.lstrip(".").upper() or "File"),
                "extension": extension,
                "size_bytes": stat.st_size,
                "modified_at": _iso(stat.st_mtime),
                "indexed_chunks": indexed_chunks,
                "expected_chunks": expected_chunks,
                "status": status,
                "supported": extension == ".txt"
            })

    # Chunks still present in ChromaDB whose source file no longer exists
    orphan_sources = [
        {"name": source, "indexed_chunks": count}
        for source, count in sorted(indexed.items())
        if source not in seen_sources
    ]

    return {
        "documents": documents,
        "orphan_sources": orphan_sources,
        "vector_store": vector_store,
        "embedding_model": embedding_model_name(),
        "documents_path": "data/documents",
        "summary": {
            "document_count": len(documents),
            "indexed_documents": sum(1 for d in documents if d["status"] == "indexed"),
            "total_chunks": vector_store["total_chunks"]
        }
    }


def _check_ai_connection():
    """Send a minimal completion to SAP Generative AI Hub and time it."""

    from gen_ai_hub.proxy.native.openai import chat

    started = time.perf_counter()

    try:
        chat.completions.create(
            model_name=chat_model_name(),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0
        )
        return {
            "reachable": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": None
        }
    except Exception as error:
        # Exception type only: messages may contain URLs or tenant details
        return {
            "reachable": False,
            "latency_ms": None,
            "error": type(error).__name__
        }


def _vector_store_status():
    try:
        _, total = _indexed_chunks_by_source()
        return {
            "available": True,
            "collection": COLLECTION_NAME,
            "total_chunks": total,
            "embedding_model": embedding_model_name(),
            "error": None
        }
    except Exception as error:
        return {
            "available": False,
            "collection": COLLECTION_NAME,
            "total_chunks": 0,
            "embedding_model": embedding_model_name(),
            "error": type(error).__name__
        }


def _sap_database_status():
    if not DB_PATH.exists():
        return {"available": False, "tables": {}, "error": "DatabaseNotFound"}

    connection = get_connection()
    try:
        tables = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("suppliers", "purchase_orders", "purchase_order_items")
        }
        return {"available": True, "tables": tables, "error": None}
    except Exception as error:
        return {"available": False, "tables": {}, "error": type(error).__name__}
    finally:
        connection.close()


def get_system_status(check_ai: bool = False):

    configured = all(os.getenv(name) for name in REQUIRED_AI_CORE_VARIABLES)

    ai = {
        "provider": "SAP Generative AI Hub",
        "configured": configured,
        "chat_model": chat_model_name(),
        "embedding_model": embedding_model_name(),
        "connection": None
    }

    if check_ai and configured:
        ai["connection"] = _check_ai_connection()

    return {
        "backend": {
            "status": "online",
            "application": "SAP AI Copilot",
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds")
        },
        "ai": ai,
        "vector_store": _vector_store_status(),
        "sap_data": get_data_source().status(),
        "sap_database": _sap_database_status()
    }
