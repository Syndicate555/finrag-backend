from __future__ import annotations

import httpx
from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException
from starlette.responses import Response

from app.config import settings
from app.models.schemas import DocumentId, DocumentResponse, DocumentStatus, DocumentUploadResponse
from app.services.document_processor import process_document
from app.services.pinecone_store import delete_document_vectors
from app.services.supabase_client import (
    create_document,
    delete_document,
    get_document,
    list_documents,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    doc = create_document(filename=file.filename)
    document_id = doc["id"]

    background_tasks.add_task(process_document, document_id, file_bytes, file.filename)

    return DocumentUploadResponse(
        document_id=DocumentId(document_id),
        status=DocumentStatus.PENDING,
    )


@router.get("", response_model=list[DocumentResponse])
async def get_documents():
    docs = list_documents()
    return [
        DocumentResponse(
            id=DocumentId(d["id"]),
            filename=d["filename"],
            blob_url=d.get("blob_url"),
            status=DocumentStatus(d["status"]),
            page_count=d.get("page_count"),
            sections=d.get("sections"),
            created_at=d.get("created_at"),
        )
        for d in docs
    ]


@router.delete("/{document_id}", status_code=204)
async def remove_document(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    delete_document_vectors(document_id)

    bucket = settings.supabase_storage_bucket
    async with httpx.AsyncClient() as http:
        list_resp = await http.get(
            f"{settings.supabase_url}/storage/v1/object/list/{bucket}",
            headers={
                "Authorization": f"Bearer {settings.supabase_key}",
                "apikey": settings.supabase_key,
            },
            params={"prefix": document_id, "limit": 100},
            timeout=30.0,
        )
        if list_resp.status_code == 200:
            files = list_resp.json()
            file_paths = [f"{document_id}/{f['name']}" for f in files if f.get("name")]
            if file_paths:
                await http.delete(
                    f"{settings.supabase_url}/storage/v1/object/{bucket}",
                    headers={
                        "Authorization": f"Bearer {settings.supabase_key}",
                        "apikey": settings.supabase_key,
                        "Content-Type": "application/json",
                    },
                    json={"prefixes": file_paths},
                    timeout=30.0,
                )

    delete_document(document_id)
    return Response(status_code=204)


@router.get("/{document_id}/status")
async def get_document_status(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": doc["status"], "page_count": doc.get("page_count")}
