from __future__ import annotations

import pytest

from app.services.azure_di_parser import (
    ParagraphRole,
    StructuredDocument,
    StructuredKeyValuePair,
    StructuredParagraph,
    StructuredSection,
    StructuredTable,
)
from app.services.chunker import _build_embedding_text, chunk_structured_document


def _make_section(
    heading: str,
    level: int,
    elements: list,
    page_start: int = 1,
    page_end: int = 1,
) -> StructuredSection:
    return StructuredSection(
        heading=heading,
        level=level,
        page_start=page_start,
        page_end=page_end,
        elements=elements,
    )


def _make_paragraph(text: str, page: int = 1, role: ParagraphRole = ParagraphRole.BODY) -> StructuredParagraph:
    return StructuredParagraph(text=text, role=role, page_number=page)


def _make_table(markdown: str, page_start: int = 1, page_end: int = 1, caption: str = "") -> StructuredTable:
    return StructuredTable(
        markdown=markdown,
        page_start=page_start,
        page_end=page_end,
        row_count=2,
        column_count=2,
        caption=caption,
    )


class TestChunkStructuredDocument:
    def test_empty_document_returns_empty(self):
        doc = StructuredDocument()
        assert chunk_structured_document(doc) == []

    def test_single_section_under_max_tokens_produces_one_chunk(self):
        section = _make_section(
            "Overview",
            1,
            [_make_paragraph("This is a short paragraph.")],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert len(chunks) == 1
        assert chunks[0].text == "This is a short paragraph."
        assert chunks[0].section_heading == "Overview"
        assert chunks[0].section_level == 1
        assert chunks[0].content_type == "text"

    def test_long_section_splits_into_multiple_chunks(self):
        long_text = ". ".join(f"Sentence number {i} with enough words to use tokens" for i in range(200))
        section = _make_section(
            "Long Section",
            1,
            [_make_paragraph(long_text)],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.section_heading == "Long Section"
            assert chunk.content_type == "text"

    def test_table_becomes_separate_chunk(self):
        table_md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        section = _make_section(
            "Data",
            1,
            [
                _make_paragraph("Before table."),
                _make_table(table_md),
                _make_paragraph("After table."),
            ],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        text_chunks = [c for c in chunks if c.content_type == "text"]
        table_chunks = [c for c in chunks if c.content_type == "table"]
        assert len(table_chunks) == 1
        assert table_chunks[0].text == table_md
        assert len(text_chunks) == 2

    def test_table_caption_prepended(self):
        table_md = "| X | Y |\n| --- | --- |\n| 1 | 2 |"
        caption = "Table 1: Revenue by Segment"
        section = _make_section(
            "Financials",
            1,
            [_make_table(table_md, caption=caption)],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert len(chunks) == 1
        assert chunks[0].text.startswith(caption)
        assert table_md in chunks[0].text

    def test_key_value_pairs_grouped(self):
        doc = StructuredDocument(
            key_value_pairs=[
                StructuredKeyValuePair(key="Date", value="2024-01-01", page_number=1),
                StructuredKeyValuePair(key="Total", value="$500M", page_number=1),
            ],
        )
        chunks = chunk_structured_document(doc)
        assert len(chunks) == 1
        assert chunks[0].section_heading == "Key Information"
        assert "- Date: 2024-01-01" in chunks[0].text
        assert "- Total: $500M" in chunks[0].text

    def test_footnotes_marked_with_prefix(self):
        section = _make_section(
            "Notes",
            1,
            [
                _make_paragraph("Main content."),
                _make_paragraph("Important footnote.", role=ParagraphRole.FOOTNOTE),
            ],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert len(chunks) == 1
        assert "[Footnote: Important footnote.]" in chunks[0].text

    def test_l2_section_gets_parent_l1(self):
        l1 = _make_section("Chapter 1", 1, [_make_paragraph("Intro.")])
        l2 = _make_section("Section 1.1", 2, [_make_paragraph("Details.")])
        doc = StructuredDocument(sections=[l1, l2])
        chunks = chunk_structured_document(doc)
        l2_chunks = [c for c in chunks if c.section_heading == "Section 1.1"]
        assert len(l2_chunks) == 1
        assert l2_chunks[0].parent_section == "Chapter 1"

    def test_paragraphs_joined_with_double_newline(self):
        section = _make_section(
            "Multi Para",
            1,
            [
                _make_paragraph("First paragraph."),
                _make_paragraph("Second paragraph."),
            ],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert len(chunks) == 1
        assert "First paragraph.\n\nSecond paragraph." == chunks[0].text

    def test_chunk_indices_sequential(self):
        section = _make_section(
            "Data",
            1,
            [
                _make_paragraph("Text."),
                _make_table("| A |\n| --- |\n| 1 |"),
                _make_paragraph("More text."),
            ],
        )
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_table_chunk_has_section_context_in_embedding_text(self):
        table_md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        l1 = _make_section("Financial Review", 1, [_make_paragraph("Intro.")])
        l2 = _make_section(
            "Balance Sheet",
            2,
            [_make_table(table_md)],
        )
        doc = StructuredDocument(sections=[l1, l2])
        chunks = chunk_structured_document(doc)
        table_chunks = [c for c in chunks if c.content_type == "table"]
        assert len(table_chunks) == 1
        assert table_chunks[0].embedding_text.startswith("Financial Review > Balance Sheet")
        assert table_md in table_chunks[0].embedding_text

    def test_text_chunk_embedding_text_is_empty(self):
        section = _make_section("Overview", 1, [_make_paragraph("Some text.")])
        doc = StructuredDocument(sections=[section])
        chunks = chunk_structured_document(doc)
        assert chunks[0].embedding_text == ""


class TestBuildEmbeddingText:
    def test_table_with_parent_and_section(self):
        result = _build_embedding_text("| data |", "Balance Sheet", "Financial Review", "table")
        assert result == "Financial Review > Balance Sheet\n\n| data |"

    def test_table_with_section_only(self):
        result = _build_embedding_text("| data |", "Summary", "", "table")
        assert result == "Summary\n\n| data |"

    def test_text_returns_unchanged(self):
        result = _build_embedding_text("plain text", "Section", "Parent", "text")
        assert result == "plain text"
