#!/usr/bin/env bash
# FamGuard container entrypoint. RUN_MODE selects the process layout:
#   api       uvicorn only (Render web service)
#   worker    celery worker + embedded beat (Render background worker, paid tier)
#   combined  uvicorn + celery worker in one container (Render free tier; GitHub cron replaces beat)
set -euo pipefail

MODE="${RUN_MODE:-api}"
PORT="${PORT:-8000}"

api_cmd=(uvicorn app.api.main:app
  --host 0.0.0.0 --port "${PORT}"
  --proxy-headers --forwarded-allow-ips "*"
  --workers "${WEB_CONCURRENCY:-1}"
  --timeout-graceful-shutdown 8
  --no-server-header)

worker_cmd=(celery -A famguard_worker.worker worker
  --loglevel "${CELERY_LOG_LEVEL:-INFO}"
  --pool "${CELERY_POOL:-solo}"
  --concurrency "${CELERY_CONCURRENCY:-1}"
  --without-gossip --without-mingle)

if [[ "${MIGRATE_ON_START:-0}" == "1" && "${MODE}" != "worker" ]]; then
  # Idempotent and serialized by a Postgres advisory lock.
  python -m app.database --migrate
fi

case "${MODE}" in
  api)
    exec "${api_cmd[@]}"
    ;;
  worker)
    if [[ "${WORKER_BEAT:-1}" == "1" ]]; then
      exec "${worker_cmd[@]}" --beat --schedule /tmp/celerybeat-schedule
    fi
    exec "${worker_cmd[@]}"
    ;;
  combined)
    "${worker_cmd[@]}" &
    worker_pid=$!
    "${api_cmd[@]}" &
    api_pid=$!

    stopping=0
    # shellcheck disable=SC2329  # invoked via trap
    on_signal() {
      stopping=1
      kill -TERM "${api_pid}" "${worker_pid}" 2>/dev/null || true
    }
    trap on_signal TERM INT

    set +e
    wait -n "${api_pid}" "${worker_pid}"
    status=$?
    if [[ "${stopping}" == "0" ]]; then
      # A child died on its own: stop the other and exit non-zero so the platform restarts us.
      echo "combined: a process exited unexpectedly (status ${status}); stopping" >&2
      kill -TERM "${api_pid}" "${worker_pid}" 2>/dev/null || true
    fi
    wait "${api_pid}" "${worker_pid}" 2>/dev/null
    set -e

    if [[ "${stopping}" == "1" ]]; then
      exit 0
    fi
    exit $(( status == 0 ? 1 : status ))
    ;;
  *)
    echo "Unknown RUN_MODE '${MODE}' (expected api, worker or combined)" >&2
    exit 64
    ;;
esac
