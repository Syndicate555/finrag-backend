from __future__ import annotations

from app.dependencies import get_pinecone_index
from app.services.chunker import Chunk


def upsert_chunks(document_id: str, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
    index = get_pinecone_index()
    batch_size = 100

    vectors = []
    for chunk, embedding in zip(chunks, embeddings):
        vector_id = f"{document_id}#{chunk.chunk_index}"
        metadata = {
            "document_id": document_id,
            "chunk_index": chunk.chunk_index,
            "section_heading": chunk.section_heading,
            "section_level": chunk.section_level,
            "parent_section": chunk.parent_section,
            "content_type": chunk.content_type,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "chunk_text": chunk.text,
            "token_count": chunk.token_count,
        }
        vectors.append({"id": vector_id, "values": embedding, "metadata": metadata})

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i : i + batch_size]
        index.upsert(vectors=batch)


def query_vectors(
    query_embedding: list[float],
    document_id: str,
    top_k: int = 8,
    section_filter: str | None = None,
) -> list[dict]:
    index = get_pinecone_index()

    filter_dict: dict = {"document_id": {"$eq": document_id}}
    if section_filter:
        filter_dict["section_heading"] = {"$eq": section_filter}

    results = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True,
        filter=filter_dict,
    )

    return [
        {
            "id": match["id"],
            "score": match["score"],
            **match["metadata"],
        }
        for match in results["matches"]
    ]


def delete_document_vectors(document_id: str) -> None:
    index = get_pinecone_index()
    index.delete(filter={"document_id": {"$eq": document_id}})
