# FamGuard AI — Master Tracker

| Doc | Link |
|---|---|
| Requirements (BRD) | [requirements.md](requirements.md) |
| Technical design | [design.md](design.md) |
| backend-api plan | [backend-api/IMPLEMENTATION_PLAN.md](../backend-api/IMPLEMENTATION_PLAN.md) |
| worker plan | [worker/IMPLEMENTATION_PLAN.md](../worker/IMPLEMENTATION_PLAN.md) |
| frontend plan | [frontend/IMPLEMENTATION_PLAN.md](../frontend/IMPLEMENTATION_PLAN.md) |
| infrastructure plan | [infrastructure/IMPLEMENTATION_PLAN.md](../infrastructure/IMPLEMENTATION_PLAN.md) |

Status legend: `Not started` · `In progress` · `Blocked` · `Done`

---

## 1. Milestone Status Board

| Project | Milestone | Scope | Depends on | Status |
|---|---|---|---|---|
| infrastructure | INF-M0 | Repo foundations, `.dockerignore`, gitleaks | — | Done |
| infrastructure | INF-M1 | docker-compose local stack | INF-M0 | Done (make/just targets pending) |
| backend-api | BE-M0 | Scaffold, config, `/healthz` | INF-M0 | Done |
| backend-api | BE-M1 | `database.py` DDL, pool, repos | BE-M0, INF-M1 | Done |
| infrastructure | INF-M2 | Dockerfiles, combined entrypoint | BE-M0 | Done (size budget missed, trivy → INF-M4) |
| backend-api | BE-M2 | Clerk JWT, RBAC, family bootstrap | BE-M1 | Done |
| backend-api | BE-M3 | R2 storage, parsers | BE-M1 | Done |
| backend-api | BE-M4 | `state.py`, vector store, filter builder | BE-M1 | Done |
| worker | W0–W1 | Celery app, Beat schedule | BE-M1, INF-M1 | Done |
| backend-api | BE-M5 | Ingestion graph + HITL | BE-M3, BE-M4 | Done |
| worker | W2 | Ingestion tasks | BE-M5 | Done |
| worker | W3 | Email templates | W0 | Done |
| worker | W4–W5 | Expiry sweep, Resend, idempotency | BE-M4, W3 | Done |
| backend-api | BE-M6 | Upload, reviews, categories endpoints | BE-M2, BE-M5, W2 | Done |
| backend-api | BE-M7 | Bulk CSV | BE-M6 | Done |
| backend-api | BE-M8 | RAG engine + SSE chat | BE-M4, BE-M2 | Done |
| backend-api | BE-M9 | Metrics, delete, cron endpoint, rate limits | BE-M6, W4 | Done |
| backend-api | BE-M10 | Isolation suite, OpenAPI export | BE-M6–M9 | Done |
| worker | W6–W7 | Run modes, hardening | INF-M2, W5 | W6 Done; W7 in progress (task-aware PII-safe logs done; CI coverage gate awaits Postgres-backed run) |
| frontend | F0–F1 | Scaffold, Clerk, layout | INF-0 | Done (Clerk redirect check needs real keys) |
| frontend | F2 | API client, SSE parser | F1 (BE-M10 for generated types) | Done (OpenAPI-generated response types) |
| frontend | F3–F5 | Dashboard, categories, bulk upload | F2 | Done |
| frontend | F6–F7 | Chat agent, reviews page | F2 | Done |
| infrastructure | INF-M3 | Cloud provisioning (free tiers) | INF-M0 | In progress (Pinecone bootstrap + Clerk guide/checker added; external accounts still need setup) |
| infrastructure | INF-M4 | CI pipelines | INF-M2, BE-M0, WK-0, FE-0 | Written + linted; awaiting first GitHub run |
| infrastructure | INF-M5 | Render + Vercel deploy | INF-M3, INF-M4 | In progress (`render.yaml`, gated deploy job, `frontend/vercel.json`, and Vercel setup guide; dashboard setup and live deploy pending accounts) |
| infrastructure | INF-M6 | GitHub Actions cron trigger | INF-M5, BE-9.3 | Done (verified locally; first scheduled run after deploy) |
| frontend | F8 | E2E + Vercel | INF-M5, BE-M10 | In progress (E2E done; `vercel.json` and deploy guide added; live Preview/Production checks pending Vercel account setup) |
| infrastructure | INF-M7 | Observability, runbook | INF-M5 | Not started |

## 2. Cross-Project Dependency Graph

