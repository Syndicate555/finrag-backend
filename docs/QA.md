# FinRAG — Interview Q&A

## 1. Architecture Decisions

### Why did you choose Next.js for the front end?

Three reasons specific to this project:

**Server-Side Rendering for the landing page.** FinRAG has a marketing landing page at `finrag.info` and an interactive chat application behind it. Next.js 16 with the App Router lets me server-render the landing page for fast initial load and SEO, while the chat interface (`/chat/[threadId]`) runs as a fully client-side SPA with Zustand for state management. With a framework like plain React + Vite, I'd need a separate solution for the landing page or sacrifice SSR entirely.

**API route rewrites for CORS simplification.** The Next.js frontend at `finrag.info` talks to the FastAPI backend on Azure Container Apps at a completely different domain. Instead of dealing with complex CORS configurations in production, I use Next.js rewrites in `next.config.ts` to proxy `/api/*` requests from the frontend domain to the backend. The browser thinks it's talking to the same origin — no CORS preflight requests, no `Access-Control-Allow-Origin` header management.

**React 19 with the React Compiler.** I enabled the React Compiler in `next.config.ts`, which automatically memoizes components and eliminates unnecessary re-renders. This matters for the chat interface where every streamed token from the LLM triggers a state update — without memoization, the entire message list would re-render on every token. The compiler handles this without me manually wrapping everything in `React.memo` and `useMemo`.

I also use Zustand over Redux for state management because the only truly global state is the thread list in the sidebar. Everything else — streaming content, citations, clarification chips — is local to the chat component. Zustand's 2KB footprint and zero-boilerplate API was the right fit for that scope.

---

### What made you select FastAPI and Python for the back end?

**The AI/ML ecosystem is Python-native.** Every service I integrate with — OpenAI SDK, Pinecone client, Azure Document Intelligence, tiktoken for token counting — has a first-class Python SDK. The OpenAI Python SDK provides native async streaming with `AsyncOpenAI`, which maps directly to FastAPI's async route handlers. If I used Node.js or Go, I'd be working with community-maintained wrappers or raw HTTP clients for most of these services.

**FastAPI's async-first design matches the workload.** A single chat request touches four external services sequentially: GPT-4o-mini for routing, OpenAI embeddings for the query vector, Pinecone for retrieval, and GPT-4o for generation. With FastAPI's native `async/await`, these I/O-bound operations don't block the event loop. The SSE streaming response uses `EventSourceResponse` from `sse-starlette`, which yields tokens as an async generator — this is ergonomic in Python and would be significantly more complex in a compiled language.

**Pydantic for request validation and configuration.** FastAPI's integration with Pydantic gives me typed request/response schemas (`ChatRequest`, `DocumentResponse`, `Citation`) and typed configuration via `pydantic-settings`. All environment variables are validated at startup — if `OPENAI_API_KEY` is missing, the app fails fast with a clear error instead of crashing on the first API call. I also use `NewType` for branded IDs (`ThreadId`, `DocumentId`, `MessageId`) to prevent ID mix-ups at the type level.

**Practical consideration:** Python's startup time is slower than Go or Rust, which affects cold starts on Azure Container Apps (roughly 8-10 seconds). But for a document Q&A system where the bottleneck is LLM generation latency (2-5 seconds per response), shaving 200ms off the framework overhead with a compiled language doesn't meaningfully improve user experience.

---

### Why use Pinecone as your vector database instead of alternatives like FAISS or Weaviate?

I evaluated four options: Pinecone Serverless, FAISS, Weaviate, and pgvector.

**FAISS was eliminated first.** FAISS is an in-memory library, not a database. It has no persistence, no metadata filtering, and no API — I'd need to serialize indexes to disk, manage loading/unloading, and build a query service around it. For a production system where documents are uploaded dynamically and need to be immediately searchable, FAISS adds significant infrastructure burden. It's the right choice for offline batch processing or research, not for a live application.

**pgvector was tempting since I'm already on Supabase (PostgreSQL).** But pgvector stores vectors in the same database as application data, which means vector similarity searches compete for resources with CRUD operations. At scale, a heavy retrieval query could slow down thread creation or message persistence. Separation of concerns applies to data stores too — I want the vector index to scale independently from the application database. pgvector also lacks Pinecone's metadata filtering performance, which I rely on for `document_id` scoping and `section_heading` filtering during clarification queries.

**Weaviate is a strong option** with built-in vectorization and hybrid search. But it requires self-hosting or using Weaviate Cloud, which adds another managed service with its own scaling configuration. Weaviate's schema management (collections, properties, vectorizers) is more complex than what I need — I'm storing a single type of object (document chunks) with simple metadata filters.

**Pinecone Serverless won because:**
1. Zero infrastructure management — no clusters, no replicas, no index tuning
2. Native metadata filtering — I filter by `document_id` on every query, and optionally by `section_heading` for clarification flows. This is a first-class feature in Pinecone, not a post-filter hack
3. Free tier covers 100K vectors — a 100-page financial PDF produces roughly 200 chunks, so the free tier supports ~500 documents
4. Sub-100ms query latency — Pinecone Serverless in AWS us-east-1 consistently returns results in 30-80ms
5. The entire vector store interaction is behind a single adapter (`pinecone_store.py` — 65 lines), so migration to Weaviate or Azure AI Search is a one-file change

