# frontend — Milestone Implementation Plan

Project: Next.js 16 App Router (Cache Components on), React 19, TypeScript (strict), Tailwind 4, Clerk Core 3 (`frontend/`). Specs: [requirements](../docs/requirements.md) · [design](../docs/design.md) · [master tracker](../docs/master-tracker.md)

**Definition of done:** `npm run lint`, `npm run typecheck`, `npm test` (Vitest) green; listed checkpoint passes. API is mocked with MSW (Vitest) and `page.route` (Playwright) until IC-4 (live staging). Requires Node ≥ 22.12 (Vitest 5).

**Auth modes:** `NEXT_PUBLIC_AUTH_MODE=clerk` (default; `src/proxy.ts` runs `clerkMiddleware`) or `test` (no Clerk, static placeholder token; the real API rejects it, so a misconfiguration fails closed).

---

## F0 — Scaffold

| ID | Task | Checkpoint |
|---|---|---|
| FE-0.1 [x] | `create-next-app` (TS, App Router, `src/`, Tailwind, ESLint), `strict: true`, path alias `@/` | `npm run build` succeeds |
| FE-0.2 [x] | Vitest + Testing Library + jsdom + MSW; Playwright config | `npm test` runs a sample test; `npx playwright test` runs smoke against `next start` |
| FE-0.3 [x] | Env validation (`src/lib/env.ts`, zod) for `NEXT_PUBLIC_API_BASE_URL`, Clerk keys | Missing var → build-time error in test |

## F1 — Auth and Layout

| ID | Task | Checkpoint |
|---|---|---|
| FE-1.1 [~] | Clerk provider (inside `<body>`, non-dynamic so pages stay static), `src/proxy.ts` (Next 16 rename of middleware) protecting all routes except sign-in/up | Clerk-mode production build passes; redirect check needs real Clerk keys (INF-3.5) |
| FE-1.2 [x] | `layout.tsx` shell: header, nav (Dashboard, Reviews), user button, family badge | Covered by E2E (header + family badge render) |

## F2 — API Client and Types  *(generated from backend-api/openapi.json)*

| ID | Task | Checkpoint |
|---|---|---|
| FE-2.1 [x] | `src/lib/openapi-schema.ts` generated with `openapi-typescript`; response aliases in `src/lib/types.ts` use the generated schemas | `npm run generate:api-types` and `npm run typecheck` |
| FE-2.2 [x] | `src/lib/api.ts`: typed `apiFetch` adding Clerk bearer token, error envelope → `ApiError` | MSW tests: 401 → `ApiError{status:401}`; 422 details surfaced |
| FE-2.3 [x] | SSE client over `fetch` + `ReadableStream` (POST body supported), parses `event:`/`data:` frames split across chunks, abort support | Unit test feeds fragmented chunks → events `filter, token×n, sources, done` in order |

## F3 — Dashboard (`src/app/page.tsx`)

| ID | Task | Checkpoint |
|---|---|---|
| FE-3.1 [x] | Responsive grid of `MetricCard`s: documents/vectors tally, open reviews, upcoming expirations, per-category counts | MSW metrics fixture → values rendered; loading skeleton & error states tested |
| FE-3.2 [x] | Active indicators (API `/readyz` status dots: Postgres, Redis, Pinecone) with 30 s polling (configurable) | Short poll interval in test: second poll flips Redis from down to operational |
| FE-3.3 [x] | Upcoming expirations list with urgency color (≤ 7 days red `#fee2e2`) | Snapshot with 3 / 15 day items |

## F4 — Custom Category Form (`CategoryForm.tsx`)

| ID | Task | Checkpoint |
|---|---|---|
| FE-4.1 [x] | Display name → auto-slug preview, regex validation `^[a-z0-9]+(-[a-z0-9]+)*$`, admin-only rendering | "Vehicle Docs" → `vehicle-docs`; invalid manual slug shows error, submit disabled |
| FE-4.2 [x] | POST + optimistic list update; 409 shows "already exists" | MSW 201 and 409 cases |

## F5 — Bulk Upload (`components/BulkUpload.tsx`)

| ID | Task | Checkpoint |
|---|---|---|
| FE-5.1 [x] | "Download sample CSV" builds Blob client-side with headers `FamilyMemberID,Category,DocumentName,ExtractedText,ExpirationDate,FinancialValue` + example rows using the user's real member ids; RFC 4180 quoting | Unit test on `buildTemplateCsv()` output; values with commas/quotes escaped |
| FE-5.2 [x] | Drag-and-drop/file-picker dropper (CSV only, ≤ 5 MB), POST multipart, progress & result table of row errors | MSW returns `{accepted:197,rejected:3,errors:[...]}` → summary + 3 error rows rendered |

## F6 — Chat Agent (`components/ChatAgent.tsx`)

| ID | Task | Checkpoint |
|---|---|---|
| FE-6.1 [x] | Message list (user/assistant bubbles), input, send on Enter, stop button | Render + interaction test |
| FE-6.2 [x] | Streaming: append `token` events to the active assistant message; `sources` rendered as chips | MSW streamed response → final text equals concatenated tokens |
| FE-6.3 [x] | Filter banner from `filter` event: `Context Filtered: John | identitycards` (omit missing parts → `Context Filtered: All members | All categories`) | Test both full and partial filter payloads |
| FE-6.4 [x] | Drag-and-drop attachments mid-chat → `/api/vault/upload`, inline progress card polling `/uploads/{thread_id}` until `completed`/`review_required`/`failed` | Fake timers: status progression rendered; `review_required` links to Reviews page |
| FE-6.5 [x] | Accessibility: `aria-live="polite"` for streaming text, keyboard-only flow | `@axe-core/playwright` scan: no serious violations |

## F7 — Reviews Page (`src/app/reviews/page.tsx`)

| ID | Task | Checkpoint |
|---|---|---|
| FE-7.1 [x] | List pending reviews with reasons, confidence badge, signed-URL preview | MSW fixture renders 2 cards |
| FE-7.2 [x] | Edit form (category select incl. custom, name, date picker, alert toggle, value) + Approve / Reject | Approve posts corrected payload; card removed; invalid date blocked client-side |

## F8 — E2E and Deployment  *(depends on INF-M5 and BE project exit)*

| ID | Task | Checkpoint |
|---|---|---|
| FE-8.1 [x] | Playwright E2E (`page.route` mocks, test auth mode, production build): dashboard metrics + urgent banner + health, CSV template download, axe scan, chat filter banner + streamed answer, chat drop-upload → "Needs review" → Reviews, review approve with corrected date | 6 tests green locally; CI job wired |
| FE-8.2 [~] | Vercel project, env vars, `NEXT_PUBLIC_API_BASE_URL` → Render URL; cold-start friendly UX ("Waking up the vault…" retry on 502/503); see [`infrastructure/VERCEL_SETUP.md`](../infrastructure/VERCEL_SETUP.md) | `vercel.json` and Vercel setup/deploy guide added; retry UX exists; Preview deployment and staging smoke test (IC-4) still require account access |

**Project exit:** IC-4 and IC-5 pass in the [master tracker](../docs/master-tracker.md).