```mermaid
flowchart LR
    classDef be fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef wk fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef fe fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef inf fill:#f3e8ff,stroke:#7e22ce,color:#581c87
    classDef ic fill:#fee2e2,stroke:#dc2626,color:#7f1d1d

    INF0[INF-M0 Foundations]:::inf --> INF1[INF-M1 Compose]:::inf
    INF0 --> BE0[BE-M0 Scaffold]:::be
    INF0 --> FE01[F0-F1 Scaffold+Auth]:::fe
    INF0 --> INF3[INF-M3 Provisioning]:::inf
    BE0 --> BE1[BE-M1 Database]:::be
    INF1 --> BE1
    BE0 --> INF2[INF-M2 Docker]:::inf
    BE1 --> BE2[BE-M2 Auth]:::be
    BE1 --> BE3[BE-M3 Storage/Parsers]:::be
    BE1 --> BE4[BE-M4 State/Vectors]:::be
    BE1 --> W01[W0-W1 Celery+Beat]:::wk
    BE3 --> BE5[BE-M5 Ingestion+HITL]:::be
    BE4 --> BE5
    BE5 --> W2[W2 Ingest tasks]:::wk
    W01 --> W3[W3 Email templates]:::wk
    BE4 --> W45[W4-W5 Sweep+Resend]:::wk
    W3 --> W45
    BE2 --> BE6[BE-M6 Vault endpoints]:::be
    BE5 --> BE6
    W2 --> BE6
    BE6 --> BE7[BE-M7 Bulk CSV]:::be
    BE2 --> BE8[BE-M8 RAG+SSE]:::be
    BE4 --> BE8
    BE6 --> BE9[BE-M9 Ops endpoints]:::be
    W45 --> BE9
    BE7 --> BE10[BE-M10 Hardening]:::be
    BE8 --> BE10
    BE9 --> BE10
    INF2 --> W67[W6-W7 Run modes]:::wk
    W45 --> W67
    FE01 --> FE2[F2 API client]:::fe
    BE10 -. openapi.json .-> FE2
    FE2 --> FE35[F3-F5 Dashboard/Cat/Bulk]:::fe
    FE2 --> FE67[F6-F7 Chat/Reviews]:::fe
    INF2 --> INF4[INF-M4 CI]:::inf
    INF3 --> INF5[INF-M5 Deploy]:::inf
    INF4 --> INF5
    INF5 --> INF6[INF-M6 Cron]:::inf
    BE9 --> INF6
    INF5 --> FE8[F8 E2E+Vercel]:::fe
    BE10 --> FE8
    INF5 --> INF7[INF-M7 Observability]:::inf

    BE5 --> IC2{{IC-2 Ingestion+HITL}}:::ic
    W2 --> IC2
    W45 --> IC3{{IC-3 Alerts}}:::ic
    INF6 --> IC3
    FE67 --> IC4{{IC-4 Staging E2E}}:::ic
    FE8 --> IC4
    INF1 --> IC1{{IC-1 Local stack}}:::ic
    INF2 --> IC1
    IC4 --> IC5{{IC-5 Release}}:::ic
    IC3 --> IC5
```

**Critical path:** INF-M0 → BE-M0 → BE-M1 → BE-M3/BE-M4 → BE-M5 → W2 → BE-M6 → BE-M9 → BE-M10 → F8 → IC-5.

**Parallel lanes once BE-M1 is done:** (a) BE-M2 auth, (b) BE-M3/M4, (c) W0–W3 worker and email, (d) F0–F7 frontend against MSW mocks, (e) INF-M2–M4 images, provisioning and CI.

## 3. Shared Contracts (change control)

Changes to these require updating all consuming projects in the same PR.

| Contract | Owner | Consumers | Source of truth |
|---|---|---|---|
| Postgres schema (DDL) | backend-api (`app/database.py`) | worker, infrastructure (`migrate.py`) | design §3.1 |
| Pinecone metadata keys (`family_id`, `family_member_id`, `category`, `expiration_date`, `is_primary`, …) | backend-api (`app/vectorstore.py`) | worker (sweep filter) | design §3.2 |
| `UnifiedVaultExtraction` | backend-api (`app/agents/state.py`) | worker, frontend types | design §4 |
| Celery task names (`famguard.ingest_document`, `famguard.resume_ingestion`, `famguard.expiry_sweep`) | worker | backend-api (`send_task`) | design §7–8 |
| REST + SSE API (`openapi.json`, SSE event names) | backend-api | frontend | design §6, §9 |
| Env variables | infrastructure (`.env.example`) | all | design §11 |
| `RUN_MODE` + cron HMAC scheme | infrastructure | backend-api, worker | design §10, §12 |

## 4. Integration Checkpoints

Each IC is a gate. All must be green before a release.

