from __future__ import annotations

from enum import IntEnum, Enum
from typing import NewType

from pydantic import BaseModel, Field

DocumentId = NewType("DocumentId", str)
ThreadId = NewType("ThreadId", str)
MessageId = NewType("MessageId", str)
SectionId = NewType("SectionId", str)


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageType(str, Enum):
    KB = "kb"
    GENERAL = "general"
    CLARIFICATION = "clarification"


class FeedbackSignal(IntEnum):
    LIKE = 1
    DISLIKE = -1


class QueryRoute(str, Enum):
    KB = "kb"
    GENERAL = "general"
    NEEDS_CLARIFICATION = "needs_clarification"


class Citation(BaseModel):
    page_start: int
    page_end: int
    section_heading: str
    relevance_score: float
    chunk_text: str


class ClarificationChip(BaseModel):
    section_id: str
    heading: str
    level: int
    label: str


class ChatRequest(BaseModel):
    message: str
    thread_id: ThreadId | None = None
    document_id: DocumentId | None = None


class FeedbackRequest(BaseModel):
    signal: FeedbackSignal


class ClarifyRequest(BaseModel):
    message: str
    thread_id: ThreadId
    document_id: DocumentId
    section_id: str
    section_heading: str


class DocumentUploadResponse(BaseModel):
    document_id: DocumentId
    status: DocumentStatus


class DocumentResponse(BaseModel):
    id: DocumentId
    filename: str
    blob_url: str | None = None
    status: DocumentStatus
    page_count: int | None = None
    sections: list[dict] | None = None
    created_at: str | None = None


class SectionResponse(BaseModel):
    id: SectionId
    heading: str
    level: int
    start_page: int
    end_page: int
    parent_section_id: SectionId | None = None


class ThreadResponse(BaseModel):
    id: ThreadId
    title: str | None = None
    document_id: DocumentId | None = None
    created_at: str


class MessageResponse(BaseModel):
    id: MessageId
    thread_id: ThreadId
    role: MessageRole
    content: str
    citations: list[Citation] | None = None
    clarification_chips: list[ClarificationChip] | None = None
    message_type: MessageType | None = None
    feedback: int | None = None
    created_at: str


class SSEEvent(BaseModel):
    event: str
    data: str