---

### Can you explain your reasoning for using OpenAI embeddings and GPT-4o-mini versus other models, and how you route queries?

**Embedding model: text-embedding-3-large at 1536 dimensions.**

I chose this over alternatives like Cohere embed-v3 or open-source models (e5-large, BGE) for three reasons. First, it scores at the top of the MTEB benchmark for retrieval tasks, which directly measures what I need — matching user questions to relevant document chunks. Second, it supports dimensionality reduction — I can generate at 1536 dims (default) or reduce to 512 or 256 with minimal quality loss, which gives me a tuning knob for cost/quality trade-offs later. Third, keeping embeddings and generation on the same provider simplifies API key management, billing, and rate limit monitoring.

The 1536-dimension output is stored in Pinecone. At ~6KB per vector, a 100-page document with 200 chunks uses roughly 1.2MB of vector storage — well within Pinecone's free tier.

**Routing model: GPT-4o-mini.**

The query router is the most cost-critical decision in the architecture. Every user message hits the router first, so it must be cheap and fast. GPT-4o-mini costs $0.15 per million input tokens versus GPT-4o at $2.50 — roughly 17x cheaper. For classification (outputting one of three labels: `kb`, `general`, or `needs_clarification`), GPT-4o-mini performs identically to GPT-4o. I set `temperature=0` and `max_tokens=20` to make it deterministic and fast (~80ms per classification).

**How routing works:**

The router receives the user's message and a system prompt that defines three categories:

- **`kb`** — The question requires information from the uploaded document. Examples: "What was BMO's net income?", "Show me the provision for credit losses."
- **`general`** — The question can be answered from general knowledge without document retrieval. Examples: "What does CET1 ratio mean?", "Explain IFRS 9."
- **`needs_clarification`** — The question is too broad or ambiguous to retrieve effectively. Examples: "Tell me about banking", "What are the risks?"

When the router returns `needs_clarification`, the system queries the `document_sections` table in Supabase, scores each section's relevance to the query using keyword matching, and returns the top sections as clickable chips. The user selects a section, and the follow-up query filters Pinecone retrieval to only chunks from that section — dramatically improving precision.

This three-way routing saves approximately 40-60% on OpenAI API costs compared to running every query through the full retrieval pipeline, because general knowledge questions skip embedding, vector search, and the larger context window entirely.

**Generation model: GPT-4o.**

For the actual answer generation with retrieved context, I use GPT-4o. Financial documents contain nuanced language — provisions, adjustments, restatements — and GPT-4o handles multi-hop reasoning across multiple retrieved chunks better than smaller models. I stream the response via SSE so the user sees tokens immediately rather than waiting 3-5 seconds for the full response.

---

### Why did you deploy the backend on Azure Container Apps and the front end on Vercel?

**Backend on Azure Container Apps:**

The primary constraint is SSE streaming. When a user asks a question, the backend maintains an open HTTP connection for 10-30 seconds while GPT-4o generates the response token by token. This eliminates AWS Lambda (30-second hard timeout, no native streaming support) and makes Google Cloud Run workable but not ideal (requires explicit streaming configuration).

Azure Container Apps provides:
1. **Native long-lived connection support** — No timeout issues with SSE streaming
2. **Scale-to-zero** — The backend scales to 0 replicas when idle. For a take-home project, this means ~$0/month when not in use, versus a minimum $7/month for an always-on Cloud Run service
3. **Auto-HTTPS with custom domains** — TLS termination handled automatically
4. **Container Apps Environment** includes built-in Log Analytics integration, so I get request logs and diagnostics without configuring a separate logging stack
5. **Azure Container Registry integration** — The CI/CD pipeline builds a Docker image, pushes to ACR, and triggers a revision update in a single workflow

The scaling configuration is 0 minimum, 3 maximum replicas, with HTTP-based autoscaling. The first request after idle incurs a cold start (~10 seconds), but subsequent requests are sub-100ms to the backend. For production, I'd set minimum replicas to 1 to eliminate cold starts.

**Frontend on Vercel:**

Vercel is the canonical deployment platform for Next.js — it's built by the same team. Specific advantages:
1. **Edge network** — Static assets and server-rendered pages are served from the edge location closest to the user. The landing page loads in under 1 second globally.
2. **Zero-config deployments** — Push to `main` and Vercel builds and deploys in ~30 seconds. No Dockerfile, no build configuration.
3. **Preview deployments** — Every PR gets a unique preview URL, which is useful for design review.
4. **API route rewrites** — I configure Next.js to rewrite `/api/*` to the Azure backend, which means the frontend and backend appear to be on the same domain from the browser's perspective.

The alternative would be deploying the Next.js frontend as a Docker container on Azure Container Apps alongside the backend. But that means managing Node.js container builds, configuring a CDN manually (Azure Front Door), and losing Vercel's edge rendering. For a Next.js app, Vercel is simply the lowest-friction option.

---

## 2. Document Parsing and Data Processing

### How does Azure Document Intelligence integrate into your pipeline?

Azure Document Intelligence is the first stage of the ingestion pipeline. When a user uploads a PDF, the backend triggers a background task that:

