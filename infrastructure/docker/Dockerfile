# FamGuard AI backend image (API, worker, or both). Build from the repository root:
#   docker build -f infrastructure/docker/Dockerfile -t famguard-app .
# RUN_MODE=api|worker|combined selects the process layout (see entrypoint.sh).

ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# ---------------------------------------------------------------------------
# Stage 1: build a self-contained virtual environment
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

# Dependency layer: only invalidated when pyproject files change.
COPY infrastructure/docker/collect_requirements.py ./
COPY backend-api/pyproject.toml backend-api/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
RUN python collect_requirements.py . > requirements.txt \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

# Application layer.
COPY backend-api/app backend-api/app
COPY worker/famguard_worker worker/famguard_worker
RUN pip install --no-deps ./backend-api ./worker \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
    && ENV=test DATABASE_URL=postgresql://build@localhost/build OPENAI_API_KEY=build \
       PINECONE_API_KEY=build python -c "import app.api.main, famguard_worker.worker" \
    && find /opt/venv -type d -name "__pycache__" -prune -exec rm -rf {} +

# ---------------------------------------------------------------------------
# Stage 2: minimal runtime, non-root
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    PORT=8000 \
    RUN_MODE=api

RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /home/appuser \
       --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/tiktoken /opt/tiktoken
COPY --chmod=0755 infrastructure/docker/entrypoint.sh /usr/local/bin/famguard-entrypoint
# --chmod also applies to directories COPY creates, so 0755 keeps the folder traversable.
COPY --chmod=0755 infrastructure/docker/healthcheck.py /usr/local/lib/famguard/healthcheck.py
# Guard against CRLF checkouts on Windows; pre-create the dev storage mount point for appuser.
RUN sed -i 's/\r$//' /usr/local/bin/famguard-entrypoint \
    && mkdir -p /data/vault && chown appuser:appuser /data/vault

WORKDIR /home/appuser
USER appuser:appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=30s --start-period=40s --retries=3 \
    CMD ["python", "/usr/local/lib/famguard/healthcheck.py"]

ENTRYPOINT ["famguard-entrypoint"]
