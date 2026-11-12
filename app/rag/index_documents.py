
from app.rag.document_loader import load_and_split_documents
from app.rag.vector_store import SAPVectorStore


def index_documents():
    store = SAPVectorStore()

    documents = load_and_split_documents()

    print(f"Nombre de chunks à indexer : {len(documents)}")

    for document in documents:
        store.add_document(
            document_id=document["id"],
            text=document["content"],
            metadata={
                "source": document["source"],
                "chunk_index": document["chunk_index"]
            }
        )

        print(f"Indexed: {document['id']}")

    print("\nIndexation terminée !")


if __name__ == "__main__":
    index_documents()
