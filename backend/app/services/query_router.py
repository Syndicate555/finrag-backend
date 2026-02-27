from __future__ import annotations

from app.config import settings
from app.dependencies import get_openai_client
from app.models.schemas import QueryRoute
from app.prompts.query_router import QUERY_ROUTER_PROMPT


async def classify_query(query: str) -> QueryRoute:
    client = get_openai_client()
    prompt = QUERY_ROUTER_PROMPT.format(query=query)

    response = await client.chat.completions.create(
        model=settings.openai_router_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=20,
    )

    result = response.choices[0].message.content.strip().lower()

    if "needs_clarification" in result:
        return QueryRoute.NEEDS_CLARIFICATION
    if "general" in result:
        return QueryRoute.GENERAL
    return QueryRoute.KB
