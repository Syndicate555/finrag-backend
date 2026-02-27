from __future__ import annotations

import logging
import tempfile

import httpx

from app.config import settings
from app.models.schemas import DocumentStatus
from app.services.chunker import Chunk, chunk_document, chunk_structured_document
from app.services.embedder import embed_texts
from app.services.pdf_parser import parse_pdf
from app.services.pinecone_store import upsert_chunks
from app.services.supabase_client import create_sections, update_document_status

logger = logging.getLogger(__name__)


async def upload_to_supabase_storage(file_bytes: bytes, storage_path: str) -> str:
    bucket = settings.supabase_storage_bucket
    url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{storage_path}"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            content=file_bytes,
            headers={
                "Authorization": f"Bearer {settings.supabase_key}",
                "apikey": settings.supabase_key,
                "Content-Type": "application/pdf",
                "x-upsert": "true",
            },
            timeout=60.0,
        )
        response.raise_for_status()

    return f"{settings.supabase_url}/storage/v1/object/public/{bucket}/{storage_path}"


def _fallback_parse(file_bytes: bytes) -> tuple[list[Chunk], list[dict], int]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        parsed = parse_pdf(tmp.name)

    chunks = chunk_document(parsed)

    section_map: dict[str, dict] = {}
    for heading in parsed.headings:
        if heading.text not in section_map:
            section_map[heading.text] = {
                "heading": heading.text,
                "level": heading.level,
                "start_page": heading.page_number,
                "end_page": heading.page_number,
            }
        else:
            section_map[heading.text]["end_page"] = heading.page_number

    for chunk in chunks:
        key = chunk.section_heading
        if key in section_map:
            section_map[key]["end_page"] = max(
                section_map[key]["end_page"], chunk.page_end
            )

    return chunks, list(section_map.values()), parsed.page_count


def _extract_sections_from_structured(doc) -> list[dict]:
    section_map: dict[str, dict] = {}
    for section in doc.sections:
        if section.heading not in section_map:
            section_map[section.heading] = {
                "heading": section.heading,
                "level": section.level,
                "start_page": section.page_start,
                "end_page": section.page_end,
            }
        else:
            section_map[section.heading]["end_page"] = max(
                section_map[section.heading]["end_page"], section.page_end
            )
    return list(section_map.values())


def _get_pdf_page_count(file_bytes: bytes) -> int:
    import fitz

    with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
        return len(pdf)


def _azure_di_parse(file_bytes: bytes) -> tuple[list[Chunk], list[dict], int]:
    from app.services.azure_di_parser import parse_pdf_with_azure_di

    structured = parse_pdf_with_azure_di(file_bytes)
    chunks = chunk_structured_document(structured)
    sections_list = _extract_sections_from_structured(structured)
    return chunks, sections_list, structured.page_count


async def process_document(document_id: str, file_bytes: bytes, filename: str) -> None:
    try:
        update_document_status(document_id, DocumentStatus.PROCESSING)

        storage_path = f"{document_id}/{filename}"
        blob_url = await upload_to_supabase_storage(file_bytes, storage_path)

        actual_page_count = _get_pdf_page_count(file_bytes)

        chunks: list[Chunk] = []
        sections_list: list[dict] = []
        page_count = actual_page_count

        if settings.azure_di_enabled:
            try:
                chunks, sections_list, _ = _azure_di_parse(file_bytes)
            except Exception as e:
                logger.warning(
                    f"Azure DI failed for document {document_id}, "
                    f"falling back to pdfplumber: {e}"
                )
                chunks, sections_list, _ = _fallback_parse(file_bytes)
        else:
            chunks, sections_list, _ = _fallback_parse(file_bytes)

        update_document_status(
            document_id,
            DocumentStatus.PROCESSING,
            page_count=page_count,
        )

        if not chunks:
            update_document_status(document_id, DocumentStatus.FAILED)
            return

        texts = [c.embedding_text or c.text for c in chunks]
        embeddings = await embed_texts(texts)

        upsert_chunks(document_id, chunks, embeddings)

        if sections_list:
            create_sections(document_id, sections_list)

        update_document_status(
            document_id,
            DocumentStatus.READY,
            page_count=page_count,
            sections=sections_list,
        )

        logger.info(
            f"Document {document_id} processed: {len(chunks)} chunks, "
            f"{len(sections_list)} sections, {page_count} pages"
        )

    except Exception as e:
        logger.exception(f"Failed to process document {document_id}: {e}")
        update_document_status(document_id, DocumentStatus.FAILED)
        raise
