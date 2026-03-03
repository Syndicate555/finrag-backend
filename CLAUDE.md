# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FinRAG — an enterprise RAG system for financial documents. Users upload PDFs, the backend parses/chunks/embeds them into Pinecone, and a chat interface answers questions with citations using GPT-4o via SSE streaming.

## Architecture

- **`backend/`** — Python 3.12 FastAPI server (the main codebase in this repo)
  - `app/main.py` — FastAPI app entry point, CORS, router mounting
  - `app/config.py` — Pydantic Settings singleton (`settings`), all config from env vars
  - `app/dependencies.py` — Cached singleton clients (OpenAI, Pinecone, Supabase)
  - `app/routers/` — API endpoint handlers (chat, documents, sections, reset)
  - `app/services/` — Business logic layer:
    - `rag_pipeline.py` — Retrieve context from Pinecone + stream GPT-4o response
    - `query_router.py` — GPT-4o-mini classifier (kb / general / needs_clarification)
    - `document_processor.py` — Orchestrates parse → chunk → embed → store
    - `azure_di_parser.py` / `pdf_parser.py` — PDF parsing (Azure DI primary, pdfplumber fallback)
    - `chunker.py` — Token-based chunking (512 max, 64 overlap)
    - `embedder.py` — Batch OpenAI embeddings
    - `pinecone_store.py` — Vector upsert/query/delete
    - `supabase_client.py` — All database + storage operations
  - `app/prompts/` — LLM prompt templates (system, rag, query_router)
  - `app/models/schemas.py` — Pydantic request/response models
- **`frontend/`** — Next.js 16 + React 19 + TypeScript (separate git repo, embedded here)
  - `src/lib/types.ts` — Branded ID types (`DocumentId`, `ThreadId`, etc.) and domain types
  - `src/lib/api.ts` — API client with SSE streaming parser
  - `src/lib/hooks/` — React hooks (use-chat, use-documents, use-threads, use-upload)
  - `src/components/` — UI components (chat, sidebar, upload, ui primitives via shadcn)
  - State management: Zustand for thread list, React Context for document selection
- **`supabase/migration.sql`** — Database schema (documents, document_sections, threads, messages, message_feedback)
- **`docs/`** — Architecture and deployment documentation

## External Services

OpenAI (embeddings + chat + routing), Pinecone (vector store), Supabase (PostgreSQL + object storage), Azure Document Intelligence (optional PDF parsing).

## Development Commands

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run dev server
uvicorn app.main:app --reload --port 8000

# Run tests
pytest
pytest app/services/test_chunker_structured.py           # single test file
pytest app/services/test_chunker_structured.py::test_name # single test
```

### Frontend

```bash
cd frontend
npm install

npm run dev       # dev server at localhost:3000
npm run build     # production build
npm run lint      # ESLint
```

### Docker (full stack)

```bash
docker compose up   # backend at :8000, frontend at :3000
```

## Environment Variables

Backend requires `backend/.env` with: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`. Optional: `AZURE_DI_ENABLED`, `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`. See `backend/.env.example`.

Frontend requires `frontend/.env.local` with: `NEXT_PUBLIC_API_URL` (defaults to empty for relative proxy).

## Key Patterns

- Backend tests are colocated in `app/services/` alongside source files (e.g., `test_chunker_structured.py`)
- SSE streaming: `/api/chat` returns events: `thread_id`, `citations`, `token`, `clarification`, `done`
- Query routing: every chat message is classified by GPT-4o-mini before processing (kb → retrieval, general → direct answer, needs_clarification → section chips)
- Document processing runs as a background task after upload

## CI/CD

GitHub Actions (`deploy-backend.yml`): on push to `main` with `backend/**` changes → Docker build → push to Azure Container Registry → update Azure Container Apps.