| ID | Name | Projects | Test procedure | Pass criteria | Status |
|---|---|---|---|---|---|
| IC-1 | Local stack up | infra, backend-api, worker | `docker compose up -d && make migrate && curl localhost:8000/healthz` | All containers healthy; schema applied; non-root `id -u` = 10001 | Passed 2026-10-18 |
| IC-2 | Ingestion + HITL via queue | backend-api, worker, infra | In compose (OpenAI/Pinecone mocked via env `FAKE_EXTERNALS=1`): upload clear passport → `completed`; upload ID without date → `review_required` → resolve → vectors present; restart worker between interrupt and resume | Both paths succeed; resume survives restart (PostgresSaver) | Not started |
| IC-3 | Expiry alerts | worker, backend-api, infra | Seed docs expiring at T+30/7/1; trigger signed `/internal/cron/expiry-sweep`; inspect Mailpit | One digest per recipient; red `#fee2e2` banner for ≤7 days; rerun sends nothing new | Not started |
| IC-4 | Staging E2E | frontend, backend-api, worker, infra | Playwright smoke against Vercel preview + Render: sign in, add category, upload, review, chat | Filter banner shows `Context Filtered: <member> | <category>`; answer cites document | Not started |
| IC-5 | Release readiness | all | Isolation suite on staging with two real Clerk users in different families; trivy; coverage gates; runbook rollback dry-run | Zero cross-family leakage; no CRITICAL CVEs; coverage ≥ 80% | Not started |

## 5. Risks and Blockers Log

| Date | Item | Projects | Owner | Status |
|---|---|---|---|---|
| — | Render Free: no Background Worker, sleeps after idle → mitigated by `combined` mode + GH cron (ADR-004) | infra, worker | — | Accepted |
| — | Resend requires verified domain for non-owner recipients | infra, worker | — | Open |
| — | Pinecone `top_k` 1,000 cap on sweep → Postgres reconciliation (WK-4.3) | worker | — | Mitigated |
| — | Supabase transaction pooler incompatible with prepared statements → use session pooler | backend-api, infra | — | Mitigated |
| — | Alerts deferred by the daily email cap/quota are not caught up next day (offsets are exact-day matches) | worker | — | Open — consider a catch-up window in W7 |
| — | GitHub disables scheduled workflows after 60 days without repository activity | infra | — | Open — re-enable or add a monthly keep-alive commit |
| — | Third-party Actions pinned to version tags, not commit SHAs; Docker deps unpinned (no lock file) | infra | — | Open — pin SHAs (Dependabot) and add a constraints file |

## 6. Change Log

| Date | Change |
|---|---|
| 2026-10-18 | Initial tracker created from requirements v1.0 and design v1.0 |
| 2026-10-18 | BE-M0, BE-M1 done (50 tests incl. Postgres integration, 92% coverage). Repos split into `app/repositories.py`; `puremagic` replaces `python-magic` (no libmagic); compose Postgres on host port 5433 |
| 2026-10-18 | BE-M2–M4 done (159 tests, 91% coverage). Clerk session token must include custom claims `email` and `name` (INF-3.5). Invites: admin adds member with email → first login with that email joins the family. Embeddings call `AsyncOpenAI` directly |
| 2026-10-18 | BE-M5 done (185 tests). Worker contract: `IngestionService.run/resume/mark_failed`; checkpointer via `open_checkpointer(settings)` (AsyncPostgresSaver on a psycopg pool, `prepare_threshold=None`). W2 is unblocked |
| 2026-10-18 | BE-M6, BE-M8, W0–W2 done (backend 213 tests / 91%; worker 12 tests / 88%). Celery eager tests must keep `task_eager_propagates=False` so retries re-apply. Frontend F2+ can now target real `/api/vault/*` and SSE contracts |
| 2026-10-18 | W3–W5 done (worker 42 tests / 92%). `famguard.expiry_sweep` now registered; digest per recipient; Resend idempotency keys; `ALERT_DAILY_SEND_CAP` setting |
| 2026-10-18 | BE-M7, BE-M9 done (backend 244 tests / 92%). New endpoints: `/api/vault/bulk-upload`, `/api/vault/metrics`, `/api/vault/documents/{id}/url`, `DELETE /api/vault/documents/{id}`, `/readyz`, `/internal/cron/expiry-sweep` (signature format in design §9). CORS allowlist, security headers, slowapi limits (chat 30/min, upload 20/min, bulk 5/min). INF-M6 unblocked |
| 2026-10-18 | INF-M2 + W6 done. Single image `infrastructure/docker/Dockerfile` with `RUN_MODE` api/worker/combined; IC-1 passed; local IC-3 dry run passed (signed cron → sweep → Mailpit, red banner, idempotent rerun). Dev-only `SMTP_URL`/`LOCAL_STORAGE_DIR` settings added |
| 2026-10-18 | INF-M4–M6 written: `.github/workflows/ci.yml` (paths-filtered backend/worker/infra/frontend/image jobs, gitleaks, trivy, combined-mode smoke, gated deploy), `cron-expiry-sweep.yml`, root `render.yaml`. actionlint + shellcheck clean; cron script verified against local compose. Blocked on: git repo + GitHub/Render/Vercel accounts (INF-M3) |
| 2026-10-18 | Frontend F0–F7 (+ FE-8.1) done: Next.js 16.4 / React 19 / Tailwind 4 / Clerk Core 3; `src/proxy.ts`; test auth mode for automation; 51 Vitest + 6 Playwright tests (incl. axe), builds in test and Clerk modes. CI frontend job moved to Node 22 (Vitest 5). Multipart uploads in Vitest are intercepted at fetch level (Vitest 5 cannot read jsdom Blobs) |
