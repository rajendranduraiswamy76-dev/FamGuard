# FamGuard AI — Functional Design

| Field | Value |
|---|---|
| Status | Current implementation reference |
| Source of truth | Shipped application code and tests |
| Technical design | [design.md](design.md) |
| Technologies and components | [technology-stack.md](technology-stack.md) |
| RAG and AI agent one-pager | [rag-agent-one-pager.md](rag-agent-one-pager.md) |

This document describes behavior present in the repository. Hosting plans, throughput, response-time targets, and third-party account limits depend on the deployment and are not product guarantees.

## 1. Product purpose

FamGuard is a private, multi-member family vault for storing, classifying, searching, and managing documents and financial records. AI assists with document extraction and natural-language retrieval; family members remain in control of uncertain metadata through a review workflow.

## 2. People and access

| Role | Functional access |
|---|---|
| Admin | Manage the family roster and categories; access family documents and reviews; use the vault, financial views, and chat. |
| Member | Work with their own member-scoped records; chat retrieval is always restricted to their own member ID. |
| Dependent | A family roster entry without a linked login; an admin can manage documents on their behalf. |

Sign-in is provided by Clerk. The API derives family and member identity from the verified session, not from a client-supplied family ID. The backend enforces role and member access for API operations and search.

## 3. Main user journeys

### 3.1 Store a document

1. A signed-in user uploads a supported image (JPEG, PNG, WEBP, HEIC), PDF, DOCX, or XLSX file for a family member.
2. The API validates file content and size, sanitizes the filename, stores the original, creates a processing record, and queues background work. Chat attachments use this same upload path.
3. The worker extracts metadata and searchable text. Images and PDF pages are prepared as images; DOCX and XLSX content is read as text.
4. The record becomes searchable after it passes validation and its chunks are indexed. Upload status is available in the UI and API.

### 3.2 Review uncertain metadata

When an extraction has a confidence score below `0.75`, an alert-required date is missing or unparseable, or its category is not allowed, the item is held for review and is not indexed yet. An authorized reviewer can approve, correct, or reject it. Approval resumes ingestion; rejection discards the original and marks the record rejected. A previously completed upload can also be reopened for metadata review.

### 3.3 Find information with the vault assistant

Users ask a natural-language question in the Family Vault Query screen. The assistant shows the resolved member/category filter, streams the answer, and displays source documents when retrieval returns them. Users can attach documents in chat; attachments are ingested asynchronously and do not become searchable until processing completes. Chat history is held in the current browser session; the API does not persist conversational turns.

### 3.4 Manage money records

Users can import income and expense entries from structured rows or an Excel workbook, and download an Excel template. They can filter and page through records, edit entries, and correct transaction dates. The finance dashboard presents historical/current totals by currency, explicitly labeled projections based on prior data, upcoming unpaid bills, expected cashflows, and undated records. Currency totals are kept separate.

### 3.5 Track expirations

Documents marked as requiring alerts are checked at 30, 7, and 1 day before expiry. Notifications are grouped for recipients and recorded to prevent duplicate sends. The dashboard also surfaces upcoming expirations, document status, category counts, and open reviews.

## 4. Functional capabilities and API surface

All `/api` endpoints require a valid session unless identified as an operational endpoint. Exact request and response schemas are defined by FastAPI and its generated OpenAPI schema.

| Capability | API routes |
|---|---|
| Identity and family | `GET /api/me`; admin member create, update, and delete at `/api/members` |
| Vault uploads | `POST /api/vault/upload`, `POST /api/vault/bulk-upload`, `GET /api/vault/uploads`, `GET /api/vault/uploads/{thread_id}` |
| Review and categories | `GET /api/vault/pending-reviews`, `POST /api/vault/resolve-review`, `POST /api/vault/documents/{document_id}/review`, `GET/POST /api/vault/categories` |
| Documents and metrics | `GET /api/vault/metrics`, `GET /api/vault/documents/{document_id}/url`, `DELETE /api/vault/documents/{document_id}` |
| Assistant | `POST /api/vault/chat` (server-sent events) |
| Financial data | `/api/vault/financial/entries`, `/options`, `/dashboard`, `/template`, `/excel`, and entry update/date routes |
| Operations | `GET /healthz`, `GET /readyz`; signed `POST /internal/cron/expiry-sweep` |

## 5. Business rules

- Every document belongs to one family and one family member; family scope comes from the authenticated principal.
- Baseline categories are shared and custom categories are family-scoped. Slugs are lowercase alphanumeric segments separated by hyphens.
- Unknown extraction categories fall back to `miscellaneous` and require review.
- Only completed, currently indexed documents are eligible as chat sources; held, deleted, stale-version, or out-of-scope records are excluded.
- Rejected uploads are not searchable. Deleting a document removes its vectors, stored original (if present), and database record.
- Financial entries are scoped to the current family and role. Entries are stored and summarized per currency; projections are estimates, not guaranteed cashflows.
- Alerts are idempotent per document, lead-time offset, and recipient.

## 6. User-visible states

Upload and review states include `processing`, `review_required`, `completed`, `failed`, and `rejected`. The UI shows upload progress and review status, applied chat filters, streaming answer text, and returned document sources. If no eligible chat context is found, the assistant tells the user it could not find the information in the vault.

## 7. Quality and trust behavior

- Family and member boundaries are enforced server-side for database access and vector retrieval.
- Supported uploads are identified from file contents, not only their extension; originals are accessed through short-lived signed URLs.
- Retrieved document text is treated as untrusted context. The answer model has no tools for acting on document instructions.
- Chat, upload, and financial bulk-import endpoints use configured rate limits.
- Operational readiness checks Postgres, Redis, and Pinecone; structured logs carry request/task context and avoid logging extracted document contents.

## 8. Scope notes

The implementation includes document and photo vault workflows, family/member management, categories, CSV-style bulk document import, chat-assisted retrieval, expiry notifications, and income/expense tracking. Mobile apps, external document sharing, payments, and SMS/push notifications are not implemented in the current repository.