1. **Writes the PDF to a temporary file** and sends it to Azure DI's `prebuilt-layout` model
2. **Azure DI returns structured output**: paragraphs with their roles (title, sectionHeading, pageHeader, pageFooter, footnote), tables with row/column structure, and reading order across pages
3. **I transform this output** into a `ParsedDocument` — a list of content blocks where each block is either text (with its section heading and page range) or a table (converted to Markdown format with pipes and dashes)
4. **Section hierarchy is extracted**: Level-1 headings (Azure DI role `title`) and level-2 headings (`sectionHeading`) are stored in the `document_sections` table with parent-child relationships. This hierarchy powers the clarification flow — when a user's query is ambiguous, the system suggests these sections.

**The fallback is critical.** Azure DI has a free tier limit (500 pages/month) and may fail on certain PDF formats. If Azure DI fails or returns incomplete results (less than 50% of pages parsed — which happens when the tier limit is hit mid-document), the pipeline falls back to pdfplumber. pdfplumber uses font-size heuristics to detect headings: it analyzes character-level font sizes on each page, identifies the largest fonts as headings, and groups content into sections. Tables are detected via pdfplumber's line-based table extraction. This fallback ensures the pipeline always completes, even without Azure DI access.

**Why Azure DI over simpler alternatives like PyPDF2 or tika?** Financial documents have complex layouts — multi-column tables, footnotes that span pages, nested section hierarchies. PyPDF2 extracts raw text with no structural understanding. Azure DI uses a vision model that sees the document as a human would — it understands that a table spanning two pages is one table, that a footnote at the bottom of page 12 relates to content on page 12, and that "Management's Discussion and Analysis" is a section heading because of its visual prominence, not just its font size.

---

### What challenges did you face segmenting large documents into chunks?

Three specific challenges:

**1. Tables must not be split.**

A financial table like an income statement has rows that only make sense together. If I split "Revenue: $30,548M" into one chunk and "Net Income: $5,952M" into the next, a query about profitability would only retrieve half the picture. My chunker treats every table as an atomic unit — it's either included whole or not at all. Azure DI extracts tables as structured objects, and I convert them to Markdown format before chunking. If a table exceeds the 512-token limit, it still stays as one chunk because splitting it would destroy its meaning. This means some chunks are larger than 512 tokens, which is an intentional trade-off.

**2. Section boundaries must be respected.**

A chunk should not span two unrelated sections. If the end of "Credit Risk" and the beginning of "Market Risk" fall within the same 512-token window, retrieval for a credit risk question would pull in irrelevant market risk content. The chunker processes each section independently — it receives text blocks grouped by section heading and chunks within those boundaries. An overlap window can reach back into the same section's previous chunk, but never across sections.

**3. Sentence splitting in financial text is harder than general text.**

Financial documents are dense with decimals ("$3.14 billion"), abbreviations ("Dr.", "Inc.", "Corp."), and percentage notation ("increased 36.2% year-over-year"). A naive split on `.!?` characters would break "$3.14 billion" into two fragments. I use a sentence-boundary detection approach that considers the character following the period — if it's a digit, lowercase letter, or known abbreviation, it's not a sentence boundary. This prevents mid-number splits that would corrupt financial data.

---

### How do you ensure that the 512-token chunking with overlapping tokens preserves context?

The 64-token overlap serves a specific purpose: it ensures that information near chunk boundaries is retrievable from either adjacent chunk. Here's how it works mechanically and why the parameters were chosen:

**Mechanics:** When the chunker finishes a 512-token chunk, it looks back at the last 64 tokens (approximately 1-2 sentences) and includes them at the beginning of the next chunk. This means a sentence that falls at position 480-520 in the text — which would be split between chunks without overlap — is now fully present in both chunk N and chunk N+1. A user query that matches that sentence will retrieve at least one of the two chunks.

**Why 512/64 specifically:**
- **512 tokens** ≈ 380 words ≈ 1.5-2 paragraphs. This is large enough to contain a complete thought (e.g., a full discussion of a metric with context) but small enough that the retrieval is precise. Larger chunks (1024 tokens) improve context completeness but reduce precision — a 1024-token chunk about credit risk might also contain unrelated operational risk discussion, diluting the embedding signal.
- **64 tokens** = 12.5% overlap ratio. This is the minimum overlap that reliably captures cross-boundary sentences. I tested with 32 tokens (too small — sentences were still split) and 128 tokens (wasteful — 25% of each chunk was duplicate content, inflating the vector store and retrieval cost with no measurable quality improvement).

**Token counting uses tiktoken with the GPT-4o encoding** (`cl100k_base`), ensuring the token counts match what the generation model will actually consume. This prevents the edge case where a chunk appears to be 512 tokens by one counting method but exceeds the model's context window by another.

**Validation:** I can verify that context is preserved by running the offline RAGAS evaluation. The **context recall** metric specifically measures whether the retrieved chunks contain all the information needed to answer the question. If overlap were insufficient, context recall would drop on questions whose answers span chunk boundaries — particularly comparison questions like "How did net income change from 2023 to 2024?" where the two years' figures might be in adjacent chunks.

---

## 3. Handling Queries

