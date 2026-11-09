This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

## Document uploads and categories

Every tab uses the same light-blue page background (`#e6f2ff`), light-blue content
panels (`#f0f7ff`), charcoal text, and
navy text with blue navigation, buttons, and focus accents. System/Segoe UI typography,
consistent control heights, and a separate navigation row keep layouts aligned.
Semantic warning/error/success colours remain distinct.
Navigation tabs are light blue, with a stronger blue (`#cfe5ff`) for the selected
tab and hover state. Input fields remain white for readability.
All screens share an enterprise-style top header, keyboard skip link, bounded
content width, consistent page headings and panel spacing. Primary accents use
blue (`#1d4ed8`) and page text uses navy (`#0f172a`). Forms have aligned field
grids and action rows; wide tables scroll within their cards rather than the
page. Document metadata and file selection sit side-by-side on desktop; Finance
income and bill panels share a desktop row. Mobile screens stack these columns.
Warnings, errors and success states retain their semantic colours.
Navigation is **Family Vault Query**, **Uploads**, **Income & Expenses Data**,
**Finance Dashboard**, **Approval Reviews**, **Users & Categories management**
(admin-only), then **Service health** last. URLs and permissions are unchanged.
**Users & Categories management** (`/users`) now contains family member
management and category creation (admin-only). Spouse, Child, and Others are
optional **Relationship** labels, separate from Admin/Member/Dependent access
roles. Existing users retain their permissions and start with no relationship.

Uploads offers **Category** directly below **Document owner**. **Auto-detect**
preserves AI categorization; selecting an available category stores the choice
with the upload and applies it before review/indexing. Reviewers can still correct
metadata. Unknown or other-family categories are rejected by the API.

**Document details**, below Category, accepts an optional description (up to
2,000 characters): purpose, reference numbers, aliases, or useful search terms.
The same details apply to every selected file in a batch. FastAPI stores
`document_details` with the document's owner and requested category, returns it
in upload history/status, and preserves it through processing and review.
Ingestion indexes the owner name, final category, document name, description,
and extracted content together. Description and owner metadata accompany every
chunk for retrieval/answers, including Pinecone document-schema indexes.
Descriptions are labeled as uploader-provided context, not verified extracted
facts, and do not override OCR, expiry extraction, or review decisions.
The original file remains unchanged. Details clear after successful upload,
but remain available to retry after an error. Existing documents default to
empty details; this change does not automatically reindex older uploads.

The dashboard retains near-term expiry alerts, reviews, and chat, and adds
**Expiring in the next 1 year** for expiries 30–364 days away. Day 30 intentionally
appears in both the existing inclusive 30-day view and the year view.
Document totals, Pinecone vector totals, and By category have moved to
**Service health**. Both expiry lists use the family's timezone and access scope.
On desktop, two equally sized summary cards sit above the expiry lists in a
compact left column, aligned at the top with the wider vault assistant.
On smaller screens these columns stack without stretching summary cards across
the desktop page.
Backend schema migration adds nullable relationship and requested-category columns
without changing existing member roles or document categories.

Open **Uploads** in the main navigation (`/uploads`) to upload multiple PDFs,
DOCX documents, XLSX spreadsheets, or JPEG/PNG/WebP/HEIC/HEIF images. Choose the
document owner, select or drop up to 10 files (10 MB each), and click **Upload
documents**. PDFs are limited to 10 pages. Admins can upload for any family
member; other members can upload only for themselves.

Files are sent directly to FastAPI using authenticated multipart
`POST /api/vault/upload` with `files`, `family_member_id`, and `source=upload`.
Acceptance means processing has been queued, not completed. The backend checks
file contents, extracts metadata and categories, and may require approval in
**Reviews**. Legacy DOC/XLS, text files, and other unsupported formats must be
converted to a supported format first.

**Uploads > Maintenance** has **Filter by document owner** and **Filter by category**
dropdowns. Select either or both to list matching uploads (including older
uploads and chat attachments). With both dropdowns set to All, no records are
displayed or fetched; select at least one specific owner or category.
Filtering happens in FastAPI using `family_member_id` and `category` on
`GET /api/vault/uploads`; `limit` and `offset` support Previous/Next pages of
50 documents, with no latest-50 cutoff. Changing a filter starts at page one.
Admins can select family members; others can only view their own documents.
The list
shows processing, approval, indexing, and failure status; pending documents
refresh every 5 seconds. Only indexed documents are ready for chat search.
Use **Review document** to approve held extraction, or **Open original** to
retrieve the stored file through an authorized signed URL. Failed documents
show their processing error and can be uploaded again after resolving it.

