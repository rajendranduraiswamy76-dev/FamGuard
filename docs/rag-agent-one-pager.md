# FamGuard AI — RAG Setup and AI Agent Integration

## Purpose

FamGuard's vault assistant answers questions using eligible documents belonging to the signed-in user's family. It resolves the question context, retrieves relevant chunks, checks the results against current database state, then streams a grounded answer with source documents. It is not a general-purpose tool-using agent: the answer model cannot execute actions or invoke tools.

## Setup

1. **Start local services** from the repository root:

   ```powershell
   docker compose -f infrastructure/docker-compose.yml up -d --build
   ```

   This starts PostgreSQL, Redis, Mailpit, the API, and the worker. The Compose default enables `FAKE_EXTERNALS`, which is suitable for local UI and workflow checks but does not measure real model quality.

2. **Configure real integrations** in `infrastructure/.env` for an external-model run: set `FAKE_EXTERNALS=0` and provide `OPENAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX`, and the required database, storage, and Clerk settings. Keep secrets out of source control. The API requires valid Clerk configuration for authenticated `/api/*` use.

3. **Provision/verify the default vector index** with `PINECONE_API_KEY` available in the environment:

   ```powershell
   python infrastructure/scripts/bootstrap_pinecone.py
   ```

   Run this with the project's backend dependencies installed. The bootstrap utility creates or validates the configured serverless index (default `famguard-vault`, 1536 dimensions, cosine metric). FamGuard uses the `family-vault` namespace by default.

4. **Run the frontend** with Node.js 22.12 or newer, the frontend environment values configured as described in `frontend/.env.example`, and the API URL set to the local API:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

   Clerk keys and a valid Clerk application are required to sign in and use authenticated API routes.

## How documents enter RAG

`POST /api/vault/upload` (including chat attachments) validates supported file content, stores the original, creates a processing row, and queues a Celery task. The LangGraph ingestion workflow prepares the content, calls GPT-4o for structured metadata, and routes uncertain results to a durable human-review interrupt. A completed/approved document is split into 800-token chunks with 100-token overlap, embedded with `text-embedding-3-small` (1536 dimensions) unless the selected Pinecone schema embeds text itself, and upserted with family/member/document/category metadata. Pending review items are not indexed.

## How a question is answered

1. The frontend calls `POST /api/vault/chat` with the question and reads Server-Sent Events.
2. The RAG graph resolves an optional family member and category using GPT-4o-mini structured output. In fake mode a deterministic keyword resolver is used.
3. The server builds the Pinecone filter. Every query pins `family_id` from the authenticated principal; non-admin users are additionally pinned to their own member ID. A client cannot supply or override these filters.
4. The question is embedded when required by the configured index, and Pinecone returns up to eight candidates.
5. Before generation, candidate IDs are checked against Postgres for family/member/category scope, completed/searchable state, and matching index version. This filters stale Pinecone results after deletion or reindexing.
6. GPT-4o streams an answer from the retrieved text. Retrieved content is marked as untrusted data in the prompt. If no eligible context remains, the graph streams the fixed not-found response instead.

The event sequence is `filter`, zero or more `token` events, `sources`, and `done`; `error` may be emitted on failure. The UI presents the resolved filter and source-document names. Conversation turns are not persisted by the API; the optional conversation ID is returned in the terminal event.

## Agent components at a glance

| Component | Model / framework | Responsibility |
|---|---|---|
| Ingestion agent | LangGraph + GPT-4o | Extract structured metadata; validate confidence/date/category; pause for human review; embed and index approved content. |
| Context resolver | LangGraph node + GPT-4o-mini | Map natural-language references to a member from the caller's family roster and a valid category. |
| Retriever | Embedder + Pinecone adapter | Perform tenant-filtered nearest-neighbor search and reject stale/ineligible records using Postgres. |
| Answer generator | GPT-4o streaming | Answer from retrieved content and emit source references; has no tools. |
| Checkpointer | LangGraph Postgres saver | Persist ingestion state across worker restarts and review pauses. Tests use an in-memory saver. |

## Key implementation references

- Ingestion graph and review resume: `backend-api/app/agents/graph.py`
- RAG workflow, SSE events, and model adapters: `backend-api/app/agents/rag_engine.py`, `backend-api/app/api/routes/chat.py`
- Chunking and embeddings: `backend-api/app/embeddings.py`
- Pinecone records, filters, and adapters: `backend-api/app/vectorstore.py`
- Local services: `infrastructure/docker-compose.yml`
- Full architecture: [technical design](design.md); user-facing behavior: [functional design](requirements.md)
