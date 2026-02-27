from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pdfplumber


@dataclass
class TextBlock:
    text: str
    page_number: int
    font_size: float
    is_bold: bool


@dataclass
class TableBlock:
    markdown: str
    page_number: int


@dataclass
class HeadingBlock:
    text: str
    level: int
    page_number: int


@dataclass
class ParsedDocument:
    text_blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableBlock] = field(default_factory=list)
    headings: list[HeadingBlock] = field(default_factory=list)
    page_count: int = 0


def _table_to_markdown(table: list[list[str | None]]) -> str:
    if not table or not table[0]:
        return ""
    headers = [cell or "" for cell in table[0]]
    md = "| " + " | ".join(headers) + " |\n"
    md += "| " + " | ".join("---" for _ in headers) + " |\n"
    for row in table[1:]:
        cells = [cell or "" for cell in row]
        md += "| " + " | ".join(cells) + " |\n"
    return md.strip()


def _extract_font_sizes(pdf: pdfplumber.PDF) -> list[float]:
    sizes = []
    for page in pdf.pages:
        for char in page.chars:
            if char.get("size"):
                sizes.append(float(char["size"]))
    return sizes


def _detect_heading_threshold(font_sizes: list[float]) -> float:
    if not font_sizes:
        return 14.0
    median = statistics.median(font_sizes)
    return median * 1.3


def parse_pdf(pdf_path: str) -> ParsedDocument:
    doc = ParsedDocument()

    with pdfplumber.open(pdf_path) as pdf:
        doc.page_count = len(pdf.pages)
        font_sizes = _extract_font_sizes(pdf)
        heading_threshold = _detect_heading_threshold(font_sizes)

        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            for table in tables:
                md = _table_to_markdown(table)
                if md:
                    doc.tables.append(TableBlock(markdown=md, page_number=page_num))

            lines: dict[float, list[dict]] = {}
            for char in page.chars:
                top = round(char["top"], 1)
                lines.setdefault(top, []).append(char)

            for top in sorted(lines.keys()):
                chars = lines[top]
                text = "".join(c.get("text", "") for c in chars).strip()
                if not text:
                    continue

                avg_size = statistics.mean(float(c["size"]) for c in chars if c.get("size"))
                is_bold = any("Bold" in (c.get("fontname", "") or "") for c in chars)

                block = TextBlock(
                    text=text,
                    page_number=page_num,
                    font_size=avg_size,
                    is_bold=is_bold,
                )
                doc.text_blocks.append(block)

                if avg_size >= heading_threshold or (is_bold and avg_size >= heading_threshold * 0.9):
                    level = 1 if avg_size >= heading_threshold * 1.15 else 2
                    doc.headings.append(HeadingBlock(text=text, level=level, page_number=page_num))

    return doc
