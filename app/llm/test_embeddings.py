
from dotenv import load_dotenv
from gen_ai_hub.proxy.native.openai import embeddings

load_dotenv()

response = embeddings.create(
    input="SAP MM permet de gérer les achats et les commandes fournisseurs.",
    model_name="text-embedding-3-large"
)

vector = response.data[0].embedding

print("Embedding généré avec succès !")
print("Dimensions du vecteur :", len(vector))
print("Premières valeurs :", vector[:5])
