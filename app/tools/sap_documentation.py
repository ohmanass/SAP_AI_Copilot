from app.rag import knowledge_store


def search_sap_documentation(query: str, n_results: int = 3, sources=None):
    """
    Search the SAP knowledge base (semantic retrieval). Returns the excerpts and
    their references; the agent writes the answer.

    `sources` (set by the agent for module workspaces, never by the LLM)
    restricts the search to the given documents.
    """

    results = knowledge_store.search(query, n_results=n_results, sources=sources)

    if not results:
        return {
            "found": False,
            "message": "No documentation found."
        }

    return {
        "found": True,
        "documents": [
            {
                "source": result["source"],
                "chunk": result["chunk"],
                "distance": result["distance"],
                "content": result["content"]
            }
            for result in results
        ]
    }