### How do you differentiate between knowledge-based questions (answers in the document) and out-of-knowledge questions?

The query router is a GPT-4o-mini classifier with a structured system prompt that defines three categories with explicit examples:

```
You are a query classifier for a financial document Q&A system.
Classify the user's query into exactly one category:

- "kb": The question asks about specific data, metrics, or content
  that would be found in a financial document (annual reports, MD&A,
  earnings releases). Examples: revenue figures, risk factors,
  management commentary, financial ratios.

- "general": The question asks about general financial knowledge,
  definitions, or concepts that don't require document-specific data.
  Examples: "What is EBITDA?", "Explain Basel III requirements."

- "needs_clarification": The question is too broad or ambiguous to
  retrieve effectively. Examples: "Tell me about the company",
  "What are the risks?", "Summarize everything."

Respond with only the category label.
```

The classifier runs at `temperature=0` for deterministic output and `max_tokens=20` since the response is a single word.

**For out-of-scope questions** — questions that are neither in the document nor general financial knowledge (e.g., "What's the weather today?", "What were TD Bank's earnings?") — the system still routes through the `kb` path, attempts retrieval, and gets low-relevance chunks. The generation model's system prompt instructs it to say "I don't have information about that in the uploaded document" when the retrieved context doesn't contain relevant information. This is preferable to adding a fourth routing category because the model is better at judging relevance with actual retrieved context than the router is at predicting relevance without it.

**The clarification path** is what differentiates this system from most RAG implementations. When a user asks "Tell me about risk management," that query would match dozens of chunks across credit risk, market risk, operational risk, and liquidity risk sections — resulting in a noisy, unfocused answer. Instead, the router classifies this as `needs_clarification`, the system fetches the document's section hierarchy from Supabase, scores each section's relevance to the query, and returns the top sections as interactive chips: "Credit Risk", "Market Risk", "Operational Risk", "Liquidity Risk". The user clicks one, and the follow-up query filters Pinecone retrieval to only that section's chunks — producing a precise, focused answer.

---

### How are citations generated, and how do you ensure accuracy?

Citations are generated during the retrieval stage, before any text generation begins. Here's the exact flow:

1. **Pinecone returns top-k=20 results**, each with metadata including `page_start`, `page_end`, `section_heading`, `relevance_score`, and `chunk_text`.

2. **The RAG pipeline deduplicates citations** by the tuple `(page_start, page_end, section_heading)`. If five chunks all come from pages 45-47 of the "Credit Risk" section, only one citation is created. This prevents the UI from showing five identical "Pages 45-47: Credit Risk" badges.

3. **Citations are sent to the client BEFORE the first generated token.** The SSE event sequence is: `thread_id → citations → token token token... → done`. This means the frontend renders citation badges (page range, section heading, relevance score) immediately, while the answer streams in below them. The user can click a citation to see the source text before the answer even finishes generating.

4. **The citation's `chunk_text` is truncated to 200 characters** for the UI preview, but the full text is available on click.

**Accuracy is ensured at three levels:**

**Retrieval accuracy:** Every citation comes directly from a chunk that was embedded from the actual document. The chunk text stored in Pinecone metadata is the literal text extracted from the PDF — not generated, not paraphrased. The `page_start` and `page_end` values are set during ingestion based on the PDF page numbers where that text appears.

**Generation grounding:** The system prompt for GPT-4o explicitly instructs: "Base your answer only on the provided context. If the context does not contain enough information to answer the question, say so. Do not fabricate information." The RAGAS **faithfulness** metric measures whether the generated answer is actually grounded in the retrieved context — a low faithfulness score would indicate the model is hallucinating beyond what the citations support.

**User verification:** Citations include page ranges so the user can cross-reference with the original PDF, which is stored in Supabase Object Storage and accessible through the frontend's PDF viewer. This closes the trust loop — the system says "Revenue was $30,548M (Pages 12-13, Financial Results)", and the user can click to verify on the actual document page.

---

### What's your evaluation framework for measuring the system's performance on both types of queries?

The evaluation framework has two layers: **offline evaluation** for systematic quality measurement, and **online evaluation** for production monitoring.

**Offline Evaluation (RAGAS)**

I maintain a benchmark dataset of 15 hand-crafted question-answer pairs sourced from the BMO Annual Report 2025 MDA, distributed across five categories:

- **Factual (5):** Single-fact extraction — "What was BMO's net income for fiscal 2024?" Ground truth: "$5,952 million"
- **Tabular (3):** Data from tables — "What was the net interest margin for Canadian P&C?" Ground truth: "2.67%"
- **Multi-section (3):** Requires synthesizing across sections — "How did the acquisition impact U.S. P&C performance and credit risk?"
- **Comparison (2):** Year-over-year comparisons — "How did net income change from 2023 to 2024?"
- **Edge cases (2):** Out-of-scope questions — "What is BMO's current stock price?" (not in document), "What were TD Bank's earnings?" (wrong company)

For each question, I call the live API via SSE, parse the full answer and retrieved contexts, and pass them to RAGAS which computes four metrics:

| Metric | What It Catches |
|---|---|
| **Faithfulness** | Is the answer making claims not supported by retrieved chunks? |
| **Answer Relevancy** | Is the answer actually addressing what was asked? |
| **Context Precision** | Are the most relevant chunks ranked highest in retrieval? |
| **Context Recall** | Did retrieval find all chunks needed to answer the question? |

