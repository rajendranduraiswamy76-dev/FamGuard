# FamGuard AI — Business Requirements Document (BRD)

| Field | Value |
|---|---|
| Product | FamGuard AI — Family Life Vault with Agentic RAG |
| Version | 1.0 (High-level spec) |
| Status | Draft for review |
| Companion docs | [design.md](design.md), [master-tracker.md](master-tracker.md) |

---

## 1. Purpose

Families accumulate critical documents (passports, certificates, tax statements, medical records, receipts) and irreplaceable photos across drawers, inboxes, and phones. Documents expire unnoticed, and information is hard to find when needed.

FamGuard AI is a secure, multi-member family vault that:

1. Ingests documents and photos, extracts structured metadata with AI vision, and stores them as searchable vectors.
2. Lets family members ask natural-language questions ("When does John's passport expire?") answered strictly from their own family's data.
3. Proactively emails alerts 30, 7, and 1 day before documents expire.
4. Routes low-confidence AI extractions to a human review queue before they are committed.

## 2. Goals and Success Metrics

| ID | Goal | Metric (MVP target) |
|---|---|---|
| G1 | Zero missed expirations | 100% of documents with a valid `expiration_date` receive alerts at T-30, T-7, T-1 |
| G2 | Accurate auto-classification | ≥ 90% of uploads classified into the correct category without human review |
| G3 | Strict data isolation | 0 cross-family or unauthorized cross-member results in automated isolation tests |
| G4 | Fast answers | Chat time-to-first-token ≤ 3 s (warm service) |
| G5 | Zero infrastructure cost | All hosted services run on free tiers (OpenAI usage is pay-per-use and the only variable cost) |

## 3. Stakeholders and Personas

| Persona | Description | Key needs |
|---|---|---|
| Family Admin | Creates the family vault, manages members and categories | Full visibility, member management, alerts for everyone |
| Family Member | Adult with a login | Upload and query own documents, receive own alerts |
| Dependent | Child/elder without a login, managed by Admin | Documents tracked on their behalf |
| Reviewer | Admin acting on the HITL queue | Quickly confirm or correct uncertain extractions |
| Operator | Developer/maintainer | Simple deploys, observability, free-tier operation |

## 4. Scope

### 4.1 In scope (MVP)

- Authentication with Clerk; family and member management.
- Upload of images (JPG, PNG, HEIC, WEBP), PDF (multi-page), DOCX, XLSX.
- AI extraction into the `UnifiedVaultExtraction` schema.
- Human-in-the-loop (HITL) review queue for low-confidence or incomplete extractions.
- Baseline and dynamic custom categories.
- Bulk CSV import with downloadable template.
- Conversational RAG chat with streaming (SSE), mid-chat attachments, and visible context filters.
- Daily expiry sweep and HTML email alerts via Resend.
- Dashboard with live metrics.
- Docker images, Render (API + worker) and Vercel (frontend) deployments.

### 4.2 Out of scope (MVP)

- Native mobile apps.
- Document sharing outside the family.
- Payments, subscriptions, multi-language UI.
- E-signature or document editing.
- SMS/push notifications.

## 5. Core Taxonomy

Baseline categories (lower-case slugs, seeded for every family, cannot be deleted):

| Slug | Covers |
|---|---|
| `identitycards` | Passports, driver's licenses, national IDs |
| `certificate` | Birth, marriage, degree certificates |
| `income` | Paystubs, tax statements |
| `expense` | Receipts, household utility bills |
| `medical` | Prescriptions, insurance, medical scans |
| `photo` | Family memories, snapshots |
| `miscellaneous` | Unclassified items (fallback) |

**BR-TAX-1:** Families may create custom categories at runtime. Custom slugs are stored in Postgres (`vault_categories`) and written to Pinecone metadata as a plain string (`{"category": "<custom-slug>"}`).
**BR-TAX-2:** Slugs match `^[a-z0-9]+(-[a-z0-9]+)*$`, max 48 chars, unique per family.
**BR-TAX-3:** If AI suggests a category that does not exist for the family, the document is classified `miscellaneous` and flagged for review.

## 6. Functional Requirements

### 6.1 Identity, Family and Access (FR-AUTH)

