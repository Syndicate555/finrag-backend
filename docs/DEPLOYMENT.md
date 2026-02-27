# FinRAG — Deployment, CI/CD & Infrastructure Guide

> Everything you need to know to deploy, operate, debug, and extend the FinRAG platform.

---

## Table of Contents

1. [Infrastructure Overview](#1-infrastructure-overview)
2. [Azure Resources — What Exists and Why](#2-azure-resources--what-exists-and-why)
3. [Backend Deployment (Azure Container Apps)](#3-backend-deployment-azure-container-apps)
4. [Frontend Deployment (Vercel)](#4-frontend-deployment-vercel)
5. [CI/CD Pipeline — Step by Step](#5-cicd-pipeline--step-by-step)
6. [How to Push a Change to Production](#6-how-to-push-a-change-to-production)
7. [Docker — Local and Production](#7-docker--local-and-production)
8. [Secrets Management](#8-secrets-management)
9. [Database (Supabase)](#9-database-supabase)
10. [DNS, Domains & CORS](#10-dns-domains--cors)
11. [Scaling, Cold Starts & Performance](#11-scaling-cold-starts--performance)
12. [Monitoring & Debugging](#12-monitoring--debugging)
13. [Rollback & Disaster Recovery](#13-rollback--disaster-recovery)
14. [Cost Breakdown](#14-cost-breakdown)
15. [Runbook — Common Operations](#15-runbook--common-operations)
16. [Repo Structure & Git Strategy](#16-repo-structure--git-strategy)
17. [Recreating Everything from Scratch](#17-recreating-everything-from-scratch)

---

## 1. Infrastructure Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        GITHUB (Syndicate555/finrag-backend)             │
│                                                                         │
│  Push to main ──▶ GitHub Actions Workflow                               │
│                    │                                                     │
│                    ├─▶ Build Docker image                               │
│                    ├─▶ Push to Azure Container Registry                 │
│                    └─▶ Deploy to Azure Container Apps                   │
└─────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              AZURE (West US)                           │
│                                                                        │
│  Resource Group: pwc-rag-rg                                            │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Container Registry: cac2a1babdc1acr.azurecr.io (Basic SKU)     │  │
│  │  Stores Docker images tagged by git SHA + "latest"              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Container Apps Environment: pwc-rag-env                         │  │
│  │  Default domain: *.politemeadow-4143bf92.westus.azurecontainerapps│ │
│  │  Static IP: 20.253.238.21                                        │  │
│  │                                                                    │ │
│  │  ┌──────────────────────────────────────────────────────────┐    │  │
│  │  │  Container App: pwc-rag-api                              │    │  │
│  │  │  FQDN: pwc-rag-api.politemeadow-4143bf92.westus.        │    │  │
│  │  │        azurecontainerapps.io                             │    │  │
│  │  │  Port: 8000 │ External ingress │ HTTPS auto             │    │  │
│  │  │  Scale: 0–1 replicas │ 0.5 vCPU │ 1GB RAM              │    │  │
│  │  │  Workload: Consumption (serverless)                      │    │  │
│  │  │  Cooldown: 300s │ Active revisions: Single               │    │  │
│  │  └──────────────────────────────────────────────────────────┘    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Log Analytics Workspace: pwc-rag-logs                           │  │
│  │  SKU: PerGB2018 │ Retention: 30 days                             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Service Principal: finrag-backend-deploy                        │  │
│  │  Role: Contributor (scoped to pwc-rag-rg)                        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              VERCEL                                     │
│                                                                        │
│  Project: financial (linked to frontend git repo)                      │
│  Domains: finrag.info, www.finrag.info                                 │
│  Preview: financial-2juov3jtm-risk-guardian.vercel.app                 │
│  Env: NEXT_PUBLIC_API_URL = (backend Container App URL)                │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL SERVICES                               │
│                                                                        │
│  Supabase  ─ PostgreSQL database + object storage (PDFs)               │
│  Pinecone  ─ Vector database (document embeddings)                     │
│  OpenAI    ─ Embeddings (text-embedding-3-large) + Chat (GPT-4o)       │
│  Azure DI  ─ Document Intelligence (structured PDF parsing)            │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Azure Resources — What Exists and Why

Every Azure resource lives in a single **resource group** (`pwc-rag-rg`) in the **West US** region. This means you can view, manage, or delete everything from one place.

### Resource Group: `pwc-rag-rg`

| Resource | Name | Type | Purpose |
|----------|------|------|---------|
| Container Registry | `cac2a1babdc1acr` | `Microsoft.ContainerRegistry` (Basic) | Stores Docker images. GitHub Actions pushes here. |
| Container Apps Environment | `pwc-rag-env` | `Microsoft.App/managedEnvironments` | Shared networking/logging layer for container apps. |
| Container App | `pwc-rag-api` | `Microsoft.App/containerApps` | The running backend API. Pulls images from ACR. |
| Log Analytics Workspace | `pwc-rag-logs` | `Microsoft.OperationalInsights` | Collects container stdout/stderr. 30-day retention. |
| Service Principal | `finrag-backend-deploy` | Azure AD App Registration | GitHub Actions uses this to authenticate with Azure. Scoped to `pwc-rag-rg` only. |

### Why these specific resources?

- **Container Registry (Basic SKU)**: The cheapest tier (~$5/mo). Stores images. GitHub Actions builds the Docker image on GitHub's runners (free for public repos), then pushes the built image here. The Container App pulls from here on deploy.

- **Container Apps Environment**: Think of this as a "cluster" that can host multiple container apps. We only have one app, but the environment provides the shared infrastructure: internal DNS, Log Analytics integration, and the `*.azurecontainerapps.io` domain.

- **Container App**: The actual running backend. Key settings:
  - **Consumption workload profile**: Serverless — Azure manages the underlying VMs. No node pools to manage.
  - **Single active revision mode**: Only one revision runs at a time. New deploys replace the old one.
  - **0–1 replicas**: Scales to zero when idle (saves money), scales to 1 when traffic arrives.

- **Log Analytics (PerGB2018)**: Pay-per-GB ingested. At our scale, this is essentially free. Logs are queryable via Azure Portal or `az containerapp logs`.

- **Service Principal**: A non-human identity that GitHub Actions uses to run `az` commands. It has `Contributor` role only on `pwc-rag-rg` — it cannot touch anything else in the Azure subscription.

---

## 3. Backend Deployment (Azure Container Apps)

### The Dockerfile

```dockerfile
FROM python:3.12-slim    # Minimal Python image (~150MB)
WORKDIR /app
COPY pyproject.toml .    # Copy dependency spec first (Docker layer caching)
RUN pip install --no-cache-dir .   # Install dependencies
COPY . .                 # Copy application code
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Key details:**
- Uses `python:3.12-slim` (not alpine) because some dependencies (PyMuPDF, pdfplumber) need C libraries that are painful to install on alpine.
- `pyproject.toml` is copied first so that `pip install` is cached as a Docker layer. Changing application code won't re-install dependencies.
- No `.dockerignore` exists — everything in `backend/` is copied. This includes some `.next` cache files from a previous build that leaked in. Not harmful but wastes ~5MB of image space.
- Uvicorn runs without `--reload` in production (reload is only used in docker-compose for local dev).
- No multi-stage build — the image includes build tools. Could be optimized but not a priority at this scale.

### How the Container App Works

When a request hits `https://pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io`:

1. Azure's **ingress controller** terminates TLS (auto-managed certificate).
2. If replicas = 0, Azure spins up a container from the latest revision's image (cold start, ~10-15s).
3. Request is forwarded to port 8000 inside the container.
4. After 300 seconds of no traffic (cooldown period), the replica scales back to zero.

### Environment Variables in the Container

Env vars are set during deploy via the GitHub Actions workflow. They are baked into the **revision** — changing env vars creates a new revision. The current env vars:

| Variable | Source | Value Type |
|----------|--------|------------|
| `OPENAI_API_KEY` | GitHub Secret | Secret |
| `PINECONE_API_KEY` | GitHub Secret | Secret |
| `PINECONE_INDEX_NAME` | Hardcoded in workflow | `pwc-rag` |
| `SUPABASE_URL` | GitHub Secret | Secret |
| `SUPABASE_KEY` | GitHub Secret | Secret |
| `SUPABASE_BUCKET_NAME` | Hardcoded in workflow | `pwc-rag` |
| `AZURE_DI_ENABLED` | Hardcoded in workflow | `true` |
| `AZURE_DI_ENDPOINT` | GitHub Secret | Secret |
| `AZURE_DI_KEY` | GitHub Secret | Secret |
| `CORS_ORIGINS` | Hardcoded in workflow | JSON array of allowed origins |

**Important**: Env vars are NOT stored in Azure's secret store — they're passed as plain env vars. This is a simplification. For higher security, you could migrate to `secretref:` references that pull from the Container App's secret store.

### Image Tagging Strategy

Every deploy creates two tags:
- `cac2a1babdc1acr.azurecr.io/pwc-rag-api:{git-sha}` — Immutable, tied to exact commit.
- `cac2a1babdc1acr.azurecr.io/pwc-rag-api:latest` — Mutable, always points to most recent build.

The Container App always deploys the `{git-sha}` tag, so even if `latest` is overwritten, the running container is pinned to a specific commit.

---

## 4. Frontend Deployment (Vercel)

The frontend is a **separate concern** from the backend. It lives in its own git repo and deploys independently via Vercel.

### How It Works

1. Frontend code is pushed to its own GitHub repo.
2. Vercel auto-detects the push and builds the Next.js app.
3. Vercel deploys to its global CDN.
4. Custom domains `finrag.info` and `www.finrag.info` point to Vercel via DNS.

### Configuration

| Setting | Value |
|---------|-------|
| Framework | Next.js (auto-detected) |
| Build command | `next build` |
| Output directory | `.next` |
| Node version | 18+ |
| Environment variable | `NEXT_PUBLIC_API_URL` = backend URL |

### How Frontend Reaches Backend

In **production**: The frontend makes direct HTTPS requests to the backend URL stored in `NEXT_PUBLIC_API_URL`. This is a cross-origin request, so CORS must be configured on the backend.

In **local development**: `next.config.ts` has a rewrite rule that proxies `/api/*` to `http://127.0.0.1:8000/api/*`. This avoids CORS entirely in dev — the browser thinks it's talking to the same origin.

```typescript
// next.config.ts
async rewrites() {
  return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
}
```

### Vercel Preview Deployments

Every push to a non-main branch creates a preview URL like `financial-{hash}-risk-guardian.vercel.app`. This URL is included in the backend's CORS allowlist so previews can talk to the production backend.

---

## 5. CI/CD Pipeline — Step by Step

### Trigger

The workflow runs when:
- A push to `main` changes any file in `backend/**` or the workflow file itself.
- You manually trigger it via the GitHub Actions UI or `gh workflow run`.

It does **NOT** run for:
- Changes only in `frontend/`, `docs/`, or root files.
- Pushes to non-main branches.

### Pipeline Steps (Annotated)

```
┌──────────────────────────────────────────────────────────────────┐
│  STEP 1: Checkout code                                           │
│  actions/checkout@v4                                             │
│                                                                  │
│  Clones the repo on the GitHub Actions runner (ubuntu-latest).   │
│  The runner has Docker and az CLI pre-installed.                 │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│  STEP 2: Login to ACR                                            │
│  docker/login-action@v3                                          │
│                                                                  │
│  Authenticates Docker to push images to Azure Container Registry.│
│  Uses ACR admin username/password stored in GitHub Secrets.      │
│                                                                  │
│  Credentials:                                                    │
│    Registry: cac2a1babdc1acr.azurecr.io                         │
│    Username: ${{ secrets.ACR_USERNAME }}                          │
│    Password: ${{ secrets.ACR_PASSWORD }}                          │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│  STEP 3: Build and push Docker image                             │
│  docker/build-push-action@v6                                     │
│                                                                  │
│  Builds the Dockerfile at ./backend, pushes two tags:            │
│    - cac2a1babdc1acr.azurecr.io/pwc-rag-api:{SHA}              │
│    - cac2a1babdc1acr.azurecr.io/pwc-rag-api:latest             │
│                                                                  │
│  This step takes ~40-60s. The GitHub runner builds on amd64.     │
│  No Docker layer caching is configured (each build is clean).    │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│  STEP 4: Login to Azure                                          │
│  az login --service-principal                                    │
│                                                                  │
│  Authenticates the az CLI using the service principal.            │
│  Sets the subscription context.                                  │
│                                                                  │
│  Credentials:                                                    │
│    Client ID:     ${{ secrets.AZURE_CLIENT_ID }}                 │
│    Client Secret: ${{ secrets.AZURE_CLIENT_SECRET }}             │
│    Tenant ID:     ${{ secrets.AZURE_TENANT_ID }}                 │
│    Subscription:  ${{ secrets.AZURE_SUBSCRIPTION_ID }}           │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│  STEP 5: Deploy to Container Apps                                │
│  az containerapp update                                          │
│                                                                  │
│  Updates the container app with:                                 │
│    - New image (pinned to git SHA)                               │
│    - All environment variables (secrets + hardcoded values)      │
│    - Scale settings (0–1 replicas)                               │
│                                                                  │
│  This creates a NEW REVISION. The old revision is deprovisioned. │
│  Azure pulls the image from ACR and starts the new container.    │
│  Takes ~20-40s for the revision to become active.                │
│                                                                  │
│  Total pipeline time: ~1.5–2 minutes.                            │
└──────────────────────────────────────────────────────────────────┘
```

### What "Revision" Means

Azure Container Apps uses a **revision model**. Each deploy creates a new revision (immutable snapshot of config + image). In **single revision mode** (our setup), only the latest revision receives traffic. Old revisions are deprovisioned automatically.

You can see revision history:
```bash
az containerapp revision list --name pwc-rag-api --resource-group pwc-rag-rg -o table
```

---

## 6. How to Push a Change to Production

### Backend Change

```bash
# 1. Make your code changes in backend/
vim backend/app/services/rag_pipeline.py

# 2. Test locally
cd backend && uvicorn app.main:app --reload
# or: docker compose up backend

# 3. Commit and push to main
git add backend/
git commit -m "fix: improve retrieval scoring"
git push origin main

# 4. GitHub Actions automatically:
#    - Builds Docker image
#    - Pushes to ACR
#    - Deploys to Container Apps
#    Pipeline takes ~1.5-2 minutes.

# 5. Monitor the deploy
gh run watch --repo Syndicate555/finrag-backend

# 6. Verify
curl https://pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io/health
```

**That's it.** Push to main → auto-deploy. No manual steps.

### Frontend Change

```bash
# 1. Make your code changes in frontend/
vim frontend/src/components/chat/chat-container.tsx

# 2. Test locally
cd frontend && npm run dev

# 3. Commit and push to main (in the frontend repo)
cd frontend
git add .
git commit -m "fix: improve citation display"
git push origin main

# 4. Vercel automatically builds and deploys.
#    Takes ~30-60 seconds.

# 5. Verify at https://finrag.info
```

### Workflow-Only Change (e.g., updating CORS)

If you change `.github/workflows/deploy-backend.yml` without changing `backend/`, the workflow still triggers (it's in the `paths` filter). This will rebuild the same Docker image and redeploy with updated env vars.

### Manual Deploy (no code changes)

```bash
gh workflow run deploy-backend.yml --repo Syndicate555/finrag-backend
```

Useful for: rotating secrets, forcing a fresh container, or recovering from a stuck state.

---

## 7. Docker — Local and Production

### Local Development (docker-compose.yml)

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    env_file: ./backend/.env       # Reads secrets from local .env
    volumes: [./backend:/app]      # Hot reload — code changes reflect instantly
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000
    volumes:
      - ./frontend:/app
      - /app/node_modules          # Prevent overwriting node_modules from host
    command: npm run dev
```

**Key differences from production:**
| Aspect | Local (docker-compose) | Production (Container Apps) |
|--------|----------------------|---------------------------|
| Uvicorn | `--reload` (watches for file changes) | No reload (stable process) |
| Env vars | From `./backend/.env` file | From GitHub Secrets via workflow |
| Volumes | Mounted (live code sync) | None (code baked into image) |
| HTTPS | No (plain HTTP on localhost) | Yes (auto TLS from Azure) |
| Scale | Always 1 container | 0–1 (scale to zero) |

### Running Locally Without Docker

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"    # Install with dev dependencies (pytest, etc.)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev    # Starts on http://localhost:3000
```

The frontend's `next.config.ts` proxies `/api/*` to `localhost:8000`, so no CORS setup needed locally.

### Dockerfile Build Optimization Notes

The current Dockerfile is simple and works, but there are optimization opportunities:

1. **No `.dockerignore`**: Everything in `backend/` gets copied, including `__pycache__`, test files, and stale `.next` cache. Adding a `.dockerignore` would reduce image size.
2. **No multi-stage build**: The final image includes pip and build tools. A multi-stage build could reduce the image by ~100MB.
3. **No Docker layer caching in CI**: Each GitHub Actions build is clean. Adding `docker/build-push-action`'s cache-from/cache-to would speed up builds.

These are minor optimizations — the current setup works and builds in under 60 seconds.

---

## 8. Secrets Management

### Where Secrets Live

Secrets exist in **three places**, each serving a different purpose:

```
┌─────────────────────────────────────────────────────────────┐
│  1. LOCAL DEVELOPMENT: backend/.env                          │
│     - Not committed to git (.gitignore excludes .env*)       │
│     - Used by docker-compose and local uvicorn               │
│     - You maintain this manually                             │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  2. GITHUB SECRETS: Syndicate555/finrag-backend              │
│     - Encrypted at rest by GitHub                            │
│     - Injected into workflow runs as env vars                │
│     - Changed via: gh secret set NAME --body 'value'         │
│     - Cannot be read back (write-only)                       │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼ (workflow passes them to az containerapp update)
┌─────────────────────────────────────────────────────────────┐
│  3. CONTAINER APP: Runtime env vars                          │
│     - Baked into the revision                                │
│     - Readable via: az containerapp show (if you have access)│
│     - Changed by: redeploying (push or manual trigger)       │
└─────────────────────────────────────────────────────────────┘
```

### Complete Secret Inventory

| Secret Name | GitHub Secret? | Used By | How to Rotate |
|-------------|---------------|---------|---------------|
| `ACR_USERNAME` | Yes | CI: Push Docker images | `az acr credential renew` then update GitHub |
| `ACR_PASSWORD` | Yes | CI: Push Docker images | Same as above |
| `AZURE_CLIENT_ID` | Yes | CI: `az login` | Recreate service principal |
| `AZURE_CLIENT_SECRET` | Yes | CI: `az login` | `az ad sp credential reset` then update GitHub |
| `AZURE_TENANT_ID` | Yes | CI: `az login` | Fixed (won't change) |
| `AZURE_SUBSCRIPTION_ID` | Yes | CI: `az login` | Fixed (won't change) |
| `OPENAI_API_KEY` | Yes | Backend: Embeddings + chat | Rotate in OpenAI dashboard, update GitHub + local .env |
| `PINECONE_API_KEY` | Yes | Backend: Vector store | Rotate in Pinecone console, update GitHub + local .env |
| `SUPABASE_URL` | Yes | Backend: Database | Fixed (won't change unless you recreate the project) |
| `SUPABASE_KEY` | Yes | Backend: Database (service role) | Rotate in Supabase dashboard, update GitHub + local .env |
| `AZURE_DI_ENDPOINT` | Yes | Backend: PDF parsing | Fixed (won't change) |
| `AZURE_DI_KEY` | Yes | Backend: PDF parsing | Rotate in Azure Portal, update GitHub + local .env |
| `AZURE_CREDENTIALS` | Yes (unused) | Legacy — from initial setup | Can be deleted |
| `CORS_ORIGINS` | Yes (unused) | Was used before hardcoding in workflow | Can be deleted |

### Rotating a Secret

```bash
# 1. Get new value from the provider (OpenAI, Pinecone, etc.)

# 2. Update GitHub secret
gh secret set OPENAI_API_KEY --body 'sk-new-key-here' --repo Syndicate555/finrag-backend

# 3. Update local .env
vim backend/.env

# 4. Trigger redeploy to pick up the new secret
gh workflow run deploy-backend.yml --repo Syndicate555/finrag-backend

# 5. Verify
curl https://pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io/health
```

### Security Notes

- The `AZURE_CREDENTIALS` and `CORS_ORIGINS` GitHub secrets are **leftover from initial setup attempts** and are not used by the current workflow. You can safely delete them.
- The service principal (`finrag-backend-deploy`) has `Contributor` role scoped to `pwc-rag-rg` only. It cannot access other resource groups or subscriptions.
- ACR uses **admin credentials** (username/password). For higher security, you could switch to a managed identity or service principal authentication for ACR.
- Env vars in the Container App are **not** stored as Container Apps secrets (they're plain env vars). For production-grade security, migrate sensitive values to `az containerapp secret set` with `secretref:` references.

---

## 9. Database (Supabase)

### What Supabase Provides

1. **PostgreSQL database** — Stores documents, threads, messages, feedback, sections.
2. **Object storage** — Stores uploaded PDF files in a bucket called `pwc-rag`.
3. **Auto-generated REST API** — Not used (we access Supabase via the Python SDK).

### Schema

5 tables with cascading foreign keys:

```
documents
  ├── document_sections (cascade delete)
  └── threads (set null on delete)
        └── messages (cascade delete)
              └── message_feedback (cascade delete)
```

### Running Migrations

The schema is defined in `supabase/migration.sql`. To apply it:

**Option 1: Supabase Dashboard**
1. Go to your Supabase project → SQL Editor
2. Paste the contents of `supabase/migration.sql`
3. Click "Run"

**Option 2: Supabase CLI**
```bash
supabase db push
```

**Important**: The migration uses `CREATE TABLE IF NOT EXISTS` and `CREATE OR REPLACE`, so it's safe to run multiple times (idempotent).

### Adding a New Table or Column

1. Write the SQL migration:
   ```sql
   ALTER TABLE messages ADD COLUMN token_count integer;
   ```
2. Run it via the Supabase dashboard or CLI.
3. Update the Pydantic models in `backend/app/models/schemas.py`.
4. Update the Supabase client operations in `backend/app/services/supabase_client.py`.
5. Commit and deploy.

There is no automated migration system — migrations are manual. For a small project this is fine; for larger teams, consider using Supabase's built-in migration tracking (`supabase migration new`).

---

## 10. DNS, Domains & CORS

### Domain Setup

| Domain | Points To | Managed By |
|--------|-----------|------------|
| `finrag.info` | Vercel (frontend) | DNS provider → Vercel |
| `www.finrag.info` | Vercel (frontend) | DNS provider → Vercel |
| `pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io` | Azure (backend) | Azure (auto-assigned) |

The backend uses Azure's auto-assigned domain. You could add a custom domain (e.g., `api.finrag.info`) via:
```bash
az containerapp hostname add --name pwc-rag-api --resource-group pwc-rag-rg --hostname api.finrag.info
```
Then point a CNAME record from `api.finrag.info` to the Container App's FQDN.

### CORS Configuration

CORS is configured at the **application level** in FastAPI (not at the Azure ingress level). The `CORS_ORIGINS` env var is a JSON array:

```json
["https://finrag.info", "https://www.finrag.info", "https://financial-2juov3jtm-risk-guardian.vercel.app", "http://localhost:3000"]
```

This is set in the GitHub Actions workflow and baked into the Container App revision.

**To add a new allowed origin:**

1. Edit `.github/workflows/deploy-backend.yml`:
   ```yaml
   'CORS_ORIGINS=["https://finrag.info","https://new-domain.com","http://localhost:3000"]'
   ```
2. Push to main (or run the workflow manually).

**Or update directly without redeploying:**
```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg \
  --set-env-vars 'CORS_ORIGINS=["https://finrag.info","https://new-domain.com","http://localhost:3000"]'
```

Note: Direct `az containerapp update` changes will be **overwritten** on the next GitHub Actions deploy unless you also update the workflow file.

### How CORS Works in the App

```python
# backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,    # From CORS_ORIGINS env var
    allow_credentials=True,
    allow_methods=["*"],                    # All HTTP methods
    allow_headers=["*"],                    # All headers
)
```

FastAPI's CORS middleware handles preflight (OPTIONS) requests automatically.

---

## 11. Scaling, Cold Starts & Performance

### Current Scale Settings

| Setting | Value | Meaning |
|---------|-------|---------|
| Min replicas | 0 | Container shuts down when idle |
| Max replicas | 1 | Single instance (no horizontal scaling) |
| Cooldown | 300s | Wait 5 minutes of no traffic before scaling to zero |
| CPU | 0.5 vCPU | Half a CPU core |
| Memory | 1 GB | Sufficient for the Python app |

### Cold Start Behavior

When the app is at zero replicas and a request arrives:

1. Azure detects the incoming request (~1-2s).
2. Azure pulls the container image from ACR (cached, ~2-3s).
3. The container starts and uvicorn initializes (~5-8s).
4. Pydantic Settings loads env vars and the `@lru_cache` singletons are created on first use (~1-2s).
5. **Total cold start: ~10-15 seconds.**

The first request will be slow. Subsequent requests are fast (<100ms for health check, 1-5s for RAG queries depending on LLM latency).

### Eliminating Cold Starts

If cold starts are unacceptable, set min replicas to 1:

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 1
```

**Cost impact**: The app will always be running (~$0.01/hour = ~$7.50/month on Consumption plan). To revert:

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 0
```

### Scaling Beyond 1 Replica

If you need to handle concurrent users:

```bash
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --max-replicas 3
```

The app is stateless (all state is in Supabase/Pinecone), so horizontal scaling works out of the box. The only consideration is that `@lru_cache` singletons are per-process, so each replica creates its own OpenAI/Pinecone clients (which is fine).

---

## 12. Monitoring & Debugging

### View Container Logs

```bash
# Live logs (last 50 lines)
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg --tail 50

# Follow logs in real-time
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg --follow

# Logs for a specific revision
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg \
  --revision pwc-rag-api--0000003
```

### Check App Status

```bash
# Is the app running?
az containerapp show --name pwc-rag-api --resource-group pwc-rag-rg \
  --query "properties.runningStatus" -o tsv

# Current revision info
az containerapp revision list --name pwc-rag-api --resource-group pwc-rag-rg -o table

# What image is deployed?
az containerapp show --name pwc-rag-api --resource-group pwc-rag-rg \
  --query "properties.template.containers[0].image" -o tsv
```

### Health Check

```bash
curl https://pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io/health
# Expected: {"status":"ok"}
```

### Check GitHub Actions

```bash
# Recent runs
gh run list --repo Syndicate555/finrag-backend --limit 5

# Watch a specific run
gh run watch <run-id> --repo Syndicate555/finrag-backend

# View logs for a failed run
gh run view <run-id> --repo Syndicate555/finrag-backend --log-failed
```

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `curl` times out | App scaled to zero, cold starting | Wait 15s and retry |
| `{"detail":"Internal Server Error"}` | Check container logs | `az containerapp logs show ...` |
| CORS error in browser | Origin not in `CORS_ORIGINS` | Update CORS and redeploy |
| `pydantic_settings.exceptions.SettingsError` | Env var format issue | Check JSON parsing of `CORS_ORIGINS` |
| GitHub Actions fails at "Login to Azure" | Service principal expired | Recreate: `az ad sp credential reset` |
| GitHub Actions fails at "Build and push" | ACR credentials wrong | `az acr credential show --name cac2a1babdc1acr` |
| App crashes on startup | Missing env var | Check all required vars are in the workflow |

---

## 13. Rollback & Disaster Recovery

### Rolling Back to a Previous Version

Every deploy is tagged with its git commit SHA. To rollback:

```bash
# 1. Find the commit SHA you want to roll back to
git log --oneline -10

# 2. Deploy that specific image
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg \
  --image cac2a1babdc1acr.azurecr.io/pwc-rag-api:<old-commit-sha>
```

This is instant (~20-30s) because the image already exists in ACR.

**Warning**: The next push to main will overwrite this rollback. If you need to permanently revert, `git revert` the bad commit and push.

### Listing Available Images

```bash
az acr repository show-tags --name cac2a1babdc1acr --repository pwc-rag-api --orderby time_desc --top 10 -o table
```

### Full Disaster Recovery

If everything is lost, see [Section 17: Recreating Everything from Scratch](#17-recreating-everything-from-scratch).

---

## 14. Cost Breakdown

### Azure (Monthly Estimates)

| Resource | Cost | Notes |
|----------|------|-------|
| Container Apps (Consumption) | ~$0 idle, ~$7.50 always-on | 0.5 vCPU × $0.000024/s + 1GB × $0.000003/s |
| Container Registry (Basic) | ~$5/mo | 10GB storage included |
| Log Analytics | ~$0 | Free up to 5GB/month ingestion |
| **Total (scale-to-zero)** | **~$5/mo** | |
| **Total (always-on)** | **~$12.50/mo** | |

### External Services (Monthly Estimates)

| Service | Free Tier | Paid Estimate |
|---------|-----------|---------------|
| Supabase | 500MB DB, 1GB storage | Free tier sufficient |
| Pinecone | 1 index, 100K vectors | Free tier sufficient for ~200 documents |
| OpenAI | None | ~$5-20/mo depending on usage |
| Azure Document Intelligence | 500 pages/month (F0) | Free tier likely sufficient |
| Vercel | 100GB bandwidth | Free tier sufficient |

### Total Estimated Cost

- **Light usage (personal/demo)**: ~$5-10/month (mostly ACR + OpenAI)
- **Moderate usage**: ~$15-30/month
- **Scale-to-zero saves significant cost** when the app isn't being used

---

## 15. Runbook — Common Operations

### Deploy Backend

```bash
# Automatic: push to main
git push origin main

# Manual trigger:
gh workflow run deploy-backend.yml --repo Syndicate555/finrag-backend
```

### Deploy Frontend

```bash
# Push to frontend repo's main branch
cd frontend && git push origin main
# Vercel auto-deploys
```

### Add a New Environment Variable

```bash
# 1. Add to GitHub secrets
gh secret set NEW_VAR --body 'value' --repo Syndicate555/finrag-backend

# 2. Add to workflow (.github/workflows/deploy-backend.yml)
#    In the --set-env-vars section:
#    NEW_VAR="${{ secrets.NEW_VAR }}"

# 3. Add to backend config (backend/app/config.py)
#    new_var: str = "default"

# 4. Add to local .env
echo 'NEW_VAR=value' >> backend/.env

# 5. Push to deploy
git add . && git commit -m "feat: add NEW_VAR config" && git push
```

### Update CORS Origins

```bash
# Quick (direct update, overwritten on next deploy):
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg \
  --set-env-vars 'CORS_ORIGINS=["https://finrag.info","https://new-domain.com"]'

# Permanent (update workflow + push):
# Edit .github/workflows/deploy-backend.yml CORS_ORIGINS line
git commit -am "ci: add new-domain.com to CORS" && git push
```

### View Logs

```bash
# Container logs
az containerapp logs show --name pwc-rag-api --resource-group pwc-rag-rg --tail 100

# CI/CD logs
gh run view <run-id> --repo Syndicate555/finrag-backend --log
```

### Restart the App

```bash
# Create a new revision (restarts the container)
az containerapp revision restart --name pwc-rag-api --resource-group pwc-rag-rg \
  --revision $(az containerapp revision list --name pwc-rag-api --resource-group pwc-rag-rg --query "[0].name" -o tsv)
```

### Scale Up / Down

```bash
# Always-on (no cold starts)
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 1

# Scale to zero (save money)
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --min-replicas 0

# Handle more traffic
az containerapp update --name pwc-rag-api --resource-group pwc-rag-rg --max-replicas 3
```

### Rotate ACR Credentials

```bash
# Regenerate password
az acr credential renew --name cac2a1babdc1acr --password-name password

# Get new credentials
az acr credential show --name cac2a1babdc1acr

# Update GitHub secrets
gh secret set ACR_USERNAME --body '<new-username>' --repo Syndicate555/finrag-backend
gh secret set ACR_PASSWORD --body '<new-password>' --repo Syndicate555/finrag-backend
```

### Rotate Service Principal Secret

```bash
# Reset credential
az ad sp credential reset --id cd844151-d5b6-4b6f-be00-60198fc37267

# Update GitHub secret with new password
gh secret set AZURE_CLIENT_SECRET --body '<new-secret>' --repo Syndicate555/finrag-backend
```

### Factory Reset (Delete All Data)

```bash
# Via API
curl -X DELETE https://pwc-rag-api.politemeadow-4143bf92.westus.azurecontainerapps.io/api/reset

# This deletes all documents, threads, messages, and vectors.
# PDFs in Supabase storage are also deleted.
```

### Delete Everything in Azure (Teardown)

```bash
# This deletes ALL Azure resources for this project
az group delete --name pwc-rag-rg --yes --no-wait

# Delete the service principal
az ad sp delete --id cd844151-d5b6-4b6f-be00-60198fc37267
```

---

## 16. Repo Structure & Git Strategy

### Two Repositories

| Repo | Contents | Deploy Target | CI/CD |
|------|----------|--------------|-------|
| `Syndicate555/finrag-backend` | Backend + infra + docs (this repo) | Azure Container Apps | GitHub Actions |
| Frontend repo (separate) | Next.js frontend | Vercel | Vercel auto-deploy |

The frontend is embedded as a git submodule in the backend repo (the `frontend/` directory has its own `.git`). This means:
- Backend and frontend have **independent deploy cycles**.
- Changing backend code doesn't trigger a frontend deploy, and vice versa.
- The `finrag-backend` repo is the "monorepo" for infrastructure, docs, and backend code.

### Branch Strategy

Currently simple: **single `main` branch, direct push, auto-deploy.**

For a team workflow, you'd add:
1. Feature branches (`feature/improve-chunking`)
2. Pull requests with review
3. Merge to main triggers deploy
4. The workflow already supports `workflow_dispatch` for manual deploys

### What Triggers a Deploy

| Change | Backend Deploy? | Frontend Deploy? |
|--------|----------------|-----------------|
| `backend/**` changed, pushed to main | Yes | No |
| `frontend/**` changed, pushed to main | No | No (wrong repo) |
| Frontend repo pushed to main | No | Yes (Vercel) |
| `.github/workflows/deploy-backend.yml` changed | Yes | No |
| `docs/**` changed | No | No |
| Manual `workflow_dispatch` | Yes | No |

---

## 17. Recreating Everything from Scratch

If you need to rebuild the entire infrastructure (new Azure subscription, starting fresh, etc.):

### Step 1: Azure Setup

```bash
# Login
az login

# Register providers
az provider register -n Microsoft.App --wait
az provider register -n Microsoft.OperationalInsights --wait
az provider register -n Microsoft.ContainerRegistry --wait

# Create resource group
az group create --name pwc-rag-rg --location westus

# Create Log Analytics workspace
az monitor log-analytics workspace create \
  --resource-group pwc-rag-rg \
  --workspace-name pwc-rag-logs \
  --location westus

# Get workspace credentials
LOG_ID=$(az monitor log-analytics workspace show \
  --resource-group pwc-rag-rg --workspace-name pwc-rag-logs \
  --query customerId -o tsv)
LOG_KEY=$(az monitor log-analytics workspace get-shared-keys \
  --resource-group pwc-rag-rg --workspace-name pwc-rag-logs \
  --query primarySharedKey -o tsv)

# Create Container Apps environment
az containerapp env create \
  --name pwc-rag-env \
  --resource-group pwc-rag-rg \
  --location westus \
  --logs-workspace-id "$LOG_ID" \
  --logs-workspace-key "$LOG_KEY"

# Create Container Registry
az acr create --name <unique-name> --resource-group pwc-rag-rg --sku Basic --admin-enabled true

# Create service principal
az ad sp create-for-rbac \
  --name "finrag-backend-deploy" \
  --role contributor \
  --scopes /subscriptions/<sub-id>/resourceGroups/pwc-rag-rg \
  --sdk-auth
# Save the output — you need clientId, clientSecret, tenantId
```

### Step 2: GitHub Setup

```bash
# Create repo (or use existing)
gh repo create finrag-backend --public

# Set all secrets (use values from Step 1)
ACR_CREDS=$(az acr credential show --name <acr-name>)
gh secret set ACR_USERNAME --body '<username>'
gh secret set ACR_PASSWORD --body '<password>'
gh secret set AZURE_CLIENT_ID --body '<clientId>'
gh secret set AZURE_CLIENT_SECRET --body '<clientSecret>'
gh secret set AZURE_TENANT_ID --body '<tenantId>'
gh secret set AZURE_SUBSCRIPTION_ID --body '<subscriptionId>'
gh secret set OPENAI_API_KEY --body '<key>'
gh secret set PINECONE_API_KEY --body '<key>'
gh secret set SUPABASE_URL --body '<url>'
gh secret set SUPABASE_KEY --body '<key>'
gh secret set AZURE_DI_ENDPOINT --body '<endpoint>'
gh secret set AZURE_DI_KEY --body '<key>'
```

### Step 3: First Deploy

The workflow uses `az containerapp update`, which requires the app to already exist. For the **very first deploy**, change `update` to `create` in the workflow and add `--environment pwc-rag-env --target-port 8000 --ingress external --registry-server <acr>.azurecr.io --registry-username ... --registry-password ...`.

After the first successful deploy, switch back to `update`.

Or create the app manually first:
```bash
az containerapp create \
  --name pwc-rag-api \
  --resource-group pwc-rag-rg \
  --environment pwc-rag-env \
  --image mcr.microsoft.com/k8se/quickstart:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 --max-replicas 1
```

Then push code to trigger the workflow, which will update the app with the real image.

### Step 4: Database

Run `supabase/migration.sql` in your Supabase SQL editor.

### Step 5: Frontend

1. Deploy to Vercel.
2. Set `NEXT_PUBLIC_API_URL` to the new Container App FQDN.
3. Update CORS in the backend to include the new Vercel URL.

### Step 6: Verify

```bash
curl https://<new-fqdn>/health
# {"status":"ok"}

curl https://<new-fqdn>/api/threads
# []
```

---

*Last updated: 2026-02-27*
