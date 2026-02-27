from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.dependencies import get_azure_di_client

logger = logging.getLogger(__name__)


class ParagraphRole(Enum):
    TITLE = "title"
    SECTION_HEADING = "sectionHeading"
    PAGE_HEADER = "pageHeader"
    PAGE_FOOTER = "pageFooter"
    PAGE_NUMBER = "pageNumber"
    FOOTNOTE = "footnote"
    BODY = "body"


FILTERED_ROLES = {ParagraphRole.PAGE_HEADER, ParagraphRole.PAGE_FOOTER, ParagraphRole.PAGE_NUMBER}


@dataclass
class StructuredParagraph:
    text: str
    role: ParagraphRole
    page_number: int


@dataclass
class StructuredTable:
    markdown: str
    page_start: int
    page_end: int
    row_count: int
    column_count: int
    caption: str = ""


@dataclass
class StructuredKeyValuePair:
    key: str
    value: str
    page_number: int


@dataclass
class StructuredSection:
    heading: str
    level: int
    page_start: int
    page_end: int
    elements: list[StructuredParagraph | StructuredTable] = field(default_factory=list)


@dataclass
class StructuredDocument:
    paragraphs: list[StructuredParagraph] = field(default_factory=list)
    tables: list[StructuredTable] = field(default_factory=list)
    key_value_pairs: list[StructuredKeyValuePair] = field(default_factory=list)
    sections: list[StructuredSection] = field(default_factory=list)
    page_count: int = 0


def _map_role(role: str | None) -> ParagraphRole:
    if role is None:
        return ParagraphRole.BODY
    try:
        return ParagraphRole(role)
    except ValueError:
        return ParagraphRole.BODY


def _get_page_number(bounding_regions) -> int:
    if bounding_regions:
        return bounding_regions[0].page_number
    return 1


def _table_to_markdown(table) -> str:
    row_count = table.row_count
    col_count = table.column_count
    if row_count == 0 or col_count == 0:
        return ""

    grid: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]

    for cell in table.cells:
        row_idx = cell.row_index
        col_idx = cell.column_index
        content = (cell.content or "").replace("\n", " ").strip()
        row_span = getattr(cell, "row_span", 1) or 1
        col_span = getattr(cell, "column_span", 1) or 1
        for r in range(row_span):
            for c in range(col_span):
                target_r = row_idx + r
                target_c = col_idx + c
                if target_r < row_count and target_c < col_count:
                    grid[target_r][target_c] = content

    headers = grid[0]
    md = "| " + " | ".join(headers) + " |\n"
    md += "| " + " | ".join("---" for _ in headers) + " |\n"
    for row in grid[1:]:
        md += "| " + " | ".join(row) + " |\n"
    return md.strip()


def _build_sections(
    paragraphs: list[StructuredParagraph],
    tables: list[StructuredTable],
) -> list[StructuredSection]:
    sections: list[StructuredSection] = []
    current_section = StructuredSection(
        heading="Introduction",
        level=1,
        page_start=1,
        page_end=1,
    )
    current_l1_heading = ""

    table_idx = 0
    tables_sorted = sorted(tables, key=lambda t: (t.page_start, t.page_end))

    for para in paragraphs:
        if para.role in FILTERED_ROLES:
            continue

        while table_idx < len(tables_sorted):
            t = tables_sorted[table_idx]
            if t.page_start <= para.page_number:
                current_section.elements.append(t)
                current_section.page_end = max(current_section.page_end, t.page_end)
                table_idx += 1
            else:
                break

        if para.role == ParagraphRole.TITLE:
            if current_section.elements:
                sections.append(current_section)
            current_l1_heading = para.text
            current_section = StructuredSection(
                heading=para.text,
                level=1,
                page_start=para.page_number,
                page_end=para.page_number,
            )
        elif para.role == ParagraphRole.SECTION_HEADING:
            if current_section.elements:
                sections.append(current_section)
            current_section = StructuredSection(
                heading=para.text,
                level=2,
                page_start=para.page_number,
                page_end=para.page_number,
            )
        else:
            current_section.elements.append(para)
            current_section.page_end = max(current_section.page_end, para.page_number)

    while table_idx < len(tables_sorted):
        t = tables_sorted[table_idx]
        current_section.elements.append(t)
        current_section.page_end = max(current_section.page_end, t.page_end)
        table_idx += 1

    if current_section.elements:
        sections.append(current_section)

    return sections


def _get_pdf_page_count(file_bytes: bytes) -> int:
    import fitz

    with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
        return len(pdf)


def parse_pdf_with_azure_di(file_bytes: bytes) -> StructuredDocument:
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

    actual_page_count = _get_pdf_page_count(file_bytes)

    client = get_azure_di_client()

    poller = client.begin_analyze_document(
        "prebuilt-layout",
        AnalyzeDocumentRequest(bytes_source=file_bytes),
        pages=f"1-{actual_page_count}",
        output_content_format="markdown",
    )
    result = poller.result()

    di_page_count = len(result.pages) if result.pages else 0

    doc = StructuredDocument()
    doc.page_count = actual_page_count

    if di_page_count < actual_page_count:
        logger.warning(
            f"Azure DI only processed {di_page_count}/{actual_page_count} pages. "
            f"This typically indicates an F0 (free) tier page limit. "
            f"Upgrade to S0 tier for full document processing."
        )

    if result.paragraphs:
        for p in result.paragraphs:
            role = _map_role(getattr(p, "role", None))
            page = _get_page_number(p.bounding_regions)
            doc.paragraphs.append(StructuredParagraph(
                text=p.content,
                role=role,
                page_number=page,
            ))

    if result.tables:
        for table in result.tables:
            regions = getattr(table, "bounding_regions", None) or []
            pages = [r.page_number for r in regions] if regions else [1]
            caption_text = ""
            if hasattr(table, "caption") and table.caption:
                caption_text = table.caption.content if hasattr(table.caption, "content") else str(table.caption)
            doc.tables.append(StructuredTable(
                markdown=_table_to_markdown(table),
                page_start=min(pages),
                page_end=max(pages),
                row_count=table.row_count,
                column_count=table.column_count,
                caption=caption_text,
            ))

    if result.key_value_pairs:
        for kv in result.key_value_pairs:
            key_text = kv.key.content if kv.key else ""
            value_text = kv.value.content if kv.value else ""
            page = _get_page_number(kv.key.bounding_regions) if kv.key else 1
            doc.key_value_pairs.append(StructuredKeyValuePair(
                key=key_text,
                value=value_text,
                page_number=page,
            ))

    max_parsed_page = 0
    for p in doc.paragraphs:
        max_parsed_page = max(max_parsed_page, p.page_number)
    for t in doc.tables:
        max_parsed_page = max(max_parsed_page, t.page_end)

    if actual_page_count > 2 and max_parsed_page < actual_page_count * 0.5:
        raise RuntimeError(
            f"Azure DI only returned content up to page {max_parsed_page} "
            f"out of {actual_page_count}. Likely a tier page limit."
        )

    doc.sections = _build_sections(doc.paragraphs, doc.tables)

    logger.info(
        f"Azure DI parsed: {len(doc.paragraphs)} paragraphs, "
        f"{len(doc.tables)} tables, {len(doc.key_value_pairs)} KV pairs, "
        f"{len(doc.sections)} sections, {doc.page_count} pages "
        f"(DI processed {di_page_count} pages, content up to page {max_parsed_page})"
    )

    return doc
