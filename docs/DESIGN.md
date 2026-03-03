# FinRAG — Design Overview

## 1. Design Flow

FinRAG was designed around a single constraint: **a user uploads a financial PDF and gets accurate, cited answers via natural language**. Every architectural choice flows from that constraint.

### 1.1 Problem Decomposition

The problem breaks into two independent pipelines that share a vector store:

```
INGESTION (upload-time)                 RETRIEVAL (query-time)
───────────────────────                 ──────────────────────
PDF → Parse → Chunk → Embed → Store    Question → Route → Retrieve → Generate → Stream
```

Separating ingestion from retrieval means the expensive parsing and embedding work happens once per document, while queries run against pre-computed embeddings with sub-second latency.

### 1.2 Ingestion Pipeline

The ingestion pipeline converts an unstructured PDF into searchable vector embeddings:

1. **Parse** — Extract text, tables, headings, and section hierarchy from the PDF. Azure Document Intelligence handles this when available; pdfplumber provides a zero-cost fallback.
2. **Chunk** — Split the parsed content into 512-token windows with 64-token overlap. Tables are preserved as whole chunks to avoid splitting rows.
3. **Embed** — Convert each chunk into a 1536-dimensional vector using OpenAI's `text-embedding-3-large`. Chunks are batched (100 per API call) to minimize round trips.
4. **Store** — Upsert vectors to Pinecone with metadata (document ID, section heading, page range, full chunk text). Save document metadata and section hierarchy to Supabase.

The full chunk text is stored in Pinecone metadata to avoid a second database lookup at query time — a deliberate denormalization that trades storage for latency.

### 1.3 Retrieval Pipeline

When a user asks a question, the system follows a three-stage flow:

1. **Route** — A lightweight GPT-4o-mini classifier determines the query intent:
   - **Knowledge Base** — Question requires document retrieval (e.g., "What was BMO's net income?")
   - **General** — Question can be answered without retrieval (e.g., "What does CET1 ratio mean?")
   - **Needs Clarification** — Question is ambiguous and the system suggests specific document sections

2. **Retrieve** — For knowledge base queries, the user's question is embedded and matched against the vector store (Pinecone, top-k=20). Results include the chunk text, section heading, page range, and relevance score.

3. **Generate & Stream** — Retrieved chunks are formatted as context and passed to GPT-4o. The response streams to the client via Server-Sent Events in a defined sequence: `thread_id → citations → token (repeated) → done`. Citations arrive before the first token so the frontend can render source badges immediately.

### 1.4 Feedback Loop

Every assistant message supports thumbs up/down feedback stored in Supabase. This creates a ground-truth signal for measuring answer quality over time and identifying weak spots (e.g., questions about tables consistently getting downvoted would indicate a chunking or parsing issue).

---

## 2. Key Decisions

### 2.1 OpenAI API over Azure OpenAI

Azure OpenAI requires provisioning a resource group, creating a deployment per model, and waiting for model access approval. OpenAI's API provides instant access to the latest models with a single API key. Since the system uses three models (GPT-4o for generation, GPT-4o-mini for routing, text-embedding-3-large for embeddings), managing three separate Azure deployments adds operational overhead with no functional benefit at this scale. The OpenAI client's `base_url` parameter makes migration to Azure OpenAI a one-line change if enterprise compliance requires it.

### 2.2 Pinecone Serverless over Azure AI Search

Azure AI Search requires creating a resource, defining an index schema, configuring skillsets, and selecting a pricing tier (minimum ~$250/month for Basic). Pinecone Serverless provides a managed vector index with a free tier (up to 100K vectors), native metadata filtering, and automatic scaling. For a document Q&A system that filters by `document_id` and optionally by `section_heading`, Pinecone's metadata filters are sufficient — Azure AI Search's full-text + semantic hybrid search is powerful but unnecessary when the embedding model already handles semantic matching. If hybrid search becomes a requirement, the vector store is behind a single adapter (`pinecone_store.py`) and can be swapped.

### 2.3 Supabase over Azure PostgreSQL + Blob Storage

Supabase provides PostgreSQL, object storage, and a client SDK in a single service. Azure would require provisioning Azure Database for PostgreSQL (no free tier) and Azure Blob Storage separately, plus managing two connection strings and two client libraries. Supabase's free tier (500MB database, 1GB storage) is sufficient for this use case. The trade-off is that Supabase is not Azure-native, which matters for enterprise deployments that mandate single-cloud — but the database schema and storage API are standard enough that migration is straightforward.

### 2.4 SSE over WebSockets

