
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_PATH = PROJECT_ROOT / "data" / "documents"


def load_documents():
    documents = []

    for file_path in DOCUMENTS_PATH.glob("*.txt"):
        content = file_path.read_text(encoding="utf-8")

        documents.append({
            "source": file_path.name,
            "content": content
        })

    return documents



def split_text(text, chunk_size=800, overlap=150):
    paragraphs = [
        paragraph.strip()
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    ]

    chunks = []
    current_chunk = ""

    for paragraph in paragraphs:

        # Si le paragraphe tient dans le chunk actuel
        if len(current_chunk) + len(paragraph) + 2 <= chunk_size:
            current_chunk += ("\n\n" if current_chunk else "") + paragraph

        else:
            # Enregistrer le chunk actuel
            if current_chunk:
                chunks.append(current_chunk)

            # Si le paragraphe est trop long, le découper
            if len(paragraph) > chunk_size:
                start = 0

                while start < len(paragraph):
                    end = start + chunk_size
                    chunk = paragraph[start:end].strip()

                    if chunk:
                        chunks.append(chunk)

                    start += chunk_size - overlap

                current_chunk = ""

            else:
                current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk)

    return chunks



def load_and_split_documents():
    documents = load_documents()
    processed_documents = []

    for document in documents:
        chunks = split_text(document["content"])

        for index, chunk in enumerate(chunks):
            processed_documents.append({
                "id": f'{document["source"]}_{index}',
                "source": document["source"],
                "chunk_index": index,
                "content": chunk
            })

    return processed_documents
