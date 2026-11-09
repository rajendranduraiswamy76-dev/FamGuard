# infrastructure — Milestone Implementation Plan

Project: Docker, local dev stack, cloud provisioning, CI/CD, deploy configs (`infrastructure/` + root `.dockerignore` + `.github/workflows`). Specs: [requirements](../docs/requirements.md) · [design](../docs/design.md) · [master tracker](../docs/master-tracker.md)

**Constraint:** free tiers only (design §12). **Definition of done:** checkpoint command passes locally and in CI; no secrets committed (`gitleaks` clean).

---

## INF-M0 — Repository Foundations

| ID | Task | Checkpoint |
|---|---|---|
| INF-0.1 [x] | Root `.gitignore`, `.editorconfig`, `.env.example` (all vars from design §11, no values) | `git check-ignore .env` returns `.env` |
| INF-0.2 [x] | Root `.dockerignore`: `.env`, `.env.*` (keep `!.env.example`), `**/node_modules`, `**/__pycache__`, `**/*.pyc`, `**/.pytest_cache`, `**/.mypy_cache`, `**/.ruff_cache`, `.venv`, `**/.next`, `.git`, `frontend/`, `docs/`, `**/coverage`, `**/htmlcov` | `docker build` context size < 5 MB (`docker build --no-cache --progress=plain` shows transfer size); test file `.env` not present in image (`docker run ... test ! -f .env`) |
| INF-0.3 [x] | `gitleaks` pre-commit + CI step (`secrets-scan` job in `ci.yml`) | Planted fake key in a test branch is detected |

## INF-M1 — Local Dev Stack (`docker-compose.yml`)

| ID | Task | Checkpoint |
|---|---|---|
| INF-1.1 [x] | Services: `postgres:16` (host port 5433), `redis:7`, `mailpit` (email capture), `api`, `worker`, plus `combined` under the `combined` profile; healthchecks and `depends_on: condition: service_healthy`; fakes + Mailpit SMTP + shared vault volume | `docker compose up -d` → all services `healthy` |
| INF-1.2 [ ] | `make`/`just` targets: `up`, `down`, `migrate`, `test`, `logs` | `make migrate` applies schema; `make test` runs backend + worker suites in containers |

## INF-M2 — Container Images  *(depends on BE-M0)*