I analyze results by question type. If tabular questions score low on context recall, it means table chunks aren't being retrieved effectively — pointing to a chunking or embedding issue. If edge cases score low on faithfulness, it means the model is hallucinating answers instead of saying "I don't know."

**Online Evaluation**

Three signals in production:

1. **User feedback:** Every assistant message has thumbs up/down. I track satisfaction rate (`likes / total feedback` — target ≥80%) and cluster dislikes by `message_type` (kb/general/clarification) to identify systematic issues.

2. **Telemetry via Azure Application Insights:** Auto-instrumented HTTP spans give me p50/p95/p99 latency, error rates, and dependency latency breakdowns (how much time is spent on OpenAI vs Pinecone vs Supabase). Alert if p95 >10s or error rate >5%.

3. **Cost tracking:** Per-query cost is ~$0.005-0.02, dominated by GPT-4o generation. I monitor via the OpenAI usage dashboard with billing alerts at $50, $100, $200 thresholds.

**For general knowledge queries** (routed to the `general` path), faithfulness and context metrics don't apply since there's no retrieval. I evaluate these through answer relevancy only, plus user feedback. The expected behavior is a correct, concise explanation without hallucinated specifics — "CET1 ratio measures a bank's core equity capital as a percentage of risk-weighted assets" is correct; "BMO's CET1 ratio is 15%" would be a hallucination (the actual figure should come from the document, not general knowledge).

---

## 4. Trade-offs and Improvements

### What trade-offs did you consider when choosing models and cloud services, for cost, latency, and accuracy?

**Model trade-offs:**

| Decision | Cost Impact | Latency Impact | Accuracy Impact |
|---|---|---|---|
| GPT-4o-mini for routing instead of GPT-4o | 17x cheaper per classification | 80ms faster (smaller model) | No measurable difference for 3-class classification |
| GPT-4o for generation instead of GPT-4o-mini | ~10x more expensive per query | ~500ms slower (larger model) | Significantly better at multi-hop reasoning over financial context |
| text-embedding-3-large at 1536 dims instead of 3072 | 50% less vector storage | Identical query latency | Minimal quality loss (MTEB score difference <1%) |
| top-k=20 retrieval instead of top-k=5 | 4x more input tokens to GPT-4o | ~200ms more context processing | Much better recall — financial answers often need context from multiple sections |

The key insight is that **routing is the biggest cost lever**. By classifying queries with GPT-4o-mini first, I skip the entire retrieval + generation pipeline for general knowledge questions. In my testing, roughly 30-40% of questions about financial documents are definitional or conceptual ("What is a provision for credit losses?") — routing these to a direct GPT-4o response without retrieval saves both the embedding call, the Pinecone query, and the larger context window.

**Cloud service trade-offs:**

- **Azure Container Apps (scale-to-zero):** Saves ~$50/month in idle costs, but cold starts add 8-10 seconds on the first request. For a demo or low-traffic deployment, this is acceptable. For production with SLA requirements, I'd set `min-replicas=1` (~$30/month) to eliminate cold starts.
- **Pinecone Serverless over self-hosted Weaviate:** I give up hybrid search (keyword + semantic) and cross-encoder re-ranking in exchange for zero operational overhead. If retrieval precision becomes a bottleneck, I'd add a Cohere re-ranker as a post-retrieval step rather than switching vector databases.
- **Supabase over Azure PostgreSQL:** I get object storage bundled with PostgreSQL, saving one Azure resource and one connection string. The trade-off is that Supabase is outside the Azure VNet, so database traffic crosses the public internet (encrypted, but not private-linked).

---

### How would you scale the system if the document size or number of users increases significantly?

**Scaling for document volume (100s → 10,000s of documents):**

1. **Vector store:** Pinecone Serverless scales automatically. At 200 chunks per document, 10,000 documents = 2 million vectors. Pinecone handles this natively with metadata filtering by `document_id` — query latency stays under 100ms regardless of index size because the filter is applied at the index level, not post-query.

2. **Document processing:** Currently runs as a synchronous background task on the API server. At scale, I'd decouple ingestion into a separate worker service with a task queue (Azure Queue Storage or Redis). Each upload publishes a message, and dedicated worker containers process documents in parallel. This prevents large uploads from affecting chat response latency.

3. **Storage:** Supabase PostgreSQL at 500MB handles ~50,000 messages comfortably. Beyond that, I'd add connection pooling via Supabase's built-in PgBouncer, add indexes on `created_at` for pagination, and consider partitioning the messages table by `thread_id`.

**Scaling for concurrent users (10 → 1,000+ simultaneous):**

1. **Backend replicas:** Azure Container Apps autoscales based on HTTP concurrency. I'd configure: min 2 replicas (eliminate cold starts + redundancy), max 10 replicas, scale trigger at 20 concurrent requests per replica.

2. **Rate limiting:** Add `slowapi` middleware with per-IP limits (60 requests/minute for chat, 10 uploads/hour) to prevent a single user from exhausting OpenAI API quotas.

