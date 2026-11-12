
from app.rag.vector_store import SAPVectorStore

store = SAPVectorStore()

count = store.collection.count()

print(f"Nombre de documents indexés : {count}")
