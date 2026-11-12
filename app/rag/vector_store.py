import os
import chromadb
from pathlib import Path
from dotenv import load_dotenv
from gen_ai_hub.proxy.native.openai import embeddings

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"


class SAPVectorStore:

    def __init__(self):
        self.model = os.getenv(
            "EMBEDDING_MODEL_NAME",
            "text-embedding-3-large"
        )

        self.client = chromadb.PersistentClient( 
            path=str(CHROMA_PATH)
        )

        self.collection = self.client.get_or_create_collection(
            name="sap_knowledge"
        )

    def generate_embedding(self, text):
        response = embeddings.create(
            input=text,
            model_name=self.model
        )

        return response.data[0].embedding

    def add_document(self, document_id, text, metadata=None):
        vector = self.generate_embedding(text)

        self.collection.upsert(
            ids=[document_id],
            documents=[text],
            embeddings=[vector],
            metadatas=[metadata or {"source": "unknown"}]
        )

    def search(self, query, n_results=3):
        query_vector = self.generate_embedding(query)

        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=n_results
        )

        return results