3. **Connection pooling:** The OpenAI `AsyncOpenAI` client already pools HTTP connections. For Supabase, I'd switch to the async `supabase-py` client with connection pooling. For Pinecone, the SDK handles connection reuse internally.

4. **Caching:** Common queries (especially `general` route responses) would benefit from a Redis cache. If 50 users ask "What does CET1 mean?", the first response is cached and the next 49 skip the LLM call entirely.

5. **CDN for PDFs:** Move PDF serving from Supabase Object Storage to Azure CDN or Cloudflare, so document preview loads don't hit the origin server.

**Scaling for document size (50-page → 500-page documents):**

1. **Chunking is already token-based**, so larger documents simply produce more chunks. A 500-page document might produce 2,000 chunks — still well within embedding batch limits and Pinecone's capacity.
2. **Azure DI has a 2,000-page limit per request**, so even very large documents are handled.
3. **Processing time** scales linearly: embedding 2,000 chunks at 100/batch = 20 API calls. At ~1 second per call, that's 20 seconds — acceptable for a background task with status polling.

---

### What improvements would you make if you had more time or resources?

In priority order:

**1. Cross-encoder re-ranking (1-2 days)**
After Pinecone returns the top-20 results by cosine similarity, pass them through a cross-encoder model (Cohere Rerank or a self-hosted `cross-encoder/ms-marco-MiniLM`) that scores each (query, chunk) pair jointly. This reorders results by actual relevance rather than embedding similarity, which is particularly impactful for nuanced financial queries where keyword overlap matters. Expected improvement: 10-15% on context precision in RAGAS evaluation.

**2. Multi-document support (2-3 days)**
Currently, each chat thread is scoped to one document. Financial analysts often need to compare metrics across multiple documents — "How did BMO's CET1 ratio change between the 2023 and 2024 annual reports?" This requires modifying the retrieval pipeline to accept multiple `document_id` values and merging results across documents with cross-document citation attribution.

**3. Hybrid search with BM25 (1-2 days)**
Semantic search (embeddings) excels at meaning-based matching but can miss exact keyword matches. A query for "IFRS 17" might not rank highly in embedding space if the training data doesn't strongly associate that acronym. Adding BM25 keyword scoring alongside cosine similarity (hybrid search) would catch these cases. Pinecone supports sparse vectors for this, or I could implement it with a Reciprocal Rank Fusion approach.

**4. Streaming-aware conversation memory (1-2 days)**
Currently, each query is independent — the system doesn't consider previous messages in the thread when generating a response. Adding conversation history to the prompt (last 3-5 messages) would enable follow-up questions: "What was the net income?" → "How does that compare to the previous year?" The second question requires knowing that "that" refers to net income from the first answer.

**5. Fine-tuned embedding model (1 week)**
The general-purpose `text-embedding-3-large` works well, but a model fine-tuned on financial document retrieval tasks would improve embedding quality for domain-specific terminology. I'd generate training pairs from the benchmark QA dataset (question → relevant chunk) and fine-tune using OpenAI's embedding fine-tuning API.

**6. User authentication and multi-tenancy (1 week)**
Add Supabase Auth or Azure AD for user authentication, scope documents and threads to authenticated users, and implement Row-Level Security in PostgreSQL so users can only access their own data.

---

## 5. Demonstration

### What steps will you take during the demo to show the system's capabilities?

I'd structure the demo as a 5-minute narrative that progressively reveals system capabilities:

**Step 1: Upload (30 seconds)**
Upload the BMO Annual Report 2025 MDA PDF. Show the drag-and-drop interface, the processing status polling (pending → processing → ready), and the section hierarchy that appears once parsing completes. Point out that Azure Document Intelligence extracted 15+ sections with headings, page ranges, and a parent-child hierarchy.

**Step 2: Factual question — showcase retrieval + citations (60 seconds)**
Ask: "What was BMO's reported net income for fiscal 2024?"
- Show citations appearing before the first token streams
- Click a citation to reveal the source chunk text and page range
- Highlight the relevance score on each citation
- Point out the `message_type: kb` indicator — the system correctly routed this to the knowledge base path

**Step 3: General knowledge question — showcase routing (30 seconds)**
Ask: "What does the CET1 ratio measure?"
- Point out the `message_type: general` — the system recognized this doesn't need document retrieval
- The response is faster (no embedding + vector search overhead)
- No citations, because the answer comes from general knowledge

**Step 4: Ambiguous question — showcase clarification flow (60 seconds)**
Ask: "Tell me about risks"
- Show the clarification chips: "Credit Risk", "Market Risk", "Operational Risk", "Liquidity Risk"
- Click "Credit Risk"
- Show the focused, precise answer with citations only from the Credit Risk section
- Explain: this prevents the system from dumping a noisy answer from 5 different risk sections

**Step 5: Tabular question — showcase table understanding (30 seconds)**
Ask: "What were BMO Capital Markets' revenue and net income?"
- Show the answer with specific numbers extracted from a table
- Point out that the citation references a table chunk, not just paragraph text

**Step 6: Edge case — showcase grounding (30 seconds)**
Ask: "What is BMO's current stock price?"
- Show the system responding that this information is not in the document
- Explain: faithfulness — the system doesn't hallucinate a stock price

