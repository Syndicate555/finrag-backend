from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import SectionId, SectionResponse
from app.services.supabase_client import get_sections

router = APIRouter(prefix="/api/documents", tags=["sections"])


@router.get("/{document_id}/sections", response_model=list[SectionResponse])
async def get_document_sections(document_id: str):
    sections = get_sections(document_id)
    if not sections:
        raise HTTPException(status_code=404, detail="No sections found for this document")
    return [
        SectionResponse(
            id=SectionId(s["id"]),
            heading=s["heading"],
            level=s["level"],
            start_page=s["start_page"],
            end_page=s["end_page"],
            parent_section_id=SectionId(s["parent_section_id"]) if s.get("parent_section_id") else None,
        )
        for s in sections
    ]
