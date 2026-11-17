"""
The SAP BTP build does not install ChromaDB (deploy/deploy.ps1, $LocalOnly):
the backend must start and use the SAP HANA Cloud Vector Engine without it.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent("""
    import json, os, sys

    # As on SAP BTP: ChromaDB is not installed, a HANA schema is bound
    sys.modules["chromadb"] = None
    os.environ["KB_SYNC_ON_STARTUP"] = "0"
    os.environ["VCAP_SERVICES"] = json.dumps({"hana": [{"name": "sap-ai-copilot-hana", "credentials": {
        "host": "example.invalid", "port": "443", "user": "u", "password": "p", "schema": "S"}}]})

    from app import api                                   # API, agent, tools
    from app.rag import knowledge_store
    from app.storage import database

    assert isinstance(database.get_database(), database.HanaDatabase)

    # No network: skip the table creation, the vector store only needs the database object
    knowledge_store.set_vectors(None)
    import app.storage.chat_store as chat_store
    chat_store.database = lambda: database.get_database()
    vectors = knowledge_store.get_vectors()
    assert isinstance(vectors, knowledge_store.HanaVectors), vectors
    print("ok")
""")


def test_backend_starts_without_chromadb():
    result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


def test_chromadb_is_not_deployed():
    script = (ROOT / "deploy" / "deploy.ps1").read_text(encoding="utf-8")
    assert '$LocalOnly = "pytest", "chromadb"' in script
    assert "chroma_db" not in script.split("# --- Backend: requirements")[0].split("# 1. Build")[1]
