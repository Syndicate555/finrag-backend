from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    pinecone_api_key: str
    pinecone_index_name: str = "pwc-rag"
    supabase_url: str
    supabase_key: str
    supabase_storage_bucket: str = "pwc-rag"

    openai_embedding_model: str = "text-embedding-3-large"
    openai_chat_model: str = "gpt-4o"
    openai_router_model: str = "gpt-4o-mini"

    embedding_dimensions: int = 1536
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 64
    retrieval_top_k: int = 20

    azure_di_endpoint: str = ""
    azure_di_key: str = ""
    azure_di_enabled: bool = False

    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
