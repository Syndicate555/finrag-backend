from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from app.config import settings
from app.dependencies import get_openai_client
from app.models.schemas import Citation
from app.prompts.rag import RAG_PROMPT, RAG_PROMPT_WITH_SECTION
from app.prompts.system import SYSTEM_PROMPT
from app.services.embedder import embed_query
from app.services.pinecone_store import query_vectors


async def retrieve_context(
    query: str,
    document_id: str,
    section_filter: str | None = None,
) -> tuple[str, list[Citation]]:
    query_embedding = await embed_query(query)
    results = query_vectors(
        query_embedding=query_embedding,
        document_id=document_id,
        top_k=settings.retrieval_top_k,
        section_filter=section_filter,
    )

    context_parts = []
    citations = []
    seen_pages = set()

    for r in results:
        chunk_text = r.get("chunk_text", "")
        page_start = r.get("page_start", 0)
        page_end = r.get("page_end", 0)
        section = r.get("section_heading", "")
        score = r.get("score", 0.0)

        context_parts.append(
            f"[Section: {section} | Pages {page_start}-{page_end}]\n{chunk_text}"
        )

        page_key = (page_start, page_end, section)
        if page_key not in seen_pages:
            seen_pages.add(page_key)
            citations.append(Citation(
                page_start=page_start,
                page_end=page_end,
                section_heading=section,
                relevance_score=round(score, 3),
                chunk_text=chunk_text[:200],
            ))

    context = "\n\n---\n\n".join(context_parts)
    return context, citations


async def stream_rag_response(
    query: str,
    document_id: str,
    section_filter: str | None = None,
) -> AsyncGenerator[tuple[str, str, list[Citation] | None], None]:
    context, citations = await retrieve_context(query, document_id, section_filter)

    if section_filter:
        user_prompt = RAG_PROMPT_WITH_SECTION.format(
            context=context, query=query, section=section_filter
        )
    else:
        user_prompt = RAG_PROMPT.format(context=context, query=query)

    client = get_openai_client()
    stream = await client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        stream=True,
    )

    yield ("citations", json.dumps([c.model_dump() for c in citations]), None)

    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield ("token", delta.content, None)

    yield ("done", "", citations)


async def stream_general_response(query: str) -> AsyncGenerator[tuple[str, str, None], None]:
    client = get_openai_client()
    stream = await client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        temperature=0.3,
        stream=True,
    )

    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield ("token", delta.content, None)

    yield ("done", "", None)
