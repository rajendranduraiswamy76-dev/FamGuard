# FamGuard AI — Technologies and Components

This page records the technologies and their responsibilities as implemented in the repository. Exact package versions and supported ranges are maintained in the project manifests.

## Application stack

| Area | Technology | How the project uses it |
|---|---|---|
| Web application | Next.js 16 App Router, React 19, TypeScript | Family vault, uploads, chat, approval reviews, users/categories, financial data, dashboard, and health screens. |
| Web styling and validation | Tailwind CSS 4, Zod | UI styling and client-side schema/form validation. |
| Authentication | Clerk (`@clerk/nextjs`) | Sign-in/session integration in the frontend; API verifies Clerk JWTs using configured JWKS and issuer. |
| API | Python 3.11, FastAPI, Uvicorn | Versioned REST endpoints, request/response validation, server-sent chat events, and health/readiness endpoints. |
| Data validation/config | Pydantic 2, `pydantic-settings` | API schemas, AI extraction models, review payloads, and environment-driven configuration. |
| AI orchestration | LangGraph, LangChain Core, `langchain-openai` | Durable ingestion graph with human review interrupts and a streaming retrieval/answer graph. |
| Language models | OpenAI GPT-4o and GPT-4o-mini | GPT-4o extracts structured document metadata and generates chat answers; GPT-4o-mini resolves the chat's member/category context. |
| Embeddings | OpenAI `text-embedding-3-small`; `tiktoken` | 1536-dimensional embeddings and 800-token chunks with 100-token overlap for the default dense-vector index. |
| Background processing | Celery, Redis | Asynchronous ingestion/review-resume tasks, task results, and scheduling in worker mode. |

## Storage, integrations, and operations

| Area | Technology | How the project uses it |
|---|---|---|
| Relational database | PostgreSQL; `asyncpg`, Psycopg 3 | Family/member, category, document, review, alert-log, and financial records; LangGraph checkpoint storage. |
| Vector search | Pinecone | Similarity search over document chunks. The bootstrap utility provisions/validates a 1536-dimension cosine serverless index; the adapter also supports integrated-embedding schemas. |
| Original-file storage | Cloudflare R2 through the S3-compatible `boto3` client | Stores originals and issues short-lived signed download URLs. Local mode can use a filesystem-backed adapter. |
| Notifications | Resend | Sends grouped HTML expiry notifications; SMTP can be directed to Mailpit in local development. |
| Deployment | Docker; Render Blueprint; Vercel setup | Shared container supports API, worker, and combined run modes. The checked-in Render Blueprint uses combined mode; frontend deployment guidance is in `infrastructure/VERCEL_SETUP.md`. |
| Local dependencies | Docker Compose, PostgreSQL, Redis, Mailpit | Reproducible local services; fake external integrations are enabled by default in the Compose configuration. |
| Quality tooling | pytest, Ruff, mypy; Vitest, ESLint, TypeScript, Playwright | Backend/worker tests and static checks; frontend unit, accessibility, and browser tests. |

## Code ownership map

| Project area | Main responsibility | Implementation |
|---|---|---|
| `frontend/src/app` | Routes and user experiences | `components/` contains the vault assistant, upload/review screens, user/category management, and finance views. |
| `backend-api/app/api` | HTTP API | `routes/` groups family, vault, document, chat, financial, and operations endpoints. |
| `backend-api/app/agents` | AI workflows | `extractor.py`, `graph.py`, `rag_engine.py`, `prompts.py`, and `state.py`. |
| `backend-api/app` | Domain and adapters | `repositories.py`, `database.py`, `parsers.py`, `embeddings.py`, `vectorstore.py`, `storage.py`, `financial.py`, and `security.py`. |
| `worker/famguard_worker` | Asynchronous jobs | Celery app, ingestion tasks, expiry sweep, email sender/templates, and worker runtime. |
| `infrastructure` | Build and deployment | Docker configuration, Compose, migrations, Pinecone setup, and signed cron-trigger utility. |

For behavior and data flow, see [the functional design](requirements.md), [the technical design](design.md), and [the RAG/agent one-pager](rag-agent-one-pager.md).
