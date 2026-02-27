from __future__ import annotations

from dataclasses import dataclass

import tiktoken

from app.config import settings
from app.services.azure_di_parser import (
    ParagraphRole,
    StructuredDocument,
    StructuredParagraph,
    StructuredTable,
)
from app.services.pdf_parser import ParsedDocument, TextBlock


@dataclass
class Chunk:
    text: str
    chunk_index: int
    section_heading: str
    section_level: int
    parent_section: str
    content_type: str
    page_start: int
    page_end: int
    token_count: int
    embedding_text: str = ""


def _count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text))


def _build_embedding_text(text: str, section_heading: str, parent_section: str, content_type: str) -> str:
    if content_type == "table":
        prefix_parts = []
        if parent_section:
            prefix_parts.append(parent_section)
        if section_heading:
            prefix_parts.append(section_heading)
        if prefix_parts:
            return " > ".join(prefix_parts) + "\n\n" + text
    return text


def _split_on_sentences(text: str, max_tokens: int, overlap_tokens: int, encoding: tiktoken.Encoding) -> list[str]:
    sentences = []
    current = ""
    for char in text:
        current += char
        if char in ".!?" and len(current.strip()) > 1:
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())

    chunks = []
    current_chunk: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence_tokens = _count_tokens(sentence, encoding)
        if current_tokens + sentence_tokens > max_tokens and current_chunk:
            chunks.append(" ".join(current_chunk))
            overlap_chunk: list[str] = []
            overlap_count = 0
            for s in reversed(current_chunk):
                t = _count_tokens(s, encoding)
                if overlap_count + t > overlap_tokens:
                    break
                overlap_chunk.insert(0, s)
                overlap_count += t
            current_chunk = overlap_chunk
            current_tokens = overlap_count

        current_chunk.append(sentence)
        current_tokens += sentence_tokens

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def _group_blocks_by_heading(doc: ParsedDocument) -> list[dict]:
    sections = []
    current_section = {
        "heading": "Introduction",
        "level": 1,
        "parent": "",
        "blocks": [],
        "page_start": 1,
        "page_end": 1,
    }
    heading_texts = {h.text for h in doc.headings}
    current_l1_heading = ""

    for block in doc.text_blocks:
        if block.text in heading_texts:
            heading = next(h for h in doc.headings if h.text == block.text)
            if current_section["blocks"]:
                sections.append(current_section)

            parent = current_l1_heading if heading.level == 2 else ""
            if heading.level == 1:
                current_l1_heading = heading.text

            current_section = {
                "heading": heading.text,
                "level": heading.level,
                "parent": parent,
                "blocks": [],
                "page_start": block.page_number,
                "page_end": block.page_number,
            }
        else:
            current_section["blocks"].append(block)
            current_section["page_end"] = block.page_number

    if current_section["blocks"]:
        sections.append(current_section)

    return sections


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    encoding = tiktoken.encoding_for_model("gpt-4o")
    max_tokens = settings.chunk_max_tokens
    overlap_tokens = settings.chunk_overlap_tokens
    chunks: list[Chunk] = []
    chunk_index = 0

    for table in doc.tables:
        chunks.append(Chunk(
            text=table.markdown,
            chunk_index=chunk_index,
            section_heading="Table",
            section_level=2,
            parent_section="",
            content_type="table",
            page_start=table.page_number,
            page_end=table.page_number,
            token_count=_count_tokens(table.markdown, encoding),
            embedding_text=_build_embedding_text(table.markdown, "Table", "", "table"),
        ))
        chunk_index += 1

    sections = _group_blocks_by_heading(doc)

    for section in sections:
        blocks: list[TextBlock] = section["blocks"]
        full_text = " ".join(b.text for b in blocks)
        total_tokens = _count_tokens(full_text, encoding)

        if total_tokens <= max_tokens:
            if full_text.strip():
                chunks.append(Chunk(
                    text=full_text,
                    chunk_index=chunk_index,
                    section_heading=section["heading"],
                    section_level=section["level"],
                    parent_section=section["parent"],
                    content_type="text",
                    page_start=section["page_start"],
                    page_end=section["page_end"],
                    token_count=total_tokens,
                ))
                chunk_index += 1
        else:
            sub_chunks = _split_on_sentences(full_text, max_tokens, overlap_tokens, encoding)
            pages = [b.page_number for b in blocks]
            page_start = min(pages) if pages else section["page_start"]
            page_end = max(pages) if pages else section["page_end"]
            pages_per_chunk = max(1, (page_end - page_start + 1) // max(1, len(sub_chunks)))

            for i, sub_text in enumerate(sub_chunks):
                p_start = page_start + i * pages_per_chunk
                p_end = min(page_end, p_start + pages_per_chunk)
                chunks.append(Chunk(
                    text=sub_text,
                    chunk_index=chunk_index,
                    section_heading=section["heading"],
                    section_level=section["level"],
                    parent_section=section["parent"],
                    content_type="text",
                    page_start=p_start,
                    page_end=p_end,
                    token_count=_count_tokens(sub_text, encoding),
                ))
                chunk_index += 1

    return chunks


def chunk_structured_document(doc: StructuredDocument) -> list[Chunk]:
    encoding = tiktoken.encoding_for_model("gpt-4o")
    max_tokens = settings.chunk_max_tokens
    overlap_tokens = settings.chunk_overlap_tokens
    chunks: list[Chunk] = []
    chunk_index = 0

    current_l1_heading = ""

    for section in doc.sections:
        if section.level == 1:
            current_l1_heading = section.heading
        parent = current_l1_heading if section.level == 2 else ""

        text_parts: list[str] = []
        text_pages: list[int] = []

        for element in section.elements:
            if isinstance(element, StructuredTable):
                if text_parts:
                    chunk_index = _flush_text_parts(
                        text_parts, text_pages, section, parent,
                        max_tokens, overlap_tokens, encoding, chunks, chunk_index,
                    )
                    text_parts = []
                    text_pages = []

                table_text = element.markdown
                if element.caption:
                    table_text = f"{element.caption}\n\n{element.markdown}"
                chunks.append(Chunk(
                    text=table_text,
                    chunk_index=chunk_index,
                    section_heading=section.heading,
                    section_level=section.level,
                    parent_section=parent,
                    content_type="table",
                    page_start=element.page_start,
                    page_end=element.page_end,
                    token_count=_count_tokens(table_text, encoding),
                    embedding_text=_build_embedding_text(table_text, section.heading, parent, "table"),
                ))
                chunk_index += 1

            elif isinstance(element, StructuredParagraph):
                if element.role == ParagraphRole.FOOTNOTE:
                    text_parts.append(f"[Footnote: {element.text}]")
                else:
                    text_parts.append(element.text)
                text_pages.append(element.page_number)

        if text_parts:
            chunk_index = _flush_text_parts(
                text_parts, text_pages, section, parent,
                max_tokens, overlap_tokens, encoding, chunks, chunk_index,
            )

    if doc.key_value_pairs:
        kv_lines = ["Key Information:"]
        kv_pages: list[int] = []
        for kv in doc.key_value_pairs:
            kv_lines.append(f"- {kv.key}: {kv.value}")
            kv_pages.append(kv.page_number)
        kv_text = "\n".join(kv_lines)
        chunks.append(Chunk(
            text=kv_text,
            chunk_index=chunk_index,
            section_heading="Key Information",
            section_level=1,
            parent_section="",
            content_type="text",
            page_start=min(kv_pages),
            page_end=max(kv_pages),
            token_count=_count_tokens(kv_text, encoding),
        ))
        chunk_index += 1

    return chunks


def _flush_text_parts(
    text_parts: list[str],
    text_pages: list[int],
    section,
    parent: str,
    max_tokens: int,
    overlap_tokens: int,
    encoding: tiktoken.Encoding,
    chunks: list[Chunk],
    chunk_index: int,
) -> int:
    full_text = "\n\n".join(text_parts)
    total_tokens = _count_tokens(full_text, encoding)
    page_start = min(text_pages) if text_pages else section.page_start
    page_end = max(text_pages) if text_pages else section.page_end

    if total_tokens <= max_tokens:
        if full_text.strip():
            chunks.append(Chunk(
                text=full_text,
                chunk_index=chunk_index,
                section_heading=section.heading,
                section_level=section.level,
                parent_section=parent,
                content_type="text",
                page_start=page_start,
                page_end=page_end,
                token_count=total_tokens,
            ))
            chunk_index += 1
    else:
        sub_chunks = _split_on_sentences(full_text, max_tokens, overlap_tokens, encoding)
        pages_per_chunk = max(1, (page_end - page_start + 1) // max(1, len(sub_chunks)))
        for i, sub_text in enumerate(sub_chunks):
            p_start = page_start + i * pages_per_chunk
            p_end = min(page_end, p_start + pages_per_chunk)
            chunks.append(Chunk(
                text=sub_text,
                chunk_index=chunk_index,
                section_heading=section.heading,
                section_level=section.level,
                parent_section=parent,
                content_type="text",
                page_start=p_start,
                page_end=p_end,
                token_count=_count_tokens(sub_text, encoding),
            ))
            chunk_index += 1

    return chunk_index