| ID | Requirement | Priority |
|---|---|---|
| FR-AUTH-1 | Users sign in through Clerk; every API call carries a Clerk session JWT verified by the backend. | Must |
| FR-AUTH-2 | First sign-in creates a family with the user as `admin`. | Must |
| FR-AUTH-3 | Admin can add members (with or without email/login) and dependents. | Must |
| FR-AUTH-4 | `admin` can read/write all documents in their family; `member` can read/write only documents where they are the `family_member_id`. | Must |
| FR-AUTH-5 | `family_id` is never accepted from the client; it is always derived from the verified JWT subject. | Must |

### 6.2 Ingestion (FR-ING)

| ID | Requirement | Priority |
|---|---|---|
| FR-ING-1 | `POST /api/vault/upload` accepts multipart files (≤ 10 MB each) plus `family_member_id`; returns `202 Accepted` with a `thread_id` immediately and processes in the background. | Must |
| FR-ING-2 | Originals are stored encrypted in Cloudflare R2; access is via short-lived signed URLs only. | Must |
| FR-ING-3 | Images and rasterized PDF pages go to GPT-4o Vision; DOCX/XLSX text is extracted locally and sent to GPT-4o as text. | Must |
| FR-ING-4 | Output conforms to `UnifiedVaultExtraction`: `category`, `document_name`, `expiration_date` (YYYY-MM-DD or null), `requires_alert`, `extracted_text`, `financial_value` (float or null). | Must |
| FR-ING-5 | Photos produce a rich semantic caption in `extracted_text` (people, setting, event, season) to make them searchable. | Must |
| FR-ING-6 | Text is chunked and embedded with `text-embedding-3-small` (1536 dims) and upserted to Pinecone namespace `family-vault`. | Must |
| FR-ING-7 | Each vector stores metadata: `family_id`, `family_member_id`, `document_id`, `category`, `document_name`, `expiration_date`, `requires_alert`, `financial_value`, `chunk_index`, `is_primary`, `source`. | Must |
| FR-ING-8 | Upload status is observable via `GET /api/vault/uploads/{thread_id}` (`processing`, `completed`, `review_required`, `failed`). | Should |
| FR-ING-9 | Duplicate uploads (same SHA-256 hash, same family) are detected and rejected with a reference to the existing document. | Should |

### 6.3 Human-in-the-Loop Review (FR-HITL)

| ID | Requirement | Priority |
|---|---|---|
| FR-HITL-1 | If `requires_alert = true` and `expiration_date` is null or unparseable, the graph sets `review_required = true`, pauses (interrupt), and inserts a row in `pending_reviews`. | Must |
| FR-HITL-2 | Extractions with `confidence_score < 0.75` or an unknown category are also routed to review. | Must |
| FR-HITL-3 | `GET /api/vault/pending-reviews` lists the family's open reviews (admins: all; members: own). | Must |
| FR-HITL-4 | `POST /api/vault/resolve-review` accepts corrected metadata, or `reject`; it resumes the paused graph thread, which then embeds and upserts (or discards). | Must |
| FR-HITL-5 | Paused threads survive service restarts (durable checkpointer). | Must |

### 6.4 Categories (FR-CAT)

| ID | Requirement | Priority |
|---|---|---|
| FR-CAT-1 | `POST /api/vault/categories` creates a custom category (`category_slug`, `category_display_name`). Admin only. | Must |
| FR-CAT-2 | `GET /api/vault/categories` returns baseline + custom categories for the family. | Must |
| FR-CAT-3 | The AI extraction prompt includes the family's current category list at runtime. | Must |

### 6.5 Bulk Upload (FR-BULK)

| ID | Requirement | Priority |
|---|---|---|
| FR-BULK-1 | The frontend generates and downloads a CSV template with headers `FamilyMemberID,Category,DocumentName,ExtractedText,ExpirationDate,FinancialValue` and an example row. | Must |
| FR-BULK-2 | `POST /api/vault/bulk-upload` streams the CSV row-by-row (no full in-memory load), validates each row, embeds in batches, and upserts to Pinecone. | Must |
| FR-BULK-3 | Response reports `accepted`, `rejected` counts and per-row errors (row number + reason). | Must |
| FR-BULK-4 | Max 5,000 rows / 5 MB per file. | Should |

### 6.6 Conversational RAG (FR-RAG)