**Step 7: Feedback (15 seconds)**
Give thumbs up on a good answer, thumbs down on a less useful one. Explain the feedback storage and how it feeds into evaluation.

**Step 8: Show evaluation notebook (45 seconds)**
Open `docs/evaluation.ipynb` and walk through the RAGAS metrics. Show the aggregate scores and per-question-type breakdown. Point to the bar chart visualizing results.

---

### How would you handle unexpected errors or if the model gives an incorrect answer during the demo?

**For errors:**

If the API times out or returns an error, I'd explain: "This is running on Azure Container Apps with scale-to-zero — what we're seeing is a cold start. The container is spinning up for the first time. Let me retry." Then show the request succeeding on the second attempt and explain that in production, I'd set `min-replicas=1` to eliminate this. This actually turns a failure into a teaching moment about the cost-availability trade-off.

If the error is persistent (e.g., OpenAI API outage), I'd switch to a pre-recorded video of the demo flow, noting: "We're hitting a transient API outage, which is exactly why the architecture includes retry logic with exponential backoff and circuit breakers — the system would retry 3 times before returning a graceful error message to the user."

**For incorrect answers:**

If the model gives a wrong number or hallucinates, I'd click the citation, show the actual source text, and say: "This is exactly why we include citations with page ranges — the user can always verify against the source. This is also what our faithfulness metric in RAGAS measures. Let me show you how we'd catch this systematically."

Then open the evaluation notebook and explain: "If faithfulness drops below our 0.8 threshold, the action is to tighten the system prompt constraints, reduce the temperature, or add re-ranking to improve the quality of retrieved context. The offline evaluation runs on 15 benchmark questions, so one bad answer in a demo doesn't mean the system is unreliable — it means we've found a new test case to add to the benchmark."

The key principle: **never pretend the system is perfect. Show that you've built the instrumentation to detect, measure, and fix quality issues.**

---

## 6. CI/CD and Operations

### Can you walk us through your GitHub Actions pipeline and how you ensure smooth deployments?

The pipeline is defined in `.github/workflows/deploy-backend.yml` and triggers on every push to `main` that modifies files in `backend/**`:

**Stage 1: Build & Push**
```
- Checkout code
- Log in to Azure Container Registry (ACR) using admin credentials stored in GitHub Secrets
- Build Docker image from backend/Dockerfile (Python 3.12-slim base)
- Tag with both the git SHA (for traceability) and `latest` (for convenience)
- Push to ACR
```

**Stage 2: Deploy**
```
- Authenticate to Azure via a service principal (stored as AZURE_CREDENTIALS secret)
- Run `az containerapp update` with the new image tag and all environment variables
- Azure Container Apps creates a new revision and routes 100% of traffic to it
```

**Deployment safety:**

- **Immutable image tags:** Each deployment is tagged with the git SHA, so I can roll back to any previous commit: `az containerapp update --image cac2a1babdc1acr.azurecr.io/pwc-rag-api:<previous-sha>`
- **Revision history:** Azure Container Apps keeps previous revisions. If a new deployment is broken, I can reactivate the previous revision in seconds via the Azure CLI or portal.
- **Environment variable injection:** All secrets (OpenAI API key, Pinecone API key, Supabase credentials, Azure DI key) are stored in GitHub Secrets and injected as environment variables during deployment — never committed to the repository.
- **Path-scoped trigger:** The workflow only runs when `backend/**` files change. Frontend changes don't trigger a backend deployment.

**What I'd add for production:**

1. **Pre-deployment test stage:** Run `pytest` and `mypy` before building the image. If tests fail, the deployment is blocked.
2. **Staged rollout:** Route 10% of traffic to the new revision, monitor error rates for 5 minutes, then route 100%.
3. **Post-deployment smoke test:** Hit the `/health` endpoint after deployment and verify a 200 response. If it fails, auto-rollback.
4. **Dependency scanning:** Run `pip-audit` in CI to catch known vulnerabilities before they reach production.

---

### How are you monitoring the application's performance and handling logging?

**Telemetry stack:**

The backend integrates Azure Application Insights via the `azure-monitor-opentelemetry` SDK. When the `APPLICATIONINSIGHTS_CONNECTION_STRING` environment variable is set, the SDK auto-instruments:

- **HTTP request spans:** Every API call (`POST /api/chat`, `POST /api/documents/upload`, etc.) is traced with duration, status code, and request/response size
- **Dependency tracking:** Outbound calls to OpenAI, Pinecone, and Supabase are automatically captured as dependency spans with latency breakdowns
- **Exception tracking:** Unhandled exceptions are reported with full stack traces
- **Custom metrics:** Request count, response time distributions, and failure rates

**What I monitor:**

| Signal | Where | Alert Threshold |
|---|---|---|
| End-to-end latency (p50/p95) | Application Insights → Performance | p95 > 10s |
| Error rate (5xx) | Application Insights → Failures | > 5% over 5 minutes |
| OpenAI dependency latency | Application Insights → Dependencies | p95 > 5s |
| Pinecone dependency latency | Application Insights → Dependencies | p95 > 500ms |
| Container CPU/Memory | Azure Container Apps metrics | > 80% sustained |
| Cold start frequency | Custom metric from startup time | > 10 per day |

**Logging:**

