#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-unknown}"
BP_ROOT="${BP_ROOT:-/opt/bp}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
COMPOSE_FILE="$BP_ROOT/docker-compose.prod.yml"
EVIDENCE_ROOT=/var/lib/bp/evidence
STAGED_ENV=""
DB_CHANGED=0
ENV_SWAPPED=0
NEW_PASSWORD=""

if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 12 PostgreSQL credential rotation must run as root" >&2
  exit 2
fi
for command in docker openssl python3 systemctl curl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "required command is missing: $command" >&2
    exit 2
  fi
done
if [[ ! -f "$ENV_FILE" || ! -f "$COMPOSE_FILE" ]]; then
  echo "BP environment or compose file is missing" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
POSTGRES_USER=$(read_env POSTGRES_USER)
POSTGRES_DB=$(read_env POSTGRES_DB)

if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Credential rotation requires research mode, live disabled, and zero money limits" >&2
  exit 3
fi
if ! [[ "$POSTGRES_USER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "POSTGRES_USER is missing or unsafe" >&2
  exit 3
fi
if ! [[ "$POSTGRES_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "POSTGRES_DB is missing or unsafe" >&2
  exit 3
fi
if ! grep -q '^POSTGRES_PASSWORD=.' "$ENV_FILE" || ! grep -q '^DATABASE_URL=.' "$ENV_FILE"; then
  echo "POSTGRES_PASSWORD or DATABASE_URL is missing" >&2
  exit 3
fi

for service in \
  bp-postgres.service \
  bp-recorder.service \
  bp-dashboard-api.service \
  bp-dashboard-web.service; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "$service must be active before credential rotation" >&2
    exit 4
  fi
done

POSTGRES_CONTAINER_BEFORE=$(
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q postgres
)
if [[ -z "$POSTGRES_CONTAINER_BEFORE" ]]; then
  echo "PostgreSQL container is not running" >&2
  exit 4
fi

install_staged_env_if_db_changed() {
  if (( DB_CHANGED && ! ENV_SWAPPED )) && [[ -n "$STAGED_ENV" && -f "$STAGED_ENV" ]]; then
    python3 -c '
import os
import sys
from pathlib import Path

staged = Path(sys.argv[1])
target = Path(sys.argv[2])
os.replace(staged, target)
parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
' "$STAGED_ENV" "$ENV_FILE" || true
    ENV_SWAPPED=1
  fi
}

cleanup() {
  local rc=$?
  set +e
  install_staged_env_if_db_changed
  if (( rc != 0 && DB_CHANGED )); then
    systemctl restart bp-recorder.service >/dev/null 2>&1 || true
    systemctl restart bp-dashboard-api.service >/dev/null 2>&1 || true
  fi
  [[ -n "$STAGED_ENV" ]] && rm -f "$STAGED_ENV"
  NEW_PASSWORD=""
  unset NEW_PASSWORD
  set -e
}
trap cleanup EXIT

NEW_PASSWORD=$(openssl rand -hex 32)
if ! [[ "$NEW_PASSWORD" =~ ^[0-9a-f]{64}$ ]]; then
  echo "failed to generate a strong replacement credential" >&2
  exit 5
fi

STAGED_ENV=$(mktemp "${ENV_FILE}.phase12-rotate.XXXXXX")
printf '%s' "$NEW_PASSWORD" | python3 -c '
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

source = Path(sys.argv[1])
target = Path(sys.argv[2])
secret = sys.stdin.read().strip()
if len(secret) != 64 or any(char not in "0123456789abcdef" for char in secret):
    raise SystemExit("invalid replacement credential")

source_stat = source.stat()
lines = source.read_text(encoding="utf-8").splitlines()
password_count = 0
url_count = 0
updated: list[str] = []
for line in lines:
    if line.startswith("POSTGRES_PASSWORD="):
        password_count += 1
        updated.append(f"POSTGRES_PASSWORD={secret}")
        continue
    if line.startswith("DATABASE_URL="):
        url_count += 1
        raw_url = line.split("=", 1)[1]
        parsed = urlsplit(raw_url)
        if not parsed.scheme or parsed.username is None or parsed.hostname is None:
            raise SystemExit("DATABASE_URL cannot be safely rewritten")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        encoded_username = quote(parsed.username, safe="")
        netloc = f"{encoded_username}:{secret}@{host}{port}"
        updated_url = urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
        updated.append(f"DATABASE_URL={updated_url}")
        continue
    updated.append(line)

if password_count != 1 or url_count != 1:
    raise SystemExit("expected exactly one POSTGRES_PASSWORD and DATABASE_URL")

target.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.chmod(target, stat.S_IMODE(source_stat.st_mode))
os.chown(target, source_stat.st_uid, source_stat.st_gid)
with target.open("rb") as handle:
    os.fsync(handle.fileno())
' "$ENV_FILE" "$STAGED_ENV"

# Change the role through stdin so the replacement credential never appears in argv.
printf 'ALTER ROLE "%s" PASSWORD '\''%s'\'';\n' "$POSTGRES_USER" "$NEW_PASSWORD" \
  | docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
      psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      >/dev/null
DB_CHANGED=1

python3 -c '
import os
import sys
from pathlib import Path

staged = Path(sys.argv[1])
target = Path(sys.argv[2])
os.replace(staged, target)
parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
' "$STAGED_ENV" "$ENV_FILE"
ENV_SWAPPED=1
STAGED_ENV=""
NEW_PASSWORD=""
unset NEW_PASSWORD

systemctl restart bp-recorder.service
systemctl restart bp-dashboard-api.service

for service in bp-recorder.service bp-dashboard-api.service; do
  ready=0
  for _ in $(seq 1 30); do
    if [[ "$(systemctl is-active "$service" || true)" == "active" ]]; then
      ready=1
      break
    fi
    sleep 1
  done
  if (( ! ready )); then
    systemctl --no-pager --full status "$service" >&2 || true
    journalctl -u "$service" -n 80 --no-pager >&2 || true
    echo "$service did not recover after credential rotation" >&2
    exit 6
  fi
done

api_ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/api/v1/snapshot >/dev/null; then
    api_ready=1
    break
  fi
  sleep 1
done
if (( ! api_ready )); then
  echo "Dashboard API could not query PostgreSQL after credential rotation" >&2
  exit 6
fi

for service in bp-postgres.service bp-recorder.service bp-dashboard-api.service bp-dashboard-web.service; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "$service is not active after credential rotation" >&2
    exit 7
  fi
done

POSTGRES_CONTAINER_AFTER=$(
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q postgres
)
if [[ "$POSTGRES_CONTAINER_AFTER" != "$POSTGRES_CONTAINER_BEFORE" ]]; then
  echo "PostgreSQL container changed during credential rotation" >&2
  exit 7
fi

install -d -o bp -g bp "$EVIDENCE_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_file="$EVIDENCE_ROOT/phase12-postgres-password-rotation-$stamp.txt"
{
  echo "PHASE12_POSTGRES_PASSWORD_ROTATION=PASS"
  echo "PHASE12_HEAD=$EXPECTED_HEAD"
  echo "POSTGRES_CONTAINER_UNCHANGED=PASS"
  echo "POSTGRES_STATUS=$(systemctl is-active bp-postgres.service)"
  echo "RECORDER_STATUS=$(systemctl is-active bp-recorder.service)"
  echo "DASHBOARD_API_STATUS=$(systemctl is-active bp-dashboard-api.service)"
  echo "DASHBOARD_WEB_STATUS=$(systemctl is-active bp-dashboard-web.service)"
  echo "ENV_FILE_UPDATED=PASS"
} | tee "$evidence_file"
chown bp:bp "$evidence_file"
chmod 0640 "$evidence_file"
