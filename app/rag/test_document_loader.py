
from app.rag.document_loader import load_and_split_documents


documents = load_and_split_documents()

print(f"Nombre total de chunks : {len(documents)}")

for document in documents:
    print("\n" + "=" * 60)
    print(f"Source : {document['source']}")
    print(f"Chunk : {document['chunk_index']}")
    print(document["content"])