Under **Uploads > Maintenance**, choose **Delete** beside a document and
**Confirm delete** to permanently remove its original, search entries, and
document record using authenticated `DELETE /api/vault/documents/{document_id}`.
Cancel makes no changes. Errors keep the document listed for retry. Admins may
delete family documents; other members may delete only their own. Processing
or held documents cannot be deleted while ingestion is unsettled: finish
processing, or approve/reject the held document in Reviews first.

If chat repeats incorrect OCR (including plausible-looking but wrong passport
dates), choose **Review extraction** on a completed upload in Maintenance.
Confirm to keep the original but remove its search entries and open a new
correction review. In **Reviews**, compare against **Open original**, edit the
**Extracted text** (including birth/issue dates) and **Expiry date** separately,
then approve. Only approval reindexes the corrected text and restores search.
Existing extraction/review threads are retained as history. This does not
re-run OCR or send the original for another AI extraction. Chat also checks
current database status and correction index version so stale Pinecone results
for held/deleted documents or previous extractions cannot be used. The confidence
badge is a model estimate, not verification.

In local development, `FAKE_EXTERNALS=1` with `LOCAL_STORAGE_DIR` now stores
search vectors in `.vectors.sqlite3` on the same shared volume as originals.
API and worker containers must mount that same directory. This prevents vectors
from being lost across processes or restarts. Production continues using
Pinecone. Offline embeddings/extraction are test substitutes, not real AI.

### Enable real OpenAI and Pinecone

Merge the entries from `infrastructure/.env.example` into the existing ignored
`infrastructure/.env`, preserving the Clerk settings. Enter `OPENAI_API_KEY` and
`PINECONE_API_KEY` locally, set `PINECONE_INDEX=famguard-vault` and
`PINECONE_NAMESPACE=family-vault`, then change `FAKE_EXTERNALS=0`. The Pinecone
index must use dimension **1536** and **cosine** similarity. The backend uses
`text-embedding-3-small` for embeddings and GPT-4o/GPT-4o-mini for extraction/chat.
The OpenAI project must have billing and access to those models.

From the workspace root, recreate containers to apply environment changes
(a restart alone does not apply them):

```powershell
docker compose -f infrastructure\docker-compose.yml --env-file infrastructure\.env up -d --force-recreate api worker
# Only if you also use the optional combined service:
docker compose -f infrastructure\docker-compose.yml --env-file infrastructure\.env --profile combined up -d --force-recreate combined
Invoke-RestMethod http://localhost:8000/readyz
```

Local originals stay on the existing volume, while vectors now go to Pinecone.
Mail stays in Mailpit. Missing/placeholder keys fail explicitly when real mode is
enabled. Do not use `docker compose down -v`, which deletes local data.

On Render, enter the same keys in the backend service's **Environment** settings,
set `PINECONE_INDEX=famguard-vault`, `PINECONE_NAMESPACE=family-vault`,
`FAKE_EXTERNALS=0`, and keep `ENV=prod`. Save and redeploy. A separate production
worker needs the same settings; the default combined service needs them only
once. Production still requires the existing R2, Clerk, database, Redis, and
email configuration. Do not set `LOCAL_STORAGE_DIR` or `SMTP_URL` in production.

Never put these API keys in `frontend/.env.local`, Vercel client variables, or
any `NEXT_PUBLIC_*` variable. Frontend configuration only points to the backend.
Check **Service health**, then upload a new document, wait for **Indexed**, and
test chat. Switching from offline to real mode does **not** migrate existing
fake vectors into Pinecone or redo fake extraction. Existing documents need a
separate reindex/re-extraction step; merely changing keys does not make them
searchable in the new index.

Pinecone document-schema indexes are also supported with Pinecone Python SDK
10.2+. FamGuard detects the schema and uses the Documents API for chunk upserts,
search, prefix listing, and deletion instead of the vectors API. The schema
must have exactly one dense-vector or embedded-string field, with 1536 dimensions
and cosine similarity. For your `chunk_text` embedded field, Pinecone embeds text
on write and query; FamGuard does not also request OpenAI embeddings. OpenAI
still handles extraction and chat answers. Stored chunk text and document metadata
remain available to chat, and family/member filters remain mandatory.