LLM token streaming is unidirectional: the server sends tokens to the client. WebSockets add bidirectional complexity (connection management, heartbeats, reconnection, sticky sessions) that provides no benefit here. SSE works over standard HTTP, auto-reconnects natively in browsers, and passes through CDNs and load balancers without special configuration. User messages are sent via standard POST requests — there is no need for a persistent bidirectional channel.

### 2.5 GPT-4o-mini for Query Routing

Not every question needs document retrieval. "What does EBITDA stand for?" can be answered directly by the LLM, saving the cost and latency of embedding + vector search + context-augmented generation. A single GPT-4o-mini classification call costs ~$0.00002 and adds ~80ms of latency — negligible compared to the retrieval pipeline. The router also enables a third path (clarification) that improves answer quality by narrowing ambiguous queries to specific document sections before retrieval.

### 2.6 Token-Based Chunking (512/64) over Semantic Chunking

Semantic chunking uses an embedding model to detect topic boundaries, which introduces non-determinism (different runs may produce different chunks) and requires embedding calls during ingestion. Token-based chunking with structural awareness — the parser first extracts headings and tables, then the chunker splits within sections using fixed 512-token windows with 64-token overlap — is fully deterministic, reproducible, and testable. Tables are never split, preserving row/column relationships. The 512-token chunk size was chosen to balance retrieval granularity (smaller chunks are more precise) with context completeness (larger chunks provide more surrounding information).

### 2.7 Azure Document Intelligence with pdfplumber Fallback

Financial PDFs contain complex tables, multi-column layouts, and section hierarchies that simple text extraction misses. Azure Document Intelligence uses vision models to understand document structure — extracting tables as structured data, identifying headings by visual hierarchy, and preserving reading order. However, Azure DI has a free-tier page limit and may be unavailable in some environments. pdfplumber provides a fallback that uses font-size heuristics to detect headings and extracts tables via line detection. The system tries Azure DI first and falls back to pdfplumber on failure, ensuring the pipeline always completes.

### 2.8 Storing Full Chunk Text in Pinecone Metadata

When the retrieval pipeline returns the top-k results from Pinecone, the response must include the chunk text for both the LLM context and the user-facing citations. If the text were stored only in Supabase, every query would require a second database round trip to fetch 20 chunk texts by ID. Storing the full text as Pinecone metadata eliminates this lookup entirely. The trade-off is increased vector storage cost, but at ~500 bytes per chunk, a 100-page document with ~200 chunks adds ~100KB — negligible.

### 2.9 Azure Container Apps over AWS Lambda / Google Cloud Run

The backend uses SSE streaming, which requires long-lived HTTP connections (30-60 seconds per response). AWS Lambda has a 30-second timeout and does not natively support SSE streaming. Google Cloud Run supports streaming but requires explicit configuration. Azure Container Apps natively supports long-lived connections, provides scale-to-zero (cost ~$0 when idle), auto-HTTPS with custom domains, and integrates with Azure Container Registry for CI/CD. The 0–1 replica configuration means the first request after idle has a cold start (~10 seconds), which is an acceptable trade-off for near-zero idle cost.

---

## 3. Evaluation Approach

Evaluation follows two complementary strategies: **offline evaluation** measures retrieval and generation quality against a static benchmark, while **online evaluation** monitors production behavior through user feedback and telemetry.

### 3.1 Offline Evaluation

