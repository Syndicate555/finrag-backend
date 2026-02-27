from __future__ import annotations

import json
from typing import Any

from app.dependencies import get_supabase_client
from app.models.schemas import (
    Citation,
    ClarificationChip,
    DocumentId,
    DocumentStatus,
    MessageRole,
    MessageType,
    ThreadId,
)


def create_document(filename: str, blob_url: str | None = None) -> dict:
    client = get_supabase_client()
    result = client.table("documents").insert({
        "filename": filename,
        "blob_url": blob_url,
        "status": DocumentStatus.PENDING.value,
    }).execute()
    return result.data[0]


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    page_count: int | None = None,
    sections: list[dict] | None = None,
) -> None:
    client = get_supabase_client()
    update: dict[str, Any] = {"status": status.value}
    if page_count is not None:
        update["page_count"] = page_count
    if sections is not None:
        update["sections"] = sections
    client.table("documents").update(update).eq("id", document_id).execute()


def get_document(document_id: str) -> dict | None:
    client = get_supabase_client()
    result = client.table("documents").select("*").eq("id", document_id).execute()
    return result.data[0] if result.data else None


def list_documents() -> list[dict]:
    client = get_supabase_client()
    result = client.table("documents").select("*").execute()
    return result.data


def create_sections(document_id: str, sections: list[dict]) -> list[dict]:
    client = get_supabase_client()
    rows = [
        {
            "document_id": document_id,
            "heading": s["heading"],
            "level": s["level"],
            "start_page": s["start_page"],
            "end_page": s["end_page"],
            "parent_section_id": s.get("parent_section_id"),
        }
        for s in sections
    ]
    result = client.table("document_sections").insert(rows).execute()
    return result.data


def get_sections(document_id: str) -> list[dict]:
    client = get_supabase_client()
    result = (
        client.table("document_sections")
        .select("*")
        .eq("document_id", document_id)
        .order("start_page")
        .execute()
    )
    return result.data


def create_thread(document_id: str | None = None, title: str | None = None) -> dict:
    client = get_supabase_client()
    data: dict[str, Any] = {}
    if document_id:
        data["document_id"] = document_id
    if title:
        data["title"] = title
    result = client.table("threads").insert(data).execute()
    return result.data[0]


def get_thread(thread_id: str) -> dict | None:
    client = get_supabase_client()
    result = client.table("threads").select("*").eq("id", thread_id).execute()
    return result.data[0] if result.data else None


def list_threads() -> list[dict]:
    client = get_supabase_client()
    result = client.table("threads").select("*").order("created_at", desc=True).execute()
    return result.data


def update_thread_title(thread_id: str, title: str) -> None:
    client = get_supabase_client()
    client.table("threads").update({"title": title}).eq("id", thread_id).execute()


def delete_thread(thread_id: str) -> None:
    client = get_supabase_client()
    client.table("threads").delete().eq("id", thread_id).execute()


def create_message(
    thread_id: str,
    role: MessageRole,
    content: str,
    message_type: MessageType | None = None,
    citations: list[Citation] | None = None,
    clarification_chips: list[ClarificationChip] | None = None,
) -> dict:
    client = get_supabase_client()
    data: dict[str, Any] = {
        "thread_id": thread_id,
        "role": role.value,
        "content": content,
    }
    if message_type:
        data["message_type"] = message_type.value
    if citations:
        data["citations"] = [c.model_dump() for c in citations]
    if clarification_chips:
        data["clarification_chips"] = [c.model_dump() for c in clarification_chips]

    result = client.table("messages").insert(data).execute()
    return result.data[0]


def get_message(message_id: str) -> dict | None:
    client = get_supabase_client()
    result = client.table("messages").select("*").eq("id", message_id).execute()
    return result.data[0] if result.data else None


def get_messages(thread_id: str) -> list[dict]:
    client = get_supabase_client()
    result = (
        client.table("messages")
        .select("*")
        .eq("thread_id", thread_id)
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def upsert_feedback(message_id: str, signal: int) -> None:
    client = get_supabase_client()
    client.table("message_feedback").upsert({
        "message_id": message_id,
        "signal": signal,
    }).execute()


def delete_feedback(message_id: str) -> None:
    client = get_supabase_client()
    client.table("message_feedback").delete().eq("message_id", message_id).execute()


def get_feedback_for_messages(message_ids: list[str]) -> dict[str, int]:
    if not message_ids:
        return {}
    client = get_supabase_client()
    result = (
        client.table("message_feedback")
        .select("message_id, signal")
        .in_("message_id", message_ids)
        .execute()
    )
    return {row["message_id"]: row["signal"] for row in result.data}


def delete_document(document_id: str) -> None:
    client = get_supabase_client()
    threads = (
        client.table("threads")
        .select("id")
        .eq("document_id", document_id)
        .execute()
    )
    thread_ids = [t["id"] for t in threads.data]
    for tid in thread_ids:
        client.table("messages").delete().eq("thread_id", tid).execute()
    if thread_ids:
        client.table("threads").delete().in_("id", thread_ids).execute()
    client.table("document_sections").delete().eq("document_id", document_id).execute()
    client.table("documents").delete().eq("id", document_id).execute()
