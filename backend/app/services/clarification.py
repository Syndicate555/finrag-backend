from __future__ import annotations

from app.models.schemas import ClarificationChip
from app.services.supabase_client import get_sections


def generate_clarification_chips(document_id: str, query: str) -> list[ClarificationChip]:
    sections = get_sections(document_id)
    if not sections:
        return []

    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored: list[tuple[float, dict]] = []
    for section in sections:
        heading_lower = section["heading"].lower()
        heading_words = set(heading_lower.split())
        overlap = len(query_words & heading_words)
        if overlap > 0 or any(word in heading_lower for word in query_words):
            score = overlap + sum(0.5 for word in query_words if word in heading_lower)
            scored.append((score, section))

    if not scored:
        top_sections = [s for s in sections if s["level"] == 1][:5]
    else:
        scored.sort(key=lambda x: x[0], reverse=True)
        top_sections = [s for _, s in scored[:5]]

    return [
        ClarificationChip(
            section_id=s["id"],
            heading=s["heading"],
            level=s["level"],
            label=s["heading"][:50],
        )
        for s in top_sections
    ]
