# FinRAG — Architecture & Operations Guide

> A comprehensive reference for understanding, operating, and extending the FinRAG platform.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Frontend (Next.js)](#3-frontend-nextjs)
4. [Backend (FastAPI)](#4-backend-fastapi)
5. [RAG Pipeline — End to End](#5-rag-pipeline--end-to-end)
6. [External Services](#6-external-services)
7. [Database Schema](#7-database-schema)
8. [Deployment & Infrastructure](#8-deployment--infrastructure)
9. [CI/CD Pipeline](#9-cicd-pipeline)
10. [Local Development](#10-local-development)
11. [Design Decisions & Trade-offs](#11-design-decisions--trade-offs)
12. [Environment Variables Reference](#12-environment-variables-reference)
13. [API Endpoints Reference](#13-api-endpoints-reference)
14. [Common Operations](#14-common-operations)

---

## 1. System Overview

FinRAG is a **Retrieval-Augmented Generation** platform for analyzing financial documents. Users upload PDFs (annual reports, MD&A filings, etc.), and the system parses, chunks, embeds, and indexes them into a vector store. Users then ask questions in a chat interface, and the system retrieves relevant context from the document and streams an LLM-generated answer with page-level citations.

### Core Capabilities

- PDF upload with structured parsing (Azure Document Intelligence) or heuristic parsing (pdfplumber)
- Token-based chunking with overlap for context preservation
- Semantic search via vector embeddings (OpenAI + Pinecone)
- 3-tier query routing: knowledge-base, general, or clarification
- Streaming chat responses with real-time citations
- Section-level clarification flow for ambiguous queries
- Message feedback (thumbs up/down) for quality tracking

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│                                                                 │
│  ┌──────────────┐     ┌──────────────────────────────────────┐  │
│  │   Sidebar     │     │        Chat Interface                │  │
│  │  - Threads    │     │  - Message bubbles (markdown)        │  │
│  │  - Upload     │     │  - Citation badges with tooltips     │  │
│  │  - Search     │     │  - Clarification chips               │  │
│  │  - Reset      │     │  - Streaming indicator               │  │
│  └──────────────┘     │  - Source panel (document selector)   │  │
│                        └──────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTPS (SSE for streaming)
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    FRONTEND — Next.js 15                         │
│                    Vercel (https://finrag.info)                  │
│                                                                  │
│  App Router │ Zustand (threads) │ React Context (documents)      │
│  Tailwind CSS + shadcn UI │ TypeScript strict mode               │
└──────────────────────────────┬───────────────────────────────────┘
                               │ NEXT_PUBLIC_API_URL
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    BACKEND — FastAPI                              │
│                    Azure Container Apps                           │
│                    (pwc-rag-api.politemeadow-4143bf92.           │
│                     westus.azurecontainerapps.io)                 │
│                                                                  │
│  Routers: chat │ documents │ sections │ reset                    │
│  Services: rag_pipeline │ query_router │ document_processor      │
│            chunker │ embedder │ pdf_parser │ azure_di_parser      │
└────┬──────────┬──────────┬──────────┬───────────────────────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
  ┌──────┐ ┌────────┐ ┌────────┐ ┌──────────────────┐
  │OpenAI│ │Pinecone│ │Supabase│ │Azure Doc Intel.  │
  │      │ │        │ │        │ │  (optional)       │
  │GPT-4o│ │Vector  │ │Postgres│ │  Structured PDF   │
  │Embed │ │Store   │ │Storage │ │  parsing          │
  └──────┘ └────────┘ └────────┘ └──────────────────┘
```

### Data Flow Summary

```
Upload:  PDF → Parse → Chunk → Embed → Store vectors + metadata
Query:   Question → Route → Retrieve context → Stream LLM answer + citations
```

---

## 3. Frontend (Next.js)

### Stack

| Technology | Purpose |
|-----------|---------|
| Next.js 16 + React 19 | Framework, App Router, React Compiler |
| TypeScript (strict) | Type safety with branded types |
| Zustand | Thread list state management |
| React Context | Document selection state |
| Tailwind CSS v4 + shadcn | Styling and accessible UI components |
| react-markdown + remark-gfm | Rendering LLM responses with tables |
| react-dropzone | PDF upload drag-and-drop |
| GSAP | Landing page animations |

### Route Structure

```
/                      → Landing page (feature showcase, CTA)
/chat                  → New chat session (no thread yet)
/chat/[threadId]       → Existing conversation thread
```

### State Management Strategy

The frontend uses a **hybrid approach** — not everything lives in a global store:

- **Zustand store** (`useThreads`): Thread list. Global because the sidebar and chat both need it, and it persists across route navigation.
- **React Context** (`DocumentProvider`): Active document selection. Context because it's needed by many components but doesn't change frequently.
- **Local state** (`useChat` hook): Messages, streaming content, citations. Local because each chat page is independent — navigating away discards streaming state, and messages are re-fetched from the API on mount.

This avoids the complexity of putting everything in a global store while keeping the data flow predictable.

### API Communication

All backend calls go through `lib/api.ts`, which provides:

- `apiFetch<T>()` — typed wrapper around `fetch` with error handling
- `streamChat()` / `streamClarify()` — SSE streaming with `parseSSEStream()` for real-time token delivery
- `AbortController` integration for cancelling in-flight streams

The frontend uses `NEXT_PUBLIC_API_URL` to reach the backend. In local dev, `next.config.ts` proxies `/api/*` requests to `http://127.0.0.1:8000` so no CORS is needed.

### Key UI Components

| Component | Role |
|-----------|------|
| `ChatContainer` | Orchestrator — manages layout, loads messages, coordinates streaming |
| `ChatInput` | Auto-resizing textarea, Enter to send, Shift+Enter for newline |
| `MessageBubble` | Renders markdown, shows feedback buttons on hover, displays citations |
| `CitationBadge` | Hoverable badge showing page range, section, relevance score |
| `SectionChips` | Interactive buttons for clarification (user picks a document section) |
| `SourcePanel` | Right sidebar — document list with status indicators, click to select |
| `Sidebar` | Left nav — thread list with search, upload button, factory reset |
| `UploadModal` | Drag-drop PDF upload with 3-step processing progress animation |
| `MarkdownRenderer` | Custom react-markdown with styled tables, code blocks, headings |

### Branded Types

The frontend mirrors the backend's type system using TypeScript branded types:

```typescript
type DocumentId = string & { readonly __brand: "DocumentId" }
type ThreadId   = string & { readonly __brand: "ThreadId" }
type MessageId  = string & { readonly __brand: "MessageId" }
type SectionId  = string & { readonly __brand: "SectionId" }
```

This prevents accidentally passing a `ThreadId` where a `DocumentId` is expected, catching bugs at compile time.

---

## 4. Backend (FastAPI)

### Stack

| Technology | Purpose |
|-----------|---------|
| FastAPI + Uvicorn | Async HTTP server |
| Pydantic Settings | Configuration from environment |
| OpenAI SDK (async) | Embeddings + chat completions |
| Pinecone | Vector store for semantic search |
| Supabase (Python) | PostgreSQL + object storage |
| Azure Document Intelligence | Structured PDF parsing (optional) |
| pdfplumber + PyMuPDF | Fallback PDF parsing |
| tiktoken | Token counting for chunking |
| sse-starlette | Server-Sent Events streaming |

### Code Organization

```
backend/app/
├── main.py              # FastAPI app, CORS, health check, router mounting
├── config.py            # Pydantic Settings (env vars → typed config)
├── dependencies.py      # Cached singleton clients (OpenAI, Pinecone, Supabase)
├── models/schemas.py    # All request/response Pydantic models
├── prompts/             # LLM prompt templates (system, RAG, query router)
├── routers/             # API endpoint handlers
│   ├── chat.py          # Chat streaming, threads, messages, feedback
│   ├── documents.py     # Upload, list, delete documents
│   ├── sections.py      # Document section retrieval
│   └── reset.py         # Factory reset (delete everything)
└── services/            # Business logic
    ├── rag_pipeline.py       # Retrieve context + stream LLM response
    ├── query_router.py       # Classify query intent (kb/general/clarify)
    ├── clarification.py      # Generate section suggestion chips
    ├── document_processor.py # Orchestrate parse → chunk → embed → store
    ├── pdf_parser.py         # pdfplumber-based parsing with heading heuristics
    ├── azure_di_parser.py    # Azure Document Intelligence parsing
    ├── chunker.py            # Token-based text chunking with overlap
    ├── embedder.py           # Batch OpenAI embedding generation
    ├── pinecone_store.py     # Vector upsert/query/delete
    └── supabase_client.py    # All database operations
```

### Dependency Injection

Clients are created once and cached using `@lru_cache`:

```python
@lru_cache
def get_openai_client() -> AsyncOpenAI: ...

@lru_cache
def get_pinecone_index() -> Index: ...

@lru_cache
def get_supabase_client() -> Client: ...
```

This avoids creating new connections per request while keeping the code testable (functions can be overridden in tests).

---

## 5. RAG Pipeline — End to End

### Phase 1: Document Ingestion

```
                    ┌─────────────┐
                    │  PDF Upload  │
                    │  (max 50MB)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Store in    │
                    │  Supabase    │
                    │  Storage     │
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │   Parse PDF              │
              │                          │
              │  Primary: Azure Doc      │
              │  Intelligence            │
              │  (structured sections,   │
              │   tables, headings)      │
              │                          │
              │  Fallback: pdfplumber    │
              │  (font-size heuristics   │
              │   for heading detection) │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   Chunk Document         │
              │                          │
              │  - Max 512 tokens/chunk  │
              │  - 64 token overlap      │
              │  - Tables = separate     │
              │    chunks                │
              │  - Sentence-level split  │
              └────────────┬────────────┘
              ┌────────────▼────────────┐
              │   Embed Chunks           │
              │                          │
              │  OpenAI text-embedding-  │
              │  3-large (1536 dims)     │
              │  Batched: 100/API call   │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   Store in Pinecone      │
              │                          │
              │  ID: "{doc_id}#{index}"  │
              │  Metadata: section,      │
              │  pages, content_type,    │
              │  full chunk text         │
              └─────────────────────────┘
```

**Key design choice — Table embedding enrichment**: When a table is embedded, the embedding text is prefixed with its section context (e.g., `"Financial Review > Balance Sheet\n\n| data |"`). This helps the embedding model understand what the table is about, since table markdown alone lacks semantic context.

**Key design choice — Fallback parsing strategy**: Azure Document Intelligence provides structured, semantic parsing (headings, roles, sections). But if it's disabled, unavailable, or hits a tier limit (free tier = 20 pages), the system gracefully falls back to pdfplumber with font-size heuristics for heading detection. The 50% content threshold triggers the fallback — if Azure DI returns content for less than half the pages, it's likely a tier limit.

### Phase 2: Query Routing

```
         ┌──────────────┐
         │ User Question │
         └──────┬───────┘
                │
    ┌───────────▼───────────┐
    │   Query Router         │
    │   (GPT-4o-mini,        │
    │    temp=0, 20 tokens)  │
    └───────────┬───────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────────┐
│   KB   │ │General │ │Needs         │
│        │ │        │ │Clarification │
└───┬────┘ └───┬────┘ └──────┬───────┘
    │          │              │
    ▼          ▼              ▼
 Retrieve   Direct LLM    Show section
 from       response       chips to user
 Pinecone   (no context)   (user picks)
```

**Why a separate router?** Using a cheap, fast model (GPT-4o-mini with only 20 max tokens) as a classifier avoids wasting expensive retrieval + GPT-4o calls on general knowledge questions ("What is net income?") that don't need document context. It also catches ambiguous queries that would produce poor results and instead asks the user to specify which section they mean.

### Phase 3: Retrieval & Response

```
    ┌──────────────────┐
    │  Embed query      │
    │  (same model as   │
    │   document chunks) │
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  Pinecone query   │
    │  top_k=20         │
    │  filter: doc_id   │
    │  (+ section if    │
    │   clarified)      │
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  Format context   │
    │  with sections    │
    │  and page ranges  │
    └────────┬─────────┘
             │
    ┌────────▼──────────────┐
    │  Stream GPT-4o         │
    │  (temp=0.1 for KB,     │
    │   temp=0.3 for general)│
    │                        │
    │  SSE events:           │
    │  1. "citations" (JSON) │
    │  2. "token" (deltas)   │
    │  3. "done" (final)     │
    └────────────────────────┘
```

**Why stream citations first?** Citations are derived from the Pinecone retrieval step, which completes before the LLM starts generating. Sending them as the first SSE event lets the frontend display them immediately while the answer streams in, giving a more responsive feel.

---

## 6. External Services

| Service | What It Does | Failure Impact | Cost Model |
|---------|-------------|----------------|------------|
| **OpenAI** | Embeddings (`text-embedding-3-large`), chat (`gpt-4o`), routing (`gpt-4o-mini`) | System cannot embed or answer | Pay-per-token |
| **Pinecone** | Vector storage and semantic similarity search | No retrieval — KB queries fail | Free tier available, then pay-per-query |
| **Supabase** | PostgreSQL (metadata, threads, messages), object storage (PDF files) | No persistence — nothing works | Free tier generous, then pay-per-use |
| **Azure Document Intelligence** | Structured PDF parsing (tables, headings, sections) | Graceful fallback to pdfplumber | Free tier: 20 pages/doc limit |

### Service Interaction Diagram

```
Frontend ──SSE──▶ Backend ──async──▶ OpenAI (embeddings + chat)
                     │
                     ├──async──▶ Pinecone (vector search)
                     │
                     ├──sync───▶ Supabase (CRUD + file storage)
                     │
                     └──async──▶ Azure DI (PDF parsing, optional)
```

---

## 7. Database Schema

### Entity Relationship

```
documents ◄──────── document_sections
    │                    (parent_section_id → self-referential)
    │
    ▼
threads ◄──────── messages ◄──── message_feedback
```

### Tables

**`documents`** — Uploaded PDF files
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | Auto-generated |
| filename | text | Original file name |
| blob_url | text | Supabase storage URL |
| status | text | `pending` → `processing` → `ready` / `failed` |
| page_count | integer | Set after parsing |
| sections | JSONB | Section hierarchy (denormalized for quick access) |
| created_at | timestamptz | Auto |

**`document_sections`** — Hierarchical section index
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | |
| document_id | UUID (FK → documents) | Cascade delete |
| heading | text | Section title |
| level | integer | 1 = top-level, 2 = subsection |
| start_page | integer | |
| end_page | integer | |
| parent_section_id | UUID (FK → self) | Null for level-1 |

**`threads`** — Chat conversations
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | |
| document_id | UUID (FK → documents) | Nullable |
| title | text | Auto-generated from first message |
| created_at, updated_at | timestamptz | |

**`messages`** — Chat messages
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | |
| thread_id | UUID (FK → threads) | Cascade delete |
| role | text | `user` or `assistant` |
| content | text | Message body |
| message_type | text | `kb`, `general`, or `clarification` |
| citations | JSONB | Array of citation objects |
| clarification_chips | JSONB | Array of section suggestion chips |
| created_at | timestamptz | |

**`message_feedback`** — Quality signals
| Column | Type | Notes |
|--------|------|-------|
| id | UUID (PK) | |
| message_id | UUID (FK → messages, unique) | One feedback per message |
| signal | integer | `1` (like) or `-1` (dislike) |

### Cascade Behavior

Deleting a **document** cascades to: `document_sections`, `threads` → `messages` → `message_feedback`.

---

## 8. Deployment & Infrastructure

### Production Architecture

```
┌─────────────────────────────────────────────────┐
│                    AZURE                         │
│                                                  │
│  Resource Group: pwc-rag-rg (West US)           │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │  Container Apps Environment: pwc-rag-env │    │
│  │                                          │    │
│  │  ┌────────────────────────────────┐     │    │
│  │  │  Container App: pwc-rag-api    │     │    │
│  │  │  - Image from ACR             │     │    │
│  │  │  - Port 8000                   │     │    │
│  │  │  - External ingress (HTTPS)    │     │    │
│  │  │  - Scale: 0–1 replicas        │     │    │
│  │  │  - 0.5 vCPU, 1GB RAM          │     │    │
│  │  └────────────────────────────────┘     │    │
│  └─────────────────────────────────────────┘    │
│                                                  │
│  Container Registry: cac2a1babdc1acr.azurecr.io │
│  Log Analytics: pwc-rag-logs                     │
│                                                  │
└─────────────────────────────────────────────────┘

┌──────────────────────┐
│       VERCEL          │
│                       │
│  Frontend: finrag.info│
│  (Next.js SSR/SSG)   │
└──────────────────────┘
```

### Why Azure Container Apps?

| Option | Overhead | Cost | Verdict |
|--------|----------|------|---------|
| **Container Apps** | Zero infra, auto-HTTPS, scale-to-zero | ~$0 idle | **Chosen** |
| App Service | Always-on plan required | ~$13+/mo min | More expensive |
| ACI | No auto-scaling, no HTTPS | Pay-per-second | Too manual |
| AKS | Full Kubernetes | Overkill | Way too much |

Container Apps was chosen because it provides **serverless containers** — the app scales to zero when idle (costs nothing), scales up on demand, and includes automatic HTTPS with no load balancer or certificate management.

### Key Infrastructure Details

- **Scale-to-zero**: Min replicas = 0. Cold starts take ~10-15 seconds when the app wakes from zero.
- **Auto-HTTPS**: Azure provides TLS termination at `*.azurecontainerapps.io`.
- **CORS**: Configured at the application level via `CORS_ORIGINS` env var. Currently allows `finrag.info`, `www.finrag.info`, the Vercel preview URL, and `localhost:3000`.

---

## 9. CI/CD Pipeline

### GitHub Actions Workflow

**Trigger**: Push to `main` that changes `backend/**` or the workflow file. Also supports manual trigger (`workflow_dispatch`).

```
Push to main (backend changes)
         │
         ▼
┌─────────────────────┐
│  Checkout code       │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  Login to ACR        │
│  (admin credentials) │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  Docker build & push │
│  Tags: {sha}, latest │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  az login            │
│  (service principal) │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  az containerapp     │
│  update              │
│  - New image         │
│  - Env vars          │
│  - Scale: 0–1       │
└──────────────────────┘
```

### Secrets (GitHub Repository)

| Secret | Purpose |
|--------|---------|
| `ACR_USERNAME` / `ACR_PASSWORD` | Push Docker images to Azure Container Registry |
| `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | Service principal for `az login` |
| `OPENAI_API_KEY` | Passed as env var to container |
| `PINECONE_API_KEY` | Passed as env var to container |
| `SUPABASE_URL` / `SUPABASE_KEY` | Passed as env var to container |
| `AZURE_DI_ENDPOINT` / `AZURE_DI_KEY` | Passed as env var to container |

### Frontend Deployment

The frontend is deployed on **Vercel** via its own git repo. Vercel auto-deploys on push to `main`. The only required env var is `NEXT_PUBLIC_API_URL` pointing to the backend Container App URL.

---

## 10. Local Development

### Prerequisites

- Python 3.11+
- Node.js 18+
- A `.env` file in `backend/` with all required keys

### Option 1: Docker Compose (recommended)

```bash
docker compose up
```

This starts both services:
- Backend on `http://localhost:8000` (with hot reload)
- Frontend on `http://localhost:3000` (with hot reload)

### Option 2: Run Separately

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

The frontend's `next.config.ts` proxies `/api/*` to `http://127.0.0.1:8000` in development, so no CORS configuration is needed locally.

### Useful Commands

```bash
# Health check
curl http://localhost:8000/health

# List threads
curl http://localhost:8000/api/threads

# Check document processing status
curl http://localhost:8000/api/documents/{id}/status

# View container logs (production)
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg --tail 50

# Manual deploy trigger
gh workflow run deploy-backend.yml --repo Syndicate555/finrag-backend
```

---

## 11. Design Decisions & Trade-offs

### Architecture Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **Separate frontend/backend repos** | Independent deploy cycles; Vercel for frontend, Azure for backend | Slightly more complex local setup |
| **SSE for chat streaming** | Simpler than WebSockets; works through CDNs and proxies; one-directional is sufficient | No bidirectional communication (not needed here) |
| **Pinecone over pgvector** | Purpose-built vector DB; no self-managed index tuning; sub-100ms queries | Additional external dependency and cost |
| **Supabase over raw PostgreSQL** | Managed Postgres + built-in object storage for PDFs + generous free tier | Vendor lock-in for storage API |
| **Azure Container Apps over Lambda/Cloud Run** | Runs a long-lived container (good for streaming); scale-to-zero; no cold start penalty for short requests | Azure ecosystem dependency |
| **pdfplumber fallback** | Not everyone has Azure DI; system works with zero Azure dependencies | Heuristic heading detection is less accurate |
| **Zustand + Context (not Redux)** | Minimal boilerplate; thread list is the only truly global state | Less structured than Redux for large teams |
| **Branded types** | Prevents ID mix-ups at compile time (`DocumentId` vs `ThreadId`) | Slightly more verbose type definitions |

### RAG-Specific Decisions

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| **512-token chunks with 64-token overlap** | Balance between context window usage and retrieval granularity | May split semantic units; overlap helps but isn't perfect |
| **top_k=20 retrieval** | Cast a wide net to find all relevant context | More tokens sent to LLM (higher cost per query) |
| **Separate table chunks** | Tables have different structure than prose; embedding them separately with section context improves retrieval | More chunks per document |
| **Query routing with GPT-4o-mini** | Cheap classification (~0.001 cents) saves expensive retrieval on general questions | Adds one LLM call of latency (~200ms) |
| **Store full chunk text in Pinecone metadata** | Avoids a database round-trip to reconstruct citations | Higher Pinecone storage cost |
| **temp=0.1 for KB, temp=0.3 for general** | Low temperature for factual document answers; slightly higher for general knowledge | KB answers may feel repetitive; general answers may vary |
| **Section-level clarification** | Prevents poor answers on ambiguous queries ("tell me about banking" — which section?) | Extra interaction step for the user |

### What's Intentionally Not Built

- **Authentication**: This is a single-tenant tool, not a multi-user SaaS. Adding auth would be straightforward (Supabase Auth or NextAuth).
- **Multi-document chat**: Each thread is tied to one document. Cross-document queries would require a different retrieval strategy.
- **Conversation memory**: Each query is independently retrieved. The LLM doesn't see previous messages. This keeps token costs predictable but limits follow-up questions.
- **Soft deletes**: Deleting a document or thread is permanent. No audit trail or recovery.

---

## 12. Environment Variables Reference

### Backend (`backend/.env`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for embeddings and chat |
| `PINECONE_API_KEY` | Yes | — | Pinecone API key |
| `PINECONE_INDEX_NAME` | No | `pwc-rag` | Pinecone index name |
| `SUPABASE_URL` | Yes | — | Supabase project URL |
| `SUPABASE_KEY` | Yes | — | Supabase service role key |
| `SUPABASE_BUCKET_NAME` | No | `pwc-rag` | Supabase storage bucket name |
| `AZURE_DI_ENABLED` | No | `false` | Enable Azure Document Intelligence |
| `AZURE_DI_ENDPOINT` | No | `""` | Azure DI endpoint URL |
| `AZURE_DI_KEY` | No | `""` | Azure DI API key |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | Allowed origins (JSON array) |
| `OPENAI_EMBEDDING_MODEL` | No | `text-embedding-3-large` | Embedding model |
| `OPENAI_CHAT_MODEL` | No | `gpt-4o` | Chat completion model |
| `OPENAI_ROUTER_MODEL` | No | `gpt-4o-mini` | Query classification model |
| `EMBEDDING_DIMENSIONS` | No | `1536` | Vector dimensions |
| `CHUNK_MAX_TOKENS` | No | `512` | Max tokens per chunk |
| `CHUNK_OVERLAP_TOKENS` | No | `64` | Overlap between chunks |
| `RETRIEVAL_TOP_K` | No | `20` | Number of chunks to retrieve |

### Frontend (`frontend/.env.local`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | Yes | — | Backend API base URL |

---

## 13. API Endpoints Reference

### Documents

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/documents/upload` | Upload PDF (multipart form, max 50MB) |
| `GET` | `/api/documents` | List all documents |
| `GET` | `/api/documents/{id}/status` | Get document processing status |
| `GET` | `/api/documents/{id}/sections` | Get document section hierarchy |
| `DELETE` | `/api/documents/{id}` | Delete document (cascades to vectors, threads) |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send message, get streamed SSE response |
| `POST` | `/api/chat/clarify` | Send clarification with selected section |

### Threads & Messages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/threads` | List all threads |
| `GET` | `/api/threads/{id}/messages` | Get messages for a thread |
| `DELETE` | `/api/threads/{id}` | Delete thread and messages |
| `PUT` | `/api/messages/{id}/feedback` | Submit feedback (like/dislike) |
| `DELETE` | `/api/messages/{id}/feedback` | Remove feedback |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check → `{"status": "ok"}` |
| `DELETE` | `/api/reset` | Factory reset (delete everything) |

### SSE Event Types (from `/api/chat`)

| Event | Data | When |
|-------|------|------|
| `thread_id` | UUID string | First message creates a new thread |
| `citations` | JSON array of citation objects | After retrieval, before LLM streaming |
| `token` | Text delta | Each token from GPT-4o stream |
| `clarification` | JSON array of section chips | When query needs clarification |
| `done` | Empty or final citations | Stream complete |

---

## 14. Common Operations

### Update CORS Origins

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg \
  --set-env-vars 'CORS_ORIGINS=["https://finrag.info","https://new-domain.com","http://localhost:3000"]'
```

### View Production Logs

```bash
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg --tail 100
```

### Force Redeploy

```bash
gh workflow run deploy-backend.yml --repo Syndicate555/finrag-backend
```

### Scale Up (Prevent Cold Starts)

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 1
```

### Scale Back to Zero

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 0
```

### Add a New Environment Variable

1. Add the secret to GitHub: `gh secret set NEW_VAR --body 'value' --repo Syndicate555/finrag-backend`
2. Add to the workflow's `--set-env-vars` in `deploy-backend.yml`
3. Add to `backend/app/config.py` Settings class
4. Push to trigger redeploy

### Database Migrations

1. Write SQL in `supabase/migration.sql` (or create a new migration file)
2. Run against Supabase via the dashboard SQL editor or `supabase db push`

### Rotate API Keys

1. Generate new key from the provider (OpenAI, Pinecone, etc.)
2. Update GitHub secret: `gh secret set OPENAI_API_KEY --body 'new-key' --repo Syndicate555/finrag-backend`
3. Trigger redeploy: `gh workflow run deploy-backend.yml`

---

*Last updated: 2026-02-27*
