"""
Index the documents shipped in data/documents (python -m app.rag.index_documents).

New or changed files are embedded, unchanged ones are skipped. The backend also
runs this synchronisation at startup, so running it by hand is optional.
"""

from app.rag.knowledge_store import sync_bundled
from app.storage import chat_store


def index_documents():
    chat_store.initialize()
    actions = sync_bundled()
    for name, action in actions.items():
        print(f"{action:>9}  {name}")
    print("\nIndexation terminée !")


if __name__ == "__main__":
    index_documents()
