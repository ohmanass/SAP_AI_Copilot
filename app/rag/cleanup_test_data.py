
from app.rag.vector_store import SAPVectorStore

store = SAPVectorStore()

test_ids = [
    "sap_mm_001",
    "sap_mm_002",
    "sap_abap_001"
]

store.collection.delete(ids=test_ids)

print("Anciens documents de test supprimés.")
print(f"Documents restants : {store.collection.count()}")
