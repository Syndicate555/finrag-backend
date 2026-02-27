from __future__ import annotations

import httpx
from fastapi import APIRouter

from app.config import settings
from app.dependencies import get_pinecone_index, get_supabase_client

router = APIRouter(prefix="/api", tags=["reset"])


@router.delete("/reset", status_code=204)
async def factory_reset():
    client = get_supabase_client()

    client.table("message_feedback").delete().neq("message_id", "00000000-0000-0000-0000-000000000000").execute()
    client.table("messages").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
    client.table("threads").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
    client.table("document_sections").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

    docs = client.table("documents").select("id").execute()
    doc_ids = [d["id"] for d in docs.data]
    client.table("documents").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()

    index = get_pinecone_index()
    for doc_id in doc_ids:
        index.delete(filter={"document_id": {"$eq": doc_id}})

    bucket = settings.supabase_storage_bucket
    async with httpx.AsyncClient() as http:
        list_resp = await http.get(
            f"{settings.supabase_url}/storage/v1/object/list/{bucket}",
            headers={
                "Authorization": f"Bearer {settings.supabase_key}",
                "apikey": settings.supabase_key,
            },
            params={"prefix": "", "limit": 1000},
            timeout=30.0,
        )
        if list_resp.status_code == 200:
            files = list_resp.json()
            file_paths = [f["name"] for f in files if f.get("name")]
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