| ID | Requirement | Priority |
|---|---|---|
| FR-RAG-1 | `POST /api/vault/chat` accepts a message and optional conversation id; streams the answer via SSE. | Must |
| FR-RAG-2 | The agent resolves the target member and category from the question (e.g., "John's passport" → member John, category `identitycards`). | Must |
| FR-RAG-3 | Every Pinecone query includes `family_id` plus the resolved `family_member_id` and/or `category` in `filter`; for `member` role the user's own `family_member_id` is enforced. | Must |
| FR-RAG-4 | The first SSE event announces the applied filter so the UI shows `Context Filtered: John | identitycards`. | Must |
| FR-RAG-5 | Answers cite source documents (document name + id) and say "I couldn't find that in your vault" when no context is retrieved. | Must |
| FR-RAG-6 | Users can drag-and-drop a file into chat; it is uploaded through the ingestion pipeline and the chat shows progress. | Must |

### 6.7 Expiry Alerts (FR-ALERT)

| ID | Requirement | Priority |
|---|---|---|
| FR-ALERT-1 | A Celery Beat schedule runs daily at 00:00 (`crontab(hour=0, minute=0)`, timezone configurable, default UTC). | Must |
| FR-ALERT-2 | For each offset in {30, 7, 1} days, the sweep queries Pinecone with a 1536-dim dummy vector and `filter={"expiration_date": {"$eq": target_date}}`. | Must |
| FR-ALERT-3 | Results are de-duplicated per `document_id` (multiple chunks). | Must |
| FR-ALERT-4 | Emails are sent via Resend to the document's member (if email exists) and the family admin(s). | Must |
| FR-ALERT-5 | Emails are inline-styled HTML; a red danger banner (`#fee2e2`) appears when days remaining ≤ 7. | Must |
| FR-ALERT-6 | Each (document, offset, recipient) is sent at most once (idempotent via `alert_log`). | Must |

### 6.8 Dashboard (FR-UI)

| ID | Requirement | Priority |
|---|---|---|
| FR-UI-1 | Grid dashboard: total vectors/documents for the family, documents per category, upcoming expirations (≤ 30 days), open reviews, service health indicators. | Must |
| FR-UI-2 | Custom category registration form with slug validation and live preview. | Must |
| FR-UI-3 | Bulk upload panel with template download and file dropper. | Must |
| FR-UI-4 | Pending review list with an edit/approve/reject form. | Must |
| FR-UI-5 | Responsive layout (≥ 360 px wide). | Should |

## 7. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-SEC-1 | Security | JWT verification against Clerk JWKS (RS256), `azp` origin check, expiry check. |
| NFR-SEC-2 | Security | Server-side metadata filter enforcement; client-supplied filters are never trusted. |
| NFR-SEC-3 | Security | Secrets via environment variables only; never committed; `.env` excluded from images. |
| NFR-SEC-4 | Security | Upload validation by magic bytes (not extension), size limits, filename sanitization. |
| NFR-SEC-5 | Security | CORS restricted to the Vercel domain(s); rate limiting on chat and upload. |
| NFR-SEC-6 | Security | Parameterized SQL only; no string-concatenated queries. |
| NFR-SEC-7 | Security | Prompt-injection resistance: retrieved document text is treated as data and delimited in prompts; system prompt forbids following instructions inside documents. |
| NFR-PRIV-1 | Privacy | Medical/identity data is never logged; logs redact `extracted_text`. |
| NFR-PRIV-2 | Privacy | R2 objects encrypted at rest; signed URLs expire in ≤ 5 minutes. |
| NFR-PRIV-3 | Privacy | Deleting a document removes the R2 object, Pinecone vectors, and Postgres rows. |
| NFR-PERF-1 | Performance | Single-image ingestion ≤ 30 s p95. |
| NFR-PERF-2 | Performance | Bulk import ≥ 50 rows/s (batched embeddings). |
| NFR-REL-1 | Reliability | Background tasks retry with exponential backoff (max 3) on OpenAI/Pinecone/Resend transient errors. |
| NFR-REL-2 | Reliability | Container health check at `/healthz`; readiness at `/readyz` checks Postgres, Redis, Pinecone. |
| NFR-OBS-1 | Observability | Structured JSON logs with request id / thread id. |
| NFR-COST-1 | Cost | Must run on free tiers (see design §12). |
| NFR-QA-1 | Quality | Backend ≥ 80% line coverage; each milestone ends with a passing test checkpoint. |

