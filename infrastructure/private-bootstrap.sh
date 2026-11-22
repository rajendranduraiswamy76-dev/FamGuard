#!/usr/bin/env bash
# Sources must already be staged at /opt/famguard. Run through VM startup metadata.
set -euo pipefail
export HOME=/root DEBIAN_FRONTEND=noninteractive
exec > >(tee -a /var/log/famguard-bootstrap.log) 2>&1
trap 'echo "FAMGUARD_BOOTSTRAP_FAILED line=$LINENO"' ERR
cd /opt/famguard
echo "FAMGUARD_BOOTSTRAP_BEGIN"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 curl
systemctl enable --now docker
test "$(curl -fsS http://127.0.0.1:11434/api/version | wc -c)" -gt 0
ollama pull nomic-embed-text
test -f infrastructure/.private-bootstrap.env || (
  umask 077
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" \
    > infrastructure/.private-bootstrap.env
)
compose=(docker compose --env-file infrastructure/.private-bootstrap.env \
  -f infrastructure/private-bootstrap.yml)
"${compose[@]}" build --progress plain
"${compose[@]}" up -d --wait --wait-timeout 300
curl -fsS http://127.0.0.1:8000/readyz
test "$(curl -s -o /dev/null -w '%{http_code}' \
  -H 'Authorization: Bearer test-token' http://127.0.0.1:8000/api/me)" = 503
curl -fsS --retry 10 --retry-connrefused --retry-delay 3 http://127.0.0.1:3000/ > /dev/null
echo "FAMGUARD_SERVICES_HEALTHY_AUTH_LOCKED"
"${compose[@]}" exec -T api python < infrastructure/ollama-smoke.py
ss -lnt
echo "FAMGUARD_BOOTSTRAP_COMPLETE"
