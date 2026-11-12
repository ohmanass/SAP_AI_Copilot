
from app.rag.vector_store import SAPVectorStore

store = SAPVectorStore()

store.add_document(
    "sap_mm_001",
    "SAP EKKO stores purchase order header information.",
    {"source": "SAP MM"}
)

store.add_document(
    "sap_mm_002",
    "SAP EKPO stores purchase order item information.",
    {"source": "SAP MM"}
)

store.add_document(
    "sap_abap_001",
    "ABAP CDS Views allow developers to define semantic data models.",
    {"source": "SAP ABAP"}
)

results = store.search(
    "Which SAP table contains purchase order items?",
    n_results=2
)

for document, distance in zip(
    results["documents"][0],
    results["distances"][0]
):
    print(f"Document: {document}")
    print(f"Distance: {distance}")
    print("-" * 40)

