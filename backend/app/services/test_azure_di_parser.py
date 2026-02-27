from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.services.azure_di_parser import (
    ParagraphRole,
    StructuredParagraph,
    StructuredTable,
    _build_sections,
    _get_pdf_page_count,
    _map_role,
    _table_to_markdown,
)


class TestMapRole:
    @pytest.mark.parametrize(
        "role_str,expected",
        [
            ("title", ParagraphRole.TITLE),
            ("sectionHeading", ParagraphRole.SECTION_HEADING),
            ("pageHeader", ParagraphRole.PAGE_HEADER),
            ("pageFooter", ParagraphRole.PAGE_FOOTER),
            ("pageNumber", ParagraphRole.PAGE_NUMBER),
            ("footnote", ParagraphRole.FOOTNOTE),
            ("body", ParagraphRole.BODY),
            (None, ParagraphRole.BODY),
            ("unknownRole", ParagraphRole.BODY),
            ("", ParagraphRole.BODY),
        ],
    )
    def test_maps_role_string_to_enum(self, role_str, expected):
        assert _map_role(role_str) == expected


def _make_cell(row_index, column_index, content, row_span=1, col_span=1):
    return SimpleNamespace(
        row_index=row_index,
        column_index=column_index,
        content=content,
        row_span=row_span,
        column_span=col_span,
    )


class TestTableToMarkdown:
    def test_simple_table(self):
        table = SimpleNamespace(
            row_count=3,
            column_count=2,
            cells=[
                _make_cell(0, 0, "Name"),
                _make_cell(0, 1, "Value"),
                _make_cell(1, 0, "Revenue"),
                _make_cell(1, 1, "$100M"),
                _make_cell(2, 0, "Profit"),
                _make_cell(2, 1, "$20M"),
            ],
        )
        result = _table_to_markdown(table)
        expected = (
            "| Name | Value |\n"
            "| --- | --- |\n"
            "| Revenue | $100M |\n"
            "| Profit | $20M |"
        )
        assert result == expected

    def test_table_with_column_span(self):
        table = SimpleNamespace(
            row_count=2,
            column_count=3,
            cells=[
                _make_cell(0, 0, "Merged Header", col_span=2),
                _make_cell(0, 2, "C"),
                _make_cell(1, 0, "A1"),
                _make_cell(1, 1, "B1"),
                _make_cell(1, 2, "C1"),
            ],
        )
        result = _table_to_markdown(table)
        assert "Merged Header | Merged Header | C" in result

    def test_table_with_row_span(self):
        table = SimpleNamespace(
            row_count=3,
            column_count=2,
            cells=[
                _make_cell(0, 0, "H1"),
                _make_cell(0, 1, "H2"),
                _make_cell(1, 0, "Span", row_span=2),
                _make_cell(1, 1, "V1"),
                _make_cell(2, 1, "V2"),
            ],
        )
        result = _table_to_markdown(table)
        lines = result.strip().split("\n")
        assert len(lines) == 4
        assert "Span" in lines[2]
        assert "Span" in lines[3]

    def test_empty_table(self):
        table = SimpleNamespace(row_count=0, column_count=0, cells=[])
        assert _table_to_markdown(table) == ""

    def test_cell_with_newlines_normalized(self):
        table = SimpleNamespace(
            row_count=2,
            column_count=1,
            cells=[
                _make_cell(0, 0, "Header"),
                _make_cell(1, 0, "Line1\nLine2"),
            ],
        )
        result = _table_to_markdown(table)
        assert "\n" not in result.split("\n")[2].replace("\n", "")
        assert "Line1 Line2" in result


class TestBuildSections:
    def test_single_title_with_body(self):
        paragraphs = [
            StructuredParagraph("Main Title", ParagraphRole.TITLE, 1),
            StructuredParagraph("Body text here.", ParagraphRole.BODY, 1),
        ]
        sections = _build_sections(paragraphs, [])
        assert len(sections) == 1
        assert sections[0].heading == "Main Title"
        assert sections[0].level == 1
        assert len(sections[0].elements) == 1
        assert sections[0].elements[0].text == "Body text here."

    def test_title_then_section_heading(self):
        paragraphs = [
            StructuredParagraph("Title", ParagraphRole.TITLE, 1),
            StructuredParagraph("Body 1.", ParagraphRole.BODY, 1),
            StructuredParagraph("Sub Section", ParagraphRole.SECTION_HEADING, 2),
            StructuredParagraph("Body 2.", ParagraphRole.BODY, 2),
        ]
        sections = _build_sections(paragraphs, [])
        assert len(sections) == 2
        assert sections[0].heading == "Title"
        assert sections[0].level == 1
        assert sections[1].heading == "Sub Section"
        assert sections[1].level == 2

    def test_filters_page_headers_and_footers(self):
        paragraphs = [
            StructuredParagraph("Header", ParagraphRole.PAGE_HEADER, 1),
            StructuredParagraph("Footer", ParagraphRole.PAGE_FOOTER, 1),
            StructuredParagraph("Page 1", ParagraphRole.PAGE_NUMBER, 1),
            StructuredParagraph("Actual content.", ParagraphRole.BODY, 1),
        ]
        sections = _build_sections(paragraphs, [])
        assert len(sections) == 1
        assert len(sections[0].elements) == 1
        assert sections[0].elements[0].text == "Actual content."

    def test_tables_interleaved_by_page(self):
        paragraphs = [
            StructuredParagraph("Title", ParagraphRole.TITLE, 1),
            StructuredParagraph("Text on page 1.", ParagraphRole.BODY, 1),
            StructuredParagraph("Text on page 3.", ParagraphRole.BODY, 3),
        ]
        tables = [
            StructuredTable(
                markdown="| A | B |",
                page_start=2,
                page_end=2,
                row_count=1,
                column_count=2,
            ),
        ]
        sections = _build_sections(paragraphs, tables)
        assert len(sections) == 1
        elements = sections[0].elements
        assert len(elements) == 3
        assert isinstance(elements[0], StructuredParagraph)
        assert isinstance(elements[1], StructuredTable)
        assert isinstance(elements[2], StructuredParagraph)

    def test_empty_input(self):
        sections = _build_sections([], [])
        assert sections == []

    def test_footnote_not_filtered(self):
        paragraphs = [
            StructuredParagraph("Content.", ParagraphRole.BODY, 1),
            StructuredParagraph("See note 1.", ParagraphRole.FOOTNOTE, 1),
        ]
        sections = _build_sections(paragraphs, [])
        assert len(sections) == 1
        assert len(sections[0].elements) == 2


class TestGetPdfPageCount:
    def test_returns_page_count_from_pdf_bytes(self, tmp_path):
        import fitz

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        for _ in range(5):
            doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            page_count = _get_pdf_page_count(f.read())

        assert page_count == 5
