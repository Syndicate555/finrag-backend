from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.models.schemas import DocumentStatus
from app.services.document_processor import process_document


@pytest.fixture()
def _mock_externals():
    with (
        patch("app.services.document_processor.update_document_status") as mock_status,
        patch("app.services.document_processor.upload_to_supabase_storage", new_callable=AsyncMock) as mock_upload,
        patch("app.services.document_processor.embed_texts", new_callable=AsyncMock) as mock_embed,
        patch("app.services.document_processor.upsert_chunks") as mock_upsert,
        patch("app.services.document_processor.create_sections") as mock_sections,
        patch("app.services.document_processor._get_pdf_page_count", return_value=110) as mock_page_count,
    ):
        mock_upload.return_value = "https://example.com/file.pdf"
        mock_embed.return_value = [[0.1] * 1536]
        yield {
            "status": mock_status,
            "upload": mock_upload,
            "embed": mock_embed,
            "upsert": mock_upsert,
            "sections": mock_sections,
            "page_count": mock_page_count,
        }


@pytest.mark.asyncio
class TestProcessDocumentFallback:
    async def test_falls_back_to_pdfplumber_on_azure_di_failure(self, _mock_externals):
        fake_chunks = [MagicMock(text="chunk text", page_end=1, section_heading="Intro")]
        fake_sections = [{"heading": "Intro", "level": 1, "start_page": 1, "end_page": 1}]

        with (
            patch("app.config.settings.azure_di_enabled", True),
            patch(
                "app.services.document_processor._azure_di_parse",
                side_effect=RuntimeError("Azure DI unavailable"),
            ),
            patch(
                "app.services.document_processor._fallback_parse",
                return_value=(fake_chunks, fake_sections, 110),
            ) as mock_fallback,
        ):
            await process_document("doc-123", b"%PDF-fake", "test.pdf")
            mock_fallback.assert_called_once_with(b"%PDF-fake")

    async def test_falls_back_when_azure_di_tier_limited(self, _mock_externals):
        fake_chunks = [MagicMock(text="chunk text", page_end=50, section_heading="Intro")]
        fake_sections = [{"heading": "Intro", "level": 1, "start_page": 1, "end_page": 50}]

        with (
            patch("app.config.settings.azure_di_enabled", True),
            patch(
                "app.services.document_processor._azure_di_parse",
                side_effect=RuntimeError("Azure DI only returned content up to page 2 out of 110"),
            ),
            patch(
                "app.services.document_processor._fallback_parse",
                return_value=(fake_chunks, fake_sections, 110),
            ) as mock_fallback,
        ):
            await process_document("doc-tier", b"%PDF-fake", "test.pdf")
            mock_fallback.assert_called_once_with(b"%PDF-fake")

    async def test_uses_pdfplumber_when_azure_di_disabled(self, _mock_externals):
        fake_chunks = [MagicMock(text="chunk text", page_end=1, section_heading="Intro")]
        fake_sections = [{"heading": "Intro", "level": 1, "start_page": 1, "end_page": 1}]

        with (
            patch("app.config.settings.azure_di_enabled", False),
            patch(
                "app.services.document_processor._fallback_parse",
                return_value=(fake_chunks, fake_sections, 110),
            ) as mock_fallback,
            patch(
                "app.services.document_processor._azure_di_parse",
            ) as mock_azure,
        ):
            await process_document("doc-456", b"%PDF-fake", "test.pdf")
            mock_fallback.assert_called_once()
            mock_azure.assert_not_called()

    async def test_uses_azure_di_when_enabled_and_succeeds(self, _mock_externals):
        fake_chunks = [MagicMock(text="azure chunk", page_end=2, section_heading="Summary")]
        fake_sections = [{"heading": "Summary", "level": 1, "start_page": 1, "end_page": 2}]

        with (
            patch("app.config.settings.azure_di_enabled", True),
            patch(
                "app.services.document_processor._azure_di_parse",
                return_value=(fake_chunks, fake_sections, 110),
            ) as mock_azure,
            patch(
                "app.services.document_processor._fallback_parse",
            ) as mock_fallback,
        ):
            await process_document("doc-789", b"%PDF-fake", "test.pdf")
            mock_azure.assert_called_once_with(b"%PDF-fake")
            mock_fallback.assert_not_called()

    async def test_page_count_always_from_pymupdf(self, _mock_externals):
        fake_chunks = [MagicMock(text="chunk text", page_end=1, section_heading="Intro")]
        fake_sections = [{"heading": "Intro", "level": 1, "start_page": 1, "end_page": 1}]

        with (
            patch("app.config.settings.azure_di_enabled", True),
            patch(
                "app.services.document_processor._azure_di_parse",
                return_value=(fake_chunks, fake_sections, 2),
            ),
        ):
            await process_document("doc-pg", b"%PDF-fake", "test.pdf")

        status_calls = _mock_externals["status"].call_args_list
        ready_call = [c for c in status_calls if c[0][1] == DocumentStatus.READY][0]
        assert ready_call[1]["page_count"] == 110