Application logs are forwarded to Azure Log Analytics Workspace via the Container Apps environment. I can query logs using KQL (Kusto Query Language):

```kusto
// Find slow chat requests
requests
| where name == "POST /api/chat"
| where duration > 10000
| project timestamp, duration, resultCode, customDimensions
| order by duration desc
```

```kusto
// Track OpenAI dependency latency over time
dependencies
| where target contains "openai"
| summarize avg(duration), percentile(duration, 95) by bin(timestamp, 1h)
```

**Cost monitoring:**

OpenAI API costs are tracked via the OpenAI usage dashboard (platform.openai.com/usage), broken down by model. I have billing alerts at $50, $100, and $200 to catch unexpected cost spikes — for example, if a user uploads a 500-page document and triggers 2,000 embedding calls.

---

## 7. General Business and Strategy

### How does this tool create value for the business?

**Immediate value: analyst time savings.**

A financial analyst reviewing a 100-page annual report typically spends 2-4 hours reading, highlighting, and cross-referencing sections to answer specific questions — "What drove the increase in provision for credit losses?", "How did the acquisition impact the U.S. segment?" FinRAG compresses this to seconds per question with cited sources, allowing the analyst to verify rather than discover.

For a consulting firm like PwC, this translates directly to billable efficiency. If an engagement requires reviewing 20 annual reports and answering 50 questions per report, the manual approach takes 40-80 hours. With FinRAG, the same work takes 5-10 hours (including verification of citations). At a blended consulting rate of $300/hour, that's $9,000-$21,000 saved per engagement.

**Accuracy value: reducing errors in financial analysis.**

Manual review of dense financial documents is error-prone — an analyst might miss a restatement footnote on page 87 that changes the interpretation of figures on page 12. FinRAG's retrieval covers the entire document on every query, and the citation system makes the source explicit. The feedback mechanism creates a quality improvement loop: every thumbs-down is a data point for identifying and fixing systematic weaknesses.

**Strategic value: scalable institutional knowledge.**

Once a document is ingested, every analyst in the organization can query it. The institutional knowledge isn't locked in one person's notes — it's in a searchable, cited, version-controlled system. When a new analyst joins the team and needs to get up to speed on a client's financial history, they can ask FinRAG instead of reading 5 years of annual reports.

**Compliance value: auditable answer provenance.**

Every answer includes page-level citations, and every user interaction (questions, answers, feedback) is persisted in Supabase with timestamps. This creates an audit trail for regulatory compliance — if a regulator asks "What analysis did you perform on BMO's credit risk exposure?", the firm can produce the exact questions asked, answers received, sources cited, and analyst feedback.

---

### How would you adapt this solution to different industries or document types?

The architecture is document-type agnostic by design. The three components that would change are: the **parser**, the **prompt templates**, and the **evaluation benchmark**.

**Legal documents (contracts, regulatory filings):**
- **Parser:** Legal documents have different structural patterns — numbered clauses (1.1, 1.1.1), defined terms (capitalized and quoted), and cross-references ("as defined in Section 4.2"). I'd extend the section extractor to recognize clause numbering as a hierarchy signal and extract defined terms as metadata for chunk enrichment.
- **Prompts:** The system prompt would emphasize precision: "Quote exact clause language when answering. Always reference the specific section number." The routing model would add a `definition` category for questions like "What does 'Material Adverse Change' mean in this agreement?"
- **Evaluation:** Benchmark questions would test clause extraction ("What is the termination fee?"), cross-reference resolution ("What are the conditions precedent listed in Section 3?"), and defined term accuracy.

**Healthcare (clinical trial reports, medical literature):**
- **Parser:** Clinical documents contain structured data (patient demographics tables, statistical results, adverse event listings) and domain-specific abbreviations (HR, CI, p-value). Azure DI handles tables well; the chunker would need custom handling for statistical result sections where splitting a results table from its confidence interval footnote would destroy meaning.
- **Prompts:** Emphasis on not extrapolating beyond the data: "Do not generalize from specific study results. Always state the study population and statistical significance."
- **Evaluation:** Questions about statistical outcomes ("What was the primary endpoint result?"), safety data ("What were the most common adverse events?"), and inclusion/exclusion criteria.

**Insurance (policy documents, claims):**
- **Parser:** Insurance policies have deeply nested conditional language ("If the insured... and if the policy... then coverage shall..."). Chunk boundaries must preserve complete conditional chains.
- **Prompts:** The routing model would add a `coverage_check` category for "Am I covered for X?" questions, which require the system to find the relevant coverage clause AND any exclusions or limitations.
- **Evaluation:** Test for completeness — a question about coverage should surface both the coverage grant and any applicable exclusions.

**What stays the same across all industries:**
- The vector store, embedding model, and retrieval pipeline are domain-agnostic
- The SSE streaming, conversation management, and feedback mechanisms don't change
- The CI/CD pipeline, monitoring, and deployment infrastructure are identical
- The RAGAS evaluation framework works for any domain — only the benchmark QA pairs change

The adaptation effort for a new industry is roughly 1-2 weeks: 3 days for parser customization, 2 days for prompt engineering, 2 days for building the evaluation benchmark, and 2-3 days for testing and tuning retrieval parameters.
