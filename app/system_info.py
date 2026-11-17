"""
Read-only diagnostics for the SAP AI Copilot frontend:
knowledge base inventory and system / AI / vector store status.

Never return secrets: only booleans, counts and non-sensitive names.
"""

import os
import time
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.database.connection import DB_PATH, get_connection
from app.rag import knowledge_store
from app.rag.document_loader import DOCUMENTS_PATH
from app.sap_data import get_data_source
from app.storage import chat_store

load_dotenv()

COLLECTION_NAME = knowledge_store.COLLECTION_NAME

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
    ".docx": "Word document",
    ".xlsx": "Excel workbook",
    ".csv": "CSV"
}


def chat_model_name():
    return os.getenv("GENAI_MODEL_NAME", "gpt-4o-mini")


def embedding_model_name():
    return os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-large")


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _indexed_chunks_by_source():
    """({source: chunk_count}, total) from the vector store."""
    counts = knowledge_store.get_vectors().counts()
    return counts, sum(counts.values())


def get_modules():
    """Module workspaces with live documentation counts and data source."""

    from app.modules import describe_modules

    try:
        indexed, _ = _indexed_chunks_by_source()
        added = {}
        for document in knowledge_store.list_documents():
            if document["origin"] == "uploaded" and document["module"]:
                added.setdefault(document["module"], []).append(document["name"])
    except Exception:
        indexed, added = {}, {}

    source = get_data_source()
    modules = describe_modules(indexed, added)
    for module in modules:
        if module["status"] == "connected":
            # Transactional modules read the configured SAP data source
            if source.key == "odata":
                module["data_source"] = source.label
            elif module["requires_s4hana"]:
                module["status"] = "unavailable"
            else:
                module["data_source"] = source.label
                module["status"] = "demo"
    return modules


def get_knowledge_base(user=None):
    """Documents of the knowledge base, their indexing state and the vector store."""

    vector_store = {
        "available": True,
        "backend": None,
        "collection": COLLECTION_NAME,
        "total_chunks": 0,
        "error": None
    }

    try:
        vectors = knowledge_store.get_vectors()
        vector_store["backend"] = vectors.label
        indexed, total = _indexed_chunks_by_source()
        vector_store["total_chunks"] = total
    except Exception as error:
        indexed = {}
        vector_store["available"] = False
        vector_store["error"] = type(error).__name__

    try:
        registered = {row["name"]: row for row in knowledge_store.list_documents()}
    except Exception:
        registered = {}

    documents = []

    def entry(name, extension, size_bytes, modified_at, row):
        indexed_chunks = indexed.get(name, 0)
        expected = row["chunks"] if row else None
        if indexed_chunks == 0:
            status = "not_indexed"
        elif expected is not None and indexed_chunks != expected:
            status = "outdated"
        else:
            status = "indexed"
        origin = row["origin"] if row else "bundled"
        return {
            "name": name,
            "type": DOCUMENT_TYPES.get(extension, extension.lstrip(".").upper() or "File"),
            "extension": extension,
            "size_bytes": size_bytes,
            "modified_at": modified_at,
            "indexed_chunks": indexed_chunks,
            "expected_chunks": expected,
            "status": status,
            "supported": True,
            "origin": origin,
            "module": row["module"] if row else knowledge_store.bundled_module(name),
            # Authors are never sent to the browser: only whether it is the current user
            "can_delete": bool(row and origin == "uploaded" and user and row["uploaded_by"] == user)
        }

    # Shipped files (also listed before their first indexing)
    if DOCUMENTS_PATH.exists():
        for file_path in sorted(DOCUMENTS_PATH.glob("*.txt")):
            stat = file_path.stat()
            documents.append(entry(file_path.name, ".txt", stat.st_size, _iso(stat.st_mtime),
                                   registered.get(file_path.name)))
    shipped = {d["name"] for d in documents}

    # Documents added from the UI
    for name, row in registered.items():
        if name not in shipped and row["origin"] == "uploaded":
            documents.append(entry(name, Path(name).suffix.lower(), row["size_bytes"], row["created_at"], row))

    known = {d["name"] for d in documents}
    # Chunks still in the vector store whose document is gone
    orphan_sources = [
        {"name": source, "indexed_chunks": count}
        for source, count in sorted(indexed.items())
        if source not in known
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
            "uploaded_documents": sum(1 for d in documents if d["origin"] == "uploaded"),
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
            "backend": knowledge_store.get_vectors().label,
            "collection": COLLECTION_NAME,
            "total_chunks": total,
            "embedding_model": embedding_model_name(),
            "error": None
        }
    except Exception as error:
        return {
            "available": False,
            "backend": None,
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


def get_system_status(check_ai: bool = False, owner: str = "local"):

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
        "sap_database": _sap_database_status(),
        "chat_history": chat_store.describe(owner)
    }
