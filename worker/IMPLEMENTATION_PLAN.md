# worker — Milestone Implementation Plan

Project: Celery worker + Celery Beat (`worker/`). Imports shared code from `backend-api` (`app` package) per ADR-003. Specs: [requirements](../docs/requirements.md) · [design](../docs/design.md) · [master tracker](../docs/master-tracker.md)

**Definition of done:** `ruff` + `mypy famguard_worker` clean; checkpoint passes with `task_always_eager=True`, fakeredis, `freezegun`, `respx` for Resend/OpenAI, fake Pinecone index. No real emails in tests.

---

## W0 — Scaffold  *(depends on BE-M0, BE-M1)*

| ID | Task | Checkpoint |
|---|---|---|
| WK-0.1 [x] | `pyproject.toml` depending on `famguard-api` (install `../backend-api` first; local dev shares `backend-api/.venv`), plus `celery[redis]`, `resend`; dev deps mirror backend | `pip install -e ../backend-api -e .[dev]`; `python -c "import famguard_worker, app"` |
| WK-0.2 [x] | `famguard_worker/worker.py`: Celery app (`broker=REDIS_URL`, JSON serializer, `acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, result expiry 1 h, visibility timeout 1 h); `runtime.py` gives each process one event loop + lazily created DB pool/checkpointer/services | `pytest tests/test_app.py` — config values asserted; `celery -A famguard_worker.worker inspect ping` works against local Redis (docker-compose) |

## W1 — Beat Schedule

| ID | Task | Checkpoint |
|---|---|---|
| WK-1.1 [x] | `beat_schedule["daily-expiry-sweep"] = crontab(hour=0, minute=0)`, `timezone=CELERY_TIMEZONE` | Test asserts schedule entry, task name `famguard.expiry_sweep`, `crontab.hour == {0}`, `minute == {0}`; `remaining_estimate` from 23:59 is 1 min (freezegun) |

## W2 — Ingestion Tasks  *(depends on BE-M5)*

| ID | Task | Checkpoint |
|---|---|---|
| WK-2.1 [x] | `famguard.ingest_document(thread_id)` → `IngestionService.run`; manual retry (10 s / 60 s / 300 s, max 3) that continues from the checkpoint; `IngestionStateError` is permanent | Eager test: transient error twice → succeeds 3rd; permanent error → document `failed` |
| WK-2.2 [x] | `famguard.resume_ingestion(thread_id, payload)` | Eager test: interrupted thread (MemorySaver) resumed → vectors in fake index |
| WK-2.3 [x] | Idempotency guard (skip if document already `completed`) | Task invoked twice → single upsert |

## W3 — Email Templates (`email/templates.py`)

| ID | Task | Checkpoint |
|---|---|---|
| WK-3.1 [x] | `render_alert_email(recipient_name, items[])` → `(subject, html, text)`; 600 px table layout, inline CSS only, HTML-escaped values | Test: `<script>` in document name is escaped; no `<style>` or `class=` attributes |
| WK-3.2 [x] | Danger banner `background:#fee2e2` when any item `days ≤ 7`; amber `#fef3c7` otherwise; per-item urgency badges | Parametrized test for days 30 / 7 / 1 → banner color presence/absence |
| WK-3.3 [x] | Structural HTML assertions (ordering, escaping, banners) + plaintext alternative | Snapshot tests stable; plaintext contains each document name and date |

## W4 — Expiry Sweep (`tasks/expiry_sweep.py`)  *(depends on BE-4.2)*

| ID | Task | Checkpoint |
|---|---|---|
| WK-4.1 [x] | `target_dates(today)` for offsets 30/7/1 | freezegun `2026-12-31` → `2027-01-30`, `2027-01-07`, `2027-01-01` |
| WK-4.2 [x] | Pinecone sweep with `DUMMY_VECTOR` (1536 dims, non-zero) and filter `{"expiration_date":{"$eq":d},"is_primary":{"$eq":True},"requires_alert":{"$eq":True}}`, `top_k=1000`, namespace `family-vault` | Fake index records the call: vector length 1536, exact filter, namespace |
| WK-4.3 [x] | Dedupe by `document_id`; overflow (top_k hits) → warning + Postgres reconciliation; matches re-validated against Postgres (stale vectors skipped) | Fixture with duplicate chunks → one item; overflow fixture → reconciliation query called |
| WK-4.4 [x] | Recipient resolution (member email + all family admins, deduped; dependents without email skipped) and per-recipient digest grouping | 2 docs for same admin → 1 email with 2 items |

## W5 — Resend Delivery and Idempotency

| ID | Task | Checkpoint |
|---|---|---|
| WK-5.1 [x] | `ResendSender` around `resend.Emails.send` with idempotency key; retry on rate limit/5xx/network; quota exceeded stops the run; 4xx not retried | `respx`: 429 then 200 → one success; 400 → no retry, logged |
| WK-5.2 [x] | `alert_log` check-before-send and insert-after-send (unique constraint) | Running sweep twice on same frozen day → Resend called once per (doc, offset, recipient) |
| WK-5.3 [x] | Daily cap guard (`ALERT_DAILY_SEND_CAP`, default 95 emails), most urgent recipients first | 120 pending alerts → 95 sent, 25 deferred and logged |

## W6 — Run Modes  *(depends on INF-M2)*

| ID | Task | Checkpoint |
|---|---|---|
| WK-6.1 [x] | Worker mode: `celery -A famguard_worker.worker worker --beat --pool solo --concurrency 1` (via `RUN_MODE=worker`) | docker-compose: `beat: Starting...` logged; signed cron → API → Redis → worker ran `famguard.expiry_sweep` |
| WK-6.2 [x] | Combined mode: `/internal/cron/expiry-sweep` enqueue consumed by the embedded worker; in fake mode the sweep reconciles from Postgres (fake vectors are per process); `SMTP_URL` delivers to Mailpit | Standalone worker stopped, combined container triggered → new alert in Mailpit; red `#fee2e2` banner verified; rerun sent nothing new |

## W7 — Hardening

| ID | Task | Checkpoint |
|---|---|---|
| WK-7.1 [x] | Structured JSON logs with Celery task id; redact email addresses and sensitive text fields, omit exception messages/tracebacks | Log capture tests verify task context and that email/extracted text never appear |
| WK-7.2 [~] | Coverage gate ≥ 80% (already enforced by the worker CI job) | CI runs `pytest -q --cov=famguard_worker --cov-report=term --cov-fail-under=80` with Postgres; local run is 61.5% because 19 database integration tests are skipped without `TEST_DATABASE_URL` |

**Project exit:** IC-3 (alerts) and IC-2 (ingestion via queue) pass in the [master tracker](../docs/master-tracker.md).
