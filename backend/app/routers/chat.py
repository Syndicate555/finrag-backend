from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import (
    ChatRequest,
    ClarifyRequest,
    DocumentId,
    FeedbackRequest,
    MessageId,
    MessageRole,
    MessageType,
    QueryRoute,
    ThreadId,
    ThreadResponse,
    MessageResponse,
    Citation,
    ClarificationChip,
)
from app.services.clarification import generate_clarification_chips
from app.services.query_router import classify_query
from app.services.rag_pipeline import stream_general_response, stream_rag_response
from app.services.supabase_client import (
    create_message,
    create_thread,
    delete_feedback,
    delete_thread,
    get_feedback_for_messages,
    get_message,
    get_messages,
    get_thread,
    list_threads,
    update_thread_title,
    upsert_feedback,
)

router = APIRouter(prefix="/api", tags=["chat"])


async def _generate_title(message: str) -> str:
    return message[:50].strip() + ("..." if len(message) > 50 else "")


@router.post("/chat")
async def chat(request: ChatRequest):
    thread_id = request.thread_id
    if not thread_id:
        thread = create_thread(
            document_id=request.document_id,
            title=await _generate_title(request.message),
        )
        thread_id = ThreadId(thread["id"])
    else:
        existing = get_thread(thread_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Thread not found")

    create_message(thread_id, MessageRole.USER, request.message)

    document_id = request.document_id
    if not document_id:
        thread_data = get_thread(thread_id)
        document_id = DocumentId(thread_data["document_id"]) if thread_data and thread_data.get("document_id") else None

    route = QueryRoute.GENERAL
    if document_id:
        route = await classify_query(request.message)

    async def event_stream():
        yield {"event": "thread_id", "data": thread_id}

        if route == QueryRoute.NEEDS_CLARIFICATION:
            chips = generate_clarification_chips(document_id, request.message)
            chip_data = [c.model_dump() for c in chips]
            yield {"event": "clarification", "data": json.dumps(chip_data)}

            create_message(
                thread_id,
                MessageRole.ASSISTANT,
                "I found multiple relevant sections. Which area would you like me to focus on?",
                message_type=MessageType.CLARIFICATION,
                clarification_chips=chips,
            )
            yield {"event": "done", "data": ""}
            return

        full_content = ""
        citations_list: list[Citation] = []

        if route == QueryRoute.KB and document_id:
            async for event_type, data, cites in stream_rag_response(request.message, document_id):
                if event_type == "citations":
                    yield {"event": "citations", "data": data}
                elif event_type == "token":
                    full_content += data
                    yield {"event": "token", "data": data}
                elif event_type == "done" and cites:
                    citations_list = cites
        else:
            async for event_type, data, _ in stream_general_response(request.message):
                if event_type == "token":
                    full_content += data
                    yield {"event": "token", "data": data}

        msg_type = MessageType.KB if route == QueryRoute.KB else MessageType.GENERAL
        create_message(
            thread_id,
            MessageRole.ASSISTANT,
            full_content,
            message_type=msg_type,
            citations=citations_list if citations_list else None,
        )

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())


@router.post("/chat/clarify")
async def chat_clarify(request: ClarifyRequest):
    existing = get_thread(request.thread_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Thread not found")

    create_message(request.thread_id, MessageRole.USER, f"[{request.section_heading}] {request.message}")

    async def event_stream():
        full_content = ""
        citations_list: list[Citation] = []

        async for event_type, data, cites in stream_rag_response(
            request.message,
            request.document_id,
            section_filter=request.section_heading,
        ):
            if event_type == "citations":
                yield {"event": "citations", "data": data}
            elif event_type == "token":
                full_content += data
                yield {"event": "token", "data": data}
            elif event_type == "done" and cites:
                citations_list = cites

        create_message(
            request.thread_id,
            MessageRole.ASSISTANT,
            full_content,
            message_type=MessageType.KB,
            citations=citations_list if citations_list else None,
        )

        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_stream())


@router.get("/threads", response_model=list[ThreadResponse])
async def get_threads():
    threads = list_threads()
    return [
        ThreadResponse(
            id=ThreadId(t["id"]),
            title=t.get("title"),
            document_id=DocumentId(t["document_id"]) if t.get("document_id") else None,
            created_at=t["created_at"],
        )
        for t in threads
    ]


@router.get("/threads/{thread_id}/messages", response_model=list[MessageResponse])
async def get_thread_messages(thread_id: str):
    messages = get_messages(thread_id)
    message_ids = [m["id"] for m in messages]
    feedback_map = get_feedback_for_messages(message_ids)
    return [
        MessageResponse(
            id=MessageId(m["id"]),
            thread_id=ThreadId(m["thread_id"]),
            role=MessageRole(m["role"]),
            content=m["content"],
            citations=[Citation(**c) for c in m["citations"]] if m.get("citations") else None,
            clarification_chips=[ClarificationChip(**c) for c in m["clarification_chips"]] if m.get("clarification_chips") else None,
            message_type=MessageType(m["message_type"]) if m.get("message_type") else None,
            feedback=feedback_map.get(m["id"]),
            created_at=m["created_at"],
        )
        for m in messages
    ]


@router.put("/messages/{message_id}/feedback")
async def put_feedback(message_id: str, body: FeedbackRequest):
    if not get_message(message_id):
        raise HTTPException(status_code=404, detail="Message not found")
    upsert_feedback(message_id, body.signal.value)
    return {"status": "ok"}


@router.delete("/messages/{message_id}/feedback", status_code=204)
async def remove_feedback(message_id: str):
    delete_feedback(message_id)


@router.delete("/threads/{thread_id}", status_code=204)
async def remove_thread(thread_id: str):
    existing = get_thread(thread_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Thread not found")
    delete_thread(thread_id)