| ID | Task | Checkpoint |
|---|---|---|
| INF-2.1 [x] | `docker/Dockerfile` (spec "Dockerfile"): stage `builder` (python:3.11-slim-bookworm, `/opt/venv`, cached dependency layer, `--no-deps` app install, tiktoken pre-download, import smoke test), stage `runtime` (copy `/opt/venv`, `appuser` UID 10001, `ENV PORT=8000`, `EXPOSE 8000`, mode-aware `HEALTHCHECK`, entrypoint honours `${PORT}`) | `docker build -f infrastructure/docker/Dockerfile -t famguard-app .`; `docker run -e PORT=9000 ...` → health `healthy`; `id -u` → `10001` |
| INF-2.2 [x] | Worker uses the same image with `RUN_MODE=worker` (a separate worker Dockerfile isn't needed); healthcheck via `celery inspect ping` | Container `healthy` in compose; runs as non-root |
| INF-2.3 [x] | `docker/entrypoint.sh` for `RUN_MODE` = `api` / `worker` / `combined`; SIGTERM → exit 0; an unexpected child exit → non-zero exit; unknown mode → exit 64; `MIGRATE_ON_START` | `RUN_MODE=combined`: `/healthz` 200 + worker ping OK; `docker stop` exits 0 in ~4 s |
| INF-2.4 [ ] | Image scanning (`trivy image`) in CI (moved to INF-4.4) | No CRITICAL vulnerabilities |
| INF-2.5 [~] | Image size budget | Measured 593 MB unpacked / 129 MB compressed vs 450 MB target; slimming candidates: replace boto3 with a minimal S3 signer, drop unused langchain extras |

## INF-M3 — Cloud Provisioning (free tiers)

| ID | Task | Checkpoint |
|---|---|---|
| INF-3.1 [~] | `scripts/bootstrap_pinecone.py`: create serverless index `famguard-vault` (aws/us-east-1, cosine, 1536) if absent; idempotent | Script and mocked tests added; set `PINECONE_API_KEY` and run `python infrastructure/scripts/bootstrap_pinecone.py`; after credentials are configured, a second run reports "exists" and `describe_index` shows dimension 1536, metric cosine |
| INF-3.2 [ ] | Supabase project; session-pooler `DATABASE_URL`; run `migrate.py` | `psql $DATABASE_URL -c "\dt"` lists all tables incl. `vault_categories`, `pending_reviews` |
| INF-3.3 [ ] | Redis Cloud free DB; TLS `rediss://` URL | `redis-cli -u $REDIS_URL ping` → `PONG` |
| INF-3.4 [ ] | Cloudflare R2 bucket (private), scoped API token, CORS for Vercel origin (GET only) | `aws s3 ls --endpoint-url ...` works; anonymous GET → 403 |
| INF-3.5 [~] | Clerk application, allowed origins, and **custom session token claims** `{"email": "{{user.primary_email_address}}", "name": "{{user.full_name}}"}` (needed for invite linking and alert emails); see [`CLERK_SETUP.md`](CLERK_SETUP.md) and `scripts/check_clerk.py` | Setup guide and checker added; after account setup, JWKS URL is reachable and a test token verifies with the backend's `security.py` verifier |
| INF-3.6 [ ] | Resend: verify sending domain, API key with send-only scope | Test email delivered to operator inbox |

## INF-M4 — CI Pipelines (GitHub Actions)

Implemented as one `.github/workflows/ci.yml` with `dorny/paths-filter` jobs (a single workflow lets the deploy job depend on every check). Validated locally with `actionlint` + `shellcheck`; "green on PR" is confirmed on the first push.

| ID | Task | Checkpoint |
|---|---|---|
| INF-4.1 [~] | `backend` job: ruff, mypy, pytest with Postgres service container, coverage gate 80% | Workflow green on PR |
| INF-4.2 [~] | `worker` job: installs `../backend-api` + worker, same pattern | Green on PR |
| INF-4.3 [~] | `frontend` job: lint, typecheck, vitest, build, Playwright (runs once `frontend/` exists) | Green on PR |
| INF-4.4 [~] | `image` job: buildx (GHA cache), trivy (fixable CRITICAL fails), uid 10001 check, combined-mode smoke test (healthz, readyz, signed cron → sweep log, clean stop exit 0); `infra` job: script tests, actionlint, shellcheck | Green on PR; path filters avoid unnecessary runs |

## INF-M5 — Deployment

| ID | Task | Checkpoint |
|---|---|---|
| INF-5.1 [~] | `render.yaml` (repo root): free Docker web service `famguard-api` in `virginia`, `RUN_MODE=combined`, `MIGRATE_ON_START=1`, `healthCheckPath=/healthz`, `autoDeploy: false`, secrets as `sync: false`; commented paid worker variant | Blueprint applied in Render dashboard; `https://<api>.onrender.com/healthz` → 200 |
| INF-5.2 [~] | Vercel project for `frontend/` (root dir), env vars, preview + production; see [`VERCEL_SETUP.md`](VERCEL_SETUP.md) (`vercel.json` lands with F8) | Vercel config and setup/deploy guide added; dashboard project, environment values, Preview smoke test, and production deploy still require account access |
| INF-5.3 [~] | `deploy` job in `ci.yml`: main only, after all checks (skipped path-filtered jobs allowed); Render deploy hook + Vercel CLI prebuilt deploy; no-ops while secrets are unset | Merge to main → both deploy; failed CI blocks deploy |

## INF-M6 — Scheduled Trigger (free-tier cron, ADR-004)

| ID | Task | Checkpoint |
|---|---|---|
| INF-6.1 [x] | `cron-expiry-sweep.yml` (`0 0 * * *` + `workflow_dispatch`) runs `scripts/trigger_expiry_sweep.py`: wakes the API via `/healthz` (8 × 15 s for cold starts), signs `X-Cron-Timestamp` / `X-Cron-Signature`, retries 5xx, fails fast on 401 | Verified against local compose (202 + sweep ran; wrong secret → exit 1); 6 script tests; GitHub manual dispatch after first deploy |
| INF-6.2 [x] | Supabase keep-alive: daily sweep queries Postgres | Supabase dashboard shows daily activity after 2 days |

## INF-M7 — Observability and Runbook

| ID | Task | Checkpoint |
|---|---|---|
| INF-7.1 [ ] | Uptime check (free: UptimeRobot or GitHub Action) on `/healthz` | Alert fires when service is stopped |
| INF-7.2 [ ] | `infrastructure/RUNBOOK.md`: deploy, rollback, secret rotation, Supabase un-pause, Pinecone re-index from `documents` | Dry-run of rollback on staging succeeds |

**Project exit:** IC-1, IC-4 and IC-5 pass in the [master tracker](../docs/master-tracker.md).
