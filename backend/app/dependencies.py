from functools import lru_cache

from openai import AsyncOpenAI
from pinecone import Pinecone
from supabase import create_client, Client as SupabaseClient

from app.config import settings


def get_azure_di_client():
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    return DocumentIntelligenceClient(
        endpoint=settings.azure_di_endpoint,
        credential=AzureKeyCredential(settings.azure_di_key),
    )


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


@lru_cache
def get_pinecone_index():
    pc = Pinecone(api_key=settings.pinecone_api_key)
    return pc.Index(settings.pinecone_index_name)


@lru_cache
def get_supabase_client() -> SupabaseClient:
    return create_client(settings.supabase_url, settings.supabase_key)