Offline evaluation uses [RAGAS](https://docs.ragas.io/) to measure four dimensions of RAG quality against a hand-crafted benchmark dataset.

**Benchmark Dataset**

15 question-answer pairs sourced directly from the BMO Annual Report 2025 — Management's Discussion and Analysis. The questions are distributed across five categories to stress-test different system capabilities:

| Category | Count | Tests |
|---|---|---|
| Factual | 5 | Single-fact extraction (e.g., "What was BMO's net income?") |
| Tabular | 3 | Data from tables and figures (e.g., "What was the net interest margin for Canadian P&C?") |
| Multi-section | 3 | Synthesis across document sections (e.g., "How did the acquisition impact U.S. P&C and credit risk?") |
| Comparison | 2 | Year-over-year comparisons (e.g., "How did net income change from 2023 to 2024?") |
| Edge cases | 2 | Out-of-scope or unanswerable questions (e.g., "What is BMO's current stock price?") |

Each pair includes a ground truth answer written by reading the source document, ensuring the evaluation oracle is independent of the system.

**Metrics**

| Metric | What It Measures | Diagnostic Value |
|---|---|---|
| **Faithfulness** | Is the answer grounded in the retrieved context? (0–1) | Low score → the LLM is hallucinating beyond what the retrieved chunks support. Fix: tighten the system prompt constraints or reduce temperature. |
| **Answer Relevancy** | Does the answer address the question? (0–1) | Low score → the model is generating tangential content. Fix: refine the prompt template to focus on the query. |
| **Context Precision** | Are relevant chunks ranked higher in the retrieval results? (0–1) | Low score → the embedding model or vector search is returning noisy results. Fix: add re-ranking, tune top-k, or improve chunk boundaries. |
| **Context Recall** | Do the retrieved chunks cover all claims in the ground truth? (0–1) | Low score → the retrieval is missing relevant content. Fix: increase top-k, adjust chunk size/overlap, or improve section-level metadata filtering. |

**Execution**

The evaluation runs as a Jupyter notebook (`docs/evaluation.ipynb`) that:
1. Calls the live API (`POST /api/chat` via SSE) for each benchmark question
2. Parses the SSE stream to extract the full answer and retrieved citation contexts
3. Passes the questions, answers, contexts, and ground truths to the RAGAS `evaluate()` function
4. Produces per-question scores, aggregate scores, and scores grouped by question type

This approach tests the full end-to-end system (routing → retrieval → generation → streaming) rather than individual components in isolation, ensuring the evaluation reflects real user experience.

**Interpreting Results**

Results are analyzed at two levels:

- **Aggregate scores** reveal overall system quality. A target of ≥0.8 across all four metrics indicates production-ready RAG quality.
- **Scores by question type** expose specific weaknesses. For example, low context recall on tabular questions would indicate that table chunking or table-to-markdown conversion needs improvement, while low faithfulness on multi-section questions would suggest the model struggles to synthesize across multiple context chunks.

### 3.2 Online Evaluation

Online evaluation monitors the system in production using three signals.

**User Feedback**

Every assistant message supports thumbs up (+1) or thumbs down (-1) feedback, stored in the `message_feedback` table. Key metrics:

- **Satisfaction rate** = `likes / total feedback` — target ≥80%
- **Feedback coverage** = `feedback count / assistant message count` — measures user engagement with the feedback mechanism
- **Dislike clustering** — grouping dislikes by `message_type` (kb / general / clarification) and document section identifies systematic failure patterns rather than isolated bad answers

**Application Telemetry**

The backend integrates Azure Application Insights via OpenTelemetry, which auto-instruments HTTP requests and dependency calls:

| Signal | What It Captures |
|---|---|
| Request latency (p50/p95/p99) | End-to-end response time including streaming |
| Error rate (4xx/5xx) | API reliability |
| Dependency latency | Per-service breakdown: OpenAI, Pinecone, Supabase response times |
| Cold start frequency | How often scale-to-zero triggers a slow first request |

Alerting rules (recommended for production):
- P95 latency >10s → investigate retrieval or LLM bottleneck
- Error rate >5% over 5 minutes → page on-call
- Dislike rate >30% over 1 hour → review recent queries for pattern

**Cost Monitoring**

Per-query cost is dominated by GPT-4o generation (~$0.005–0.02 per query). The cost model:

| Component | Estimated Cost per Query |
|---|---|
| Query routing (GPT-4o-mini) | ~$0.00002 |
| Query embedding (text-embedding-3-large) | ~$0.00013 |
| Vector search (Pinecone) | ~$0.000008 |
| Response generation (GPT-4o) | ~$0.005–0.02 |
| **Total** | **~$0.005–0.02** |

Cost is tracked via the OpenAI usage dashboard and Pinecone console, with billing alerts set at $50, $100, and $200 thresholds.

### 3.3 Continuous Improvement

Offline and online evaluation feed into a continuous improvement loop:

```
User queries → System responses → Feedback + Telemetry
                                         │
                    ┌────────────────────┘
                    ▼
            Identify weakness
            (low metric or high dislike rate)
                    │
                    ▼
            Diagnose root cause
            (retrieval? generation? parsing?)
                    │
                    ▼
            Adjust parameters
            (top-k, chunk size, prompts, re-ranking)
                    │
                    ▼
            Re-run offline benchmark
            (verify improvement, check for regressions)
                    │
                    ▼
            Deploy → monitor online metrics
```

Specific triggers and actions:

| Trigger | Root Cause | Action |
|---|---|---|
| Low faithfulness | LLM hallucinating beyond context | Tighten system prompt; reduce temperature |
| Low context recall | Retrieval missing relevant chunks | Increase top-k; reduce chunk size; add overlap |
| Low context precision | Irrelevant chunks ranked high | Add cross-encoder re-ranking; tune embedding model |
| Low answer relevancy | Model not addressing the question | Refine prompt template; improve query routing |
| High dislike rate on tabular questions | Table parsing quality | Improve Azure DI table extraction; fix markdown conversion |
| High latency (p95 >10s) | Slow retrieval or generation | Reduce top-k; cache frequent queries; optimize prompts |