Embedded text must use `store_text=true`. Vault filter fields such as `family_id`,
`family_member_id`, and `category` must remain ordinary filterable metadata,
not full-text/embedding fields. Optional full-text `title` receives the document
name. Schemas with multiple dense/embedded fields or sparse embeddings fail
explicitly rather than silently choosing a field. Standard dense-vector indexes
continue using the original vectors API.

## Income and expense data

**Income & Expenses Data** (`/financial-data`) contains financial table/Excel
uploads, separate from the document-only **Uploads** tab.
Document Maintenance shows no records until an owner or category filter is selected.

Enter rows with **Name of the person**, **Category** (Income or Expense),
**Subcategory** (Salary, Groceries, Rent, etc.), **Amount**, **Currency**, and
**Details**. Add/remove rows, then choose **Save table entries** (up to 100 rows
in the GUI). Amount must be positive, below 1 trillion, and use at most two
decimal places; details are required and limited to 2,000 characters.
Amounts retain the chosen currency; no conversion or cross-currency total is
performed. The family's currency is selected initially when supported.
Person and Income/Expense are controlled dropdowns. Add people in Users &
Categories management (admin-only). Subcategory uses an editable suggestion
list populated with defaults and your family's saved entries; typing a new
subcategory saves it for future suggestions. Currency is also editable with
validated currency-code suggestions, including additional codes such as NOK.
Amount and Details remain free-entry fields.

**Transaction date**, **Payment due date**, and **Paid** support the Finance
tab. Leave transaction date blank only for undated records. For upcoming bills,
choose Expense, enter a payment due date, and set Paid = No. Unpaid bills do not
count as actual spending.

**Download sample Excel** returns a real `.xlsx` workbook with an **Entries**
table, two replaceable example rows, person/category/subcategory/currency/Paid dropdowns, and an
**Instructions** sheet. Replace/delete sample rows, fill the table, select the
file, then choose **Upload Excel data**. Preserve the first six headers below,
followed by **Transaction date**, **Payment due date**, and **Paid**:

| Name of the person | Category | Subcategory | Amount | Currency | Details |
| --- | --- | --- | --- | --- | --- |
| Your family member name | income | Salary | 5000.00 | SGD | Monthly salary |
| Your family member name | expense | Groceries | 125.50 | SGD | Household groceries |

Excel supports 5,000 rows and files up to 5 MB. Formulas are rejected; paste
values only. Person names must match unique family members; non-admins may
submit only their own data. Supported currency codes are listed in the
currency suggestions and workbook dropdown. New subcategories are permitted;
unknown currency codes are rejected. Old six-column workbooks remain valid and
import as paid, undated entries.

Authenticated FastAPI endpoints:
- `GET /api/vault/financial/template`: roster-aware Excel workbook download.
- `POST /api/vault/financial/entries`: `{rows: [{person_name, category,
  subcategory, amount, currency, details, transaction_date, payment_due_date, is_paid}]}`.
- `POST /api/vault/financial/excel`: multipart `file`.
- `GET /api/vault/financial/entries`: filtered records, up to 50 per page with offset.
- `PUT /api/vault/financial/entries/{id}`: validated full-entry update, including owner.
- `DELETE /api/vault/documents/{id}`: shared confirmed deletion of records and search vectors.

Financial **Maintenance** starts empty. Filter by person, Income/Expense,
subcategory, currency, transaction date range, payment due date range or Paid
status (filters combine with AND). Edit all saved fields or confirm permanent
deletion. Changes refresh the filtered list, suggestions and subsequent Finance
views. Updates reindex searchable text with a new correction version; stale
versions are excluded from retrieval. Failed indexing leaves the saved entry
unchanged and reports an error for retry. Non-admin members maintain only their
own entries; admins can maintain entries within their family.

Both entry methods use the existing bulk embedding/indexing pipeline. Each
record persists the owner, category, exact decimal amount, currency, subcategory,
and details, along with dates and payment status. Search text includes the financial context.
These are structured financial records, not uploaded originals, so they do not
change the document Maintenance list. Import reports distinguish saved/rejected
rows and identify errors (Excel uses worksheet row numbers). Accepted table
rows clear; rejected table rows remain for correction. For partial Excel
imports, remove accepted rows before retrying. Uploading the same transaction
again creates another record; there is no automatic duplicate detection.

The legacy `POST /api/vault/bulk-upload` CSV API remains compatible but is no
longer displayed in Uploads. Category management uses
`GET/POST /api/vault/categories`. Chat attachments remain available on Dashboard.

