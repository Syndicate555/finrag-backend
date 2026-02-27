from __future__ import annotations

from app.config import settings
from app.dependencies import get_openai_client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    client = get_openai_client()
    all_embeddings: list[list[float]] = []
    batch_size = 100

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=batch,
            dimensions=settings.embedding_dimensions,
        )
        all_embeddings.extend([item.embedding for item in response.data])

    return all_embeddings


async def embed_query(query: str) -> list[float]:
    result = await embed_texts([query])
    return result[0]
