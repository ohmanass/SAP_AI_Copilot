
from app.rag.vector_store import SAPVectorStore


class SAPRetriever:

    def __init__(self):
        self.store = SAPVectorStore()

    def retrieve(self, query, n_results=3):
        results = self.store.search(
            query=query,
            n_results=n_results
        )

        retrieved_documents = []

        for index in range(len(results["ids"][0])):
            retrieved_documents.append({
                "id": results["ids"][0][index],
                "content": results["documents"][0][index],
                "metadata": results["metadatas"][0][index],
                "distance": results["distances"][0][index]
            })

        return retrieved_documents