## Finance dashboard

Open **Finance Dashboard** (`/finance`) to select **Past 6 months**, **Current month**, or
**Projected next 3 months**. Income, expenses and net balance stay separate by
currency. Actuals include paid financial entries with transaction dates up to
today in the family's timezone. Future-dated entries are not actuals yet.

The default visual is a labelled donut chart: choose **Chart month** and
**Chart currency** to compare income and expenses for one month and currency.
The percentages are shares of income plus expenses, not a savings rate.
Income, expenses and net balance have separate amount cards; net is not a slice.
Zero activity and insufficient history show a neutral ring with an explanation,
not invented proportions. Negative amounts retain their totals but are not
plotted as proportions. **View detailed monthly totals** expands the underlying
table for all months in the selected view. Charts have accessible text labels.

Projections average up to six completed months for each currency, beginning
with its earliest recorded month within that window. Zero-activity intervening
months are included; the current partial month, undated entries, and unpaid
bills are excluded. No history shows **Insufficient history**, not a guessed
amount. Projections are estimates, not recurring scheduled transactions.

**Bill payments due in the next 30 days** lists unpaid expenses with a due date
from today through day 30 inclusive, ordered by due date. These come from
explicitly entered bills, not projected averages; no payment is made by this app.
The payment schedule combines recorded and estimated payments in due-date order,
with owner, expense category, details, currency/amount and distinct status badges.
Due-today and next-seven-day badges highlight near-term payments. Counts distinguish
recorded unpaid from estimated payments; currencies are not summed together.
**Upcoming income** and **Bill payments due in the next 30 days** automatically
repeat entries whose transaction date falls in the previous calendar month,
including settled entries. Undated entries need a transaction date before they
can establish history. The amount, currency, person and details are retained.
Use the Payment due day when supplied, otherwise the transaction day. Days repeat
monthly through today + 30 days inclusive, using the last day of shorter months.
Income and payments are displayed separately and clearly marked Estimated.
Matching recorded entries (same person, category, subcategory, currency and due
date) replace estimates one-for-one, even if settled or their amount changed.
No transactions are inserted, no actual totals change, and refreshing is safe.
Identical estimates (same person, category, subcategory, currency, due date,
amount and details) appear once even if history contains repeated uploads.
Different amounts, dates or details remain separate. Recorded unpaid bills
replace matching estimates even when their transaction date was last month.
This is display-only deduplication; saved entries and actual totals are unchanged.
These monthly assumptions are not guaranteed recurring transactions.
**Undated entries** preserves existing financial records without inventing dates
and lets authorized users assign their actual transaction date (50 at a time).
They remain excluded from monthly totals until dated. Dating updates the
financial dashboard record; it does not re-run embedding of the original text.

`GET /api/vault/financial/options` supplies scoped suggestions;
`GET /api/vault/financial/dashboard` supplies monthly/bill/undated views;
`PUT /api/vault/financial/entries/{id}/date` assigns a date.
Admins see their family's records; other members see only their own.

## Service health

Open **Service health** in the main navigation (`/health`) to view database,
processing queue, and vector index status. The page polls FastAPI's `GET /readyz`
every 30 seconds. Health polling no longer runs on the dashboard.

## Family user management

Sign in as a family admin and open [Users & Categories management](http://localhost:3000/users), or use
the **Users & Categories management** navigation link. The dedicated page lists the family roster and
supports creating, editing, and deleting family members and dependents.

- Names must be unique within the family and contain 1-80 characters.
- Admins and members require an email; dependents do not require an email or login.
- A matching email is linked when a new user first signs in. Creating a member
  does not create a Clerk account or send an invitation.
- Editing a linked user's contact email does not modify their Clerk login.
  A user with a linked login cannot be changed to a dependent.
- Admins cannot delete themselves or change their own admin role.
- Deletion is blocked when the member owns documents or any review records.
  It removes family membership only, not the Clerk account. A removed user who
  signs in again may be onboarded into a new family.

The page uses `GET /api/me`, `POST /api/members`,
`PUT /api/members/{member_id}`, and `DELETE /api/members/{member_id}`.
All mutations are admin-only and scoped to the authenticated family.

The local Docker Compose stack mounts backend source read-only. After backend
code changes, restart the API/worker (and combined service if enabled); rebuild
the image only when dependencies or Docker packaging change. Production images
still package backend source normally.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