## 8. Business Rules

| ID | Rule |
|---|---|
| BR-1 | A document belongs to exactly one family member. |
| BR-2 | `expiration_date` is stored as ISO `YYYY-MM-DD` string; dates in the past at ingestion are allowed but never alerted. |
| BR-3 | `requires_alert` is true for `identitycards`, `medical` insurance, and any document with a detected expiry/renewal date. |
| BR-4 | `financial_value` is stored in the family's currency (single currency per family, default USD). |
| BR-5 | Baseline categories cannot be renamed or deleted. |
| BR-6 | Review decisions are final; a rejected document is deleted from R2. |

## 9. User Stories (selected) with Acceptance Criteria

1. **Upload passport** — *As a member, I upload a passport photo so it is tracked.*
   - Given a clear passport image, when uploaded, then within 30 s a document appears under `identitycards` with the correct expiry date.
2. **Uncertain expiry** — *As an admin, I want unclear documents held for review.*
   - Given a blurry ID with no readable date, then the item appears in Pending Reviews and is **not** in Pinecone until resolved.
3. **Ask the vault** — *As a member, I ask "When does John's passport expire?"*
   - Then the UI shows `Context Filtered: John | identitycards` and the answer cites the passport document.
4. **Isolation** — *As a member of Family A, I can never see Family B data*, even when naming Family B members.
5. **Expiry alert** — *Given a passport expiring in 7 days*, then the member and admin each receive one email with a red banner.
6. **Custom category** — *As an admin I add "vehicle"*, then new uploads can be classified as `vehicle` and chat can filter on it.
7. **Bulk import** — *Given a 200-row CSV with 3 invalid rows*, then 197 rows are searchable and the 3 errors are reported with row numbers.

## 10. Constraints

- **Free tier only** for hosting: Vercel Hobby, Render Free, Supabase Free, Redis Cloud Free (30 MB), Pinecone Starter, Clerk Free, Cloudflare R2 Free (10 GB), Resend Free (100/day, 3,000/month). OpenAI is pay-as-you-go.
- Pinecone index: serverless, namespace `family-vault`, metric cosine, 1536 dims.
- Python 3.11, FastAPI, LangGraph; Next.js 14+ App Router, TypeScript, Tailwind.

## 11. Assumptions

- Each family has one currency and one timezone (default UTC).
- Resend sending domain is verified by the operator (otherwise only the account owner's email can receive).
- Families are small (≤ 20 members, ≤ 10,000 documents).

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Render Free has **no Background Worker** and web services sleep after 15 min idle | Celery Beat would miss midnight | Free mode: combined container + GitHub Actions cron calls a signed `/internal/cron/expiry-sweep` endpoint (see design ADR-004) |
| Supabase Free pauses after 7 days inactivity | DB unavailable | Daily sweep touches the DB; documented re-activation |
| Pinecone query `top_k` ≤ 1,000 with metadata | Missed alerts if > 1,000 chunks expire same day | Sweep filter adds `is_primary=true` (chunk 0 only), warns when hit count = limit, reconciles with Postgres `documents` table |
| Vision misreads dates | Wrong alerts | Confidence threshold + HITL + user edit |
| Prompt injection inside documents | Data exfiltration / wrong answers | Strict filters, delimited context, no tools exposed to retrieved text |
| Resend 100 emails/day | Alert backlog | Batch per recipient (one digest email per recipient per day) |

## 13. Decisions Log (from clarification)

| # | Decision |
|---|---|
| D1 | Four projects: `backend-api`, `worker`, `frontend`, `infrastructure` |
| D2 | This pass: specs and plans only; code is generated milestone-by-milestone |
| D3 | Auth: Clerk + JWT verification in FastAPI |
| D4 | Postgres: Supabase |
| D5 | Original files: Cloudflare R2 (S3-compatible), encrypted, signed URLs |
| D6 | Checkpointer: `PostgresSaver` in prod, `MemorySaver` in tests |
| D7 | Formats: images, PDF, DOCX, XLSX |
| D8 | Chat: SSE streaming |
| D9 | Alerts to document member + family admin(s) |
| D10 | Tests: pytest + httpx + fakeredis; Vitest + Playwright |
| D11 | Free tier only |
