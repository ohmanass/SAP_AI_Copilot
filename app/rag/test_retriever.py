
from app.rag.retriever import SAPRetriever


def main():
    retriever = SAPRetriever()

    question = "Which SAP table stores purchase order item information?"

    results = retriever.retrieve(question, n_results=3)

    print(f"Question: {question}")
    print(f"Documents retrieved: {len(results)}")

    for result in results:
        print("\n" + "=" * 60)
        print(f"ID: {result['id']}")
        print(f"Source: {result['metadata']['source']}")
        print(f"Chunk: {result['metadata']['chunk_index']}")
        print(f"Distance: {result['distance']:.4f}")
        print(f"Content:\n{result['content']}")


if __name__ == "__main__":
    main()
