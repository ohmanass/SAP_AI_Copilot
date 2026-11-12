
from app.rag.retriever import SAPRetriever


# Cette fonction sert d'intermédiaire entre ton agent et ChromaDB. Elle renvoie les extraits et leurs références, sans générer elle-même de réponse
def search_sap_documentation(query: str, n_results: int = 3):
    """
    Search the SAP technical documentation using semantic retrieval.
    """

    retriever = SAPRetriever()

    results = retriever.retrieve(
        query=query,
        n_results=n_results
    )

    if not results:
        return {
            "found": False,
            "message": "No documentation found."
        }

    return {
        "found": True,
        "documents": [
            {
                "source": result["metadata"]["source"],
                "chunk": result["metadata"]["chunk_index"],
                "distance": round(result["distance"], 4),
                "content": result["content"]
            }
            for result in results
        ]
    }