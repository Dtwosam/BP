#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
BP_ROOT="${BP_ROOT:-/opt/bp}"
BP_USER="${BP_USER:-bp}"
BP_GROUP="${BP_GROUP:-bp}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
DASHBOARD_DIR="$BP_ROOT/apps/dashboard"
NODE_VERSION=24.20.0
NODE_FINAL="$BP_ROOT/.node"
NODE_STAGE=""
NODE_PREVIOUS=""
NODE_SWAPPED=0
API_UNIT_NAME=bp-dashboard-api.service
WEB_UNIT_NAME=bp-dashboard-web.service
API_UNIT_DST="/etc/systemd/system/$API_UNIT_NAME"
WEB_UNIT_DST="/etc/systemd/system/$WEB_UNIT_NAME"
BACKUP_DIR=""
SNAPSHOT_FILE=""
ROLLBACK_ARMED=0
HAD_API_UNIT=0
HAD_WEB_UNIT=0
API_WAS_ACTIVE=0
WEB_WAS_ACTIVE=0
API_WAS_ENABLED=0
WEB_WAS_ENABLED=0

if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 11 install must run as root" >&2
  exit 2
fi
if [[ ! -f "$BP_ROOT/pyproject.toml" || ! -f "$DASHBOARD_DIR/package.json" ]]; then
  echo "Expected the BP repository and dashboard at $BP_ROOT" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" || ! -x "$BP_ROOT/.venv/bin/python" ]]; then
  echo "Missing BP environment or Python virtualenv" >&2
  exit 2
fi
if [[ -n "$EXPECTED_HEAD" ]]; then
  actual_head=$(git -C "$BP_ROOT" rev-parse HEAD)
  if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
    echo "Phase 11 install candidate mismatch" >&2
    echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
    echo "ACTUAL_HEAD=$actual_head" >&2
    exit 2
  fi
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" ]] || \
   [[ "$LIVE_TRADING_ENABLED" != "false" ]] || \
   [[ "$MAX_TRADE_SIZE_USD" != "0" ]] || \
   [[ "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 11 install requires research mode, live disabled, and zero trading limits" >&2
  exit 3
fi

RECORDER_BEFORE=$(systemctl is-active bp-recorder.service || true)
POSTGRES_BEFORE=$(systemctl is-active bp-postgres.service || true)
if [[ "$RECORDER_BEFORE" != "active" || "$POSTGRES_BEFORE" != "active" ]]; then
  echo "Recorder and PostgreSQL must already be active" >&2
  exit 4
fi

required_files=(
  "$BP_ROOT/deploy/systemd/bp-dashboard-api.service"
  "$BP_ROOT/deploy/systemd/bp-dashboard-web.service"
  "$BP_ROOT/src/bp_engine/dashboard/api.py"
  "$BP_ROOT/src/bp_engine/dashboard/repository.py"
  "$BP_ROOT/src/bp_engine/dashboard/service.py"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing Phase 11 deployment file: $path" >&2
    exit 2
  fi
done

unit_contract_ok() {
  local unit=$1
  grep -qx 'User=bp' "$unit" &&
    grep -qx 'Group=bp' "$unit" &&
    grep -qx 'NoNewPrivileges=true' "$unit" &&
    grep -qx 'ProtectSystem=full' "$unit" &&
    grep -qx 'ProtectHome=true' "$unit" &&
    grep -qx 'IPAddressDeny=any' "$unit" &&
    grep -qx 'IPAddressAllow=localhost' "$unit"
}
if ! unit_contract_ok "$BP_ROOT/deploy/systemd/bp-dashboard-api.service" || \
   ! unit_contract_ok "$BP_ROOT/deploy/systemd/bp-dashboard-web.service"; then
  echo "Dashboard systemd units do not satisfy the Phase 11 hardening contract" >&2
  exit 4
fi
if ! grep -qx 'ReadWritePaths=/opt/bp/apps/dashboard/.next/cache' \
  "$BP_ROOT/deploy/systemd/bp-dashboard-web.service"; then
  echo "Dashboard web unit must restrict writes to the Next runtime cache" >&2
  exit 4
fi

BACKUP_DIR=$(mktemp -d /var/tmp/bp-phase11-install-backup.XXXXXX)
NODE_STAGE=$(mktemp -d "$BP_ROOT/.node-stage.XXXXXX")

rollback_dashboard() {
  set +e
  echo "Rolling back Phase 11 dashboard services..." >&2
  systemctl stop "$WEB_UNIT_NAME" >/dev/null 2>&1 || true
  systemctl stop "$API_UNIT_NAME" >/dev/null 2>&1 || true

  if (( HAD_API_UNIT )); then
    cp "$BACKUP_DIR/$API_UNIT_NAME" "$API_UNIT_DST"
  else
    rm -f "$API_UNIT_DST"
  fi
  if (( HAD_WEB_UNIT )); then
    cp "$BACKUP_DIR/$WEB_UNIT_NAME" "$WEB_UNIT_DST"
  else
    rm -f "$WEB_UNIT_DST"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true

  if (( API_WAS_ENABLED )); then
    systemctl enable "$API_UNIT_NAME" >/dev/null 2>&1 || true
  else
    systemctl disable "$API_UNIT_NAME" >/dev/null 2>&1 || true
  fi
  if (( WEB_WAS_ENABLED )); then
    systemctl enable "$WEB_UNIT_NAME" >/dev/null 2>&1 || true
  else
    systemctl disable "$WEB_UNIT_NAME" >/dev/null 2>&1 || true
  fi
  if (( API_WAS_ACTIVE )); then
    systemctl start "$API_UNIT_NAME" >/dev/null 2>&1 || true
  fi
  if (( WEB_WAS_ACTIVE )); then
    systemctl start "$WEB_UNIT_NAME" >/dev/null 2>&1 || true
  fi

  if (( NODE_SWAPPED )); then
    rm -rf "$NODE_FINAL"
    if [[ -n "$NODE_PREVIOUS" && -d "$NODE_PREVIOUS" ]]; then
      mv "$NODE_PREVIOUS" "$NODE_FINAL"
    fi
  fi
  set -e
}

cleanup() {
  local rc=$?
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback_dashboard
  fi
  [[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"
  [[ -n "$NODE_STAGE" ]] && rm -rf "$NODE_STAGE"
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  if (( rc == 0 )) && [[ -n "$NODE_PREVIOUS" && -d "$NODE_PREVIOUS" ]]; then
    rm -rf "$NODE_PREVIOUS"
  fi
  set -e
}
trap cleanup EXIT

install_node_stage() {
  local machine node_arch archive tmp expected
  machine=$(uname -m)
  case "$machine" in
    x86_64) node_arch=x64 ;;
    aarch64|arm64) node_arch=arm64 ;;
    *) echo "Unsupported Node architecture: $machine" >&2; return 1 ;;
  esac
  archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
  tmp=$(mktemp -d /var/tmp/bp-node-download.XXXXXX)

  if ! command -v curl >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl xz-utils
  fi

  curl --fail --silent --show-error --location \
    "https://nodejs.org/dist/v${NODE_VERSION}/${archive}" -o "$tmp/$archive"
  curl --fail --silent --show-error --location \
    "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  expected=$(awk -v file="$archive" '$2 == file {print $1; exit}' "$tmp/SHASUMS256.txt")
  if [[ -z "$expected" ]]; then
    rm -rf "$tmp"
    echo "Published Node checksum not found for $archive" >&2
    return 1
  fi
  printf '%s  %s\n' "$expected" "$tmp/$archive" | sha256sum -c -
  tar -xJf "$tmp/$archive" -C "$NODE_STAGE" --strip-components=1
  rm -rf "$tmp"

  [[ "$($NODE_STAGE/bin/node --version)" == "v$NODE_VERSION" ]]
  "$NODE_STAGE/bin/node" --version
  env PATH="$NODE_STAGE/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NODE_STAGE/bin/npm" --version
}

install_node_stage

"$BP_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -e "$BP_ROOT"

NODE_PATH="$NODE_STAGE/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_STAGE/bin/npm" --prefix "$DASHBOARD_DIR" install --ignore-scripts --no-audit --no-fund --package-lock=false
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_STAGE/bin/npm" --prefix "$DASHBOARD_DIR" test
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_STAGE/bin/npm" --prefix "$DASHBOARD_DIR" run typecheck
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_STAGE/bin/npm" --prefix "$DASHBOARD_DIR" run build

install -d -o bp -g bp "$DASHBOARD_DIR/.next/cache"
chown -R "$BP_USER:$BP_GROUP" "$DASHBOARD_DIR/.next/cache"
chgrp -R "$BP_GROUP" "$DASHBOARD_DIR/node_modules" "$DASHBOARD_DIR/.next"
chmod -R g+rX "$DASHBOARD_DIR/node_modules" "$DASHBOARD_DIR/.next"

if [[ -e "$API_UNIT_DST" ]]; then
  cp "$API_UNIT_DST" "$BACKUP_DIR/$API_UNIT_NAME"
  HAD_API_UNIT=1
fi
if [[ -e "$WEB_UNIT_DST" ]]; then
  cp "$WEB_UNIT_DST" "$BACKUP_DIR/$WEB_UNIT_NAME"
  HAD_WEB_UNIT=1
fi
systemctl is-active --quiet "$API_UNIT_NAME" && API_WAS_ACTIVE=1 || true
systemctl is-active --quiet "$WEB_UNIT_NAME" && WEB_WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$API_UNIT_NAME" && API_WAS_ENABLED=1 || true
systemctl is-enabled --quiet "$WEB_UNIT_NAME" && WEB_WAS_ENABLED=1 || true

if [[ -e "$NODE_FINAL" ]]; then
  NODE_PREVIOUS="$BP_ROOT/.node.previous.$$"
  rm -rf "$NODE_PREVIOUS"
  mv "$NODE_FINAL" "$NODE_PREVIOUS"
fi
mv "$NODE_STAGE" "$NODE_FINAL"
NODE_STAGE=""
NODE_SWAPPED=1
chown -R root:"$BP_GROUP" "$NODE_FINAL"
chmod -R g+rX "$NODE_FINAL"

ROLLBACK_ARMED=1
install -m 0644 "$BP_ROOT/deploy/systemd/bp-dashboard-api.service" "$API_UNIT_DST"
install -m 0644 "$BP_ROOT/deploy/systemd/bp-dashboard-web.service" "$WEB_UNIT_DST"
systemctl daemon-reload
systemctl enable --now "$API_UNIT_NAME"

api_ready=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8787/health" >/dev/null; then
    api_ready=1
    break
  fi
  sleep 1
done
if (( ! api_ready )); then
  systemctl --no-pager --full status "$API_UNIT_NAME" >&2 || true
  journalctl -u "$API_UNIT_NAME" -n 80 --no-pager >&2 || true
  echo "Phase 11 dashboard API did not become healthy" >&2
  exit 5
fi

systemctl enable --now "$WEB_UNIT_NAME"
web_ready=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:3000/" >/dev/null; then
    web_ready=1
    break
  fi
  sleep 1
done
if (( ! web_ready )); then
  systemctl --no-pager --full status "$WEB_UNIT_NAME" >&2 || true
  journalctl -u "$WEB_UNIT_NAME" -n 80 --no-pager >&2 || true
  echo "Phase 11 dashboard web service did not become healthy" >&2
  exit 5
fi

SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase11-snapshot.XXXXXX.json)
curl -fsS "http://127.0.0.1:8787/api/v1/snapshot" > "$SNAPSHOT_FILE"
"$BP_ROOT/.venv/bin/python" - "$SNAPSHOT_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_mode = {
    "trading_mode": "RESEARCH",
    "live_trading_enabled": False,
    "execution_available": False,
    "paper_execution_available": False,
}
if snapshot.get("mode") != expected_mode:
    raise SystemExit(f"dashboard mode is not fail-closed research: {snapshot.get('mode')!r}")
paper = snapshot.get("paper_pnl") or {}
if paper.get("status") != "UNAVAILABLE_UNTIL_PHASE_12" or paper.get("value") is not None:
    raise SystemExit("dashboard paper P&L boundary is invalid")
for key in ("active_markets", "feed_health", "performance", "prediction_history"):
    if not isinstance(snapshot.get(key), list):
        raise SystemExit(f"dashboard snapshot {key} is not a list")
generated = datetime.fromisoformat(snapshot["generated_at"])
if generated.tzinfo is None:
    raise SystemExit("dashboard generated_at is not timezone-aware")
age = (datetime.now(UTC) - generated.astimezone(UTC)).total_seconds()
if age < -5 or age > 120:
    raise SystemExit(f"dashboard snapshot is stale or future-dated: age={age}")
PY
rm -f "$SNAPSHOT_FILE"
SNAPSHOT_FILE=""

mutation_status=$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST "http://127.0.0.1:8787/api/v1/snapshot")
if [[ "$mutation_status" != "405" ]]; then
  echo "Dashboard API mutation endpoint is not fail-closed" >&2
  exit 6
fi

if ! ss -ltn | grep -Eq '127\.0\.0\.1:8787[[:space:]]' || \
   ! ss -ltn | grep -Eq '127\.0\.0\.1:3000[[:space:]]'; then
  echo "Dashboard services are not listening on expected loopback ports" >&2
  ss -ltn >&2 || true
  exit 6
fi
if ss -ltn | grep -Eq '(0\.0\.0\.0|\[::\]):(8787|3000)[[:space:]]'; then
  echo "Dashboard service exposed a non-loopback listener" >&2
  ss -ltn >&2 || true
  exit 6
fi

RECORDER_AFTER=$(systemctl is-active bp-recorder.service || true)
if [[ "$RECORDER_AFTER" != "active" ]]; then
  echo "Recorder was disturbed by Phase 11 installation" >&2
  exit 7
fi

ROLLBACK_ARMED=0
NODE_SWAPPED=0

install -d -o "$BP_USER" -g "$BP_GROUP" /var/lib/bp/evidence
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence="/var/lib/bp/evidence/phase11-install-$stamp.txt"
{
  echo "PHASE11_INSTALL=PASS"
  echo "HEAD=$(git -C "$BP_ROOT" rev-parse HEAD)"
  echo "NODE_VERSION=$($NODE_FINAL/bin/node --version)"
  echo "API_STATUS=$(systemctl is-active "$API_UNIT_NAME")"
  echo "WEB_STATUS=$(systemctl is-active "$WEB_UNIT_NAME")"
  echo "RECORDER_BEFORE=$RECORDER_BEFORE"
  echo "RECORDER_AFTER=$RECORDER_AFTER"
  echo "API_LISTENER=127.0.0.1:8787"
  echo "WEB_LISTENER=127.0.0.1:3000"
} | tee "$evidence"
chown "$BP_USER:$BP_GROUP" "$evidence"
chmod 0640 "$evidence"

echo "PHASE11_INSTALL=PASS"
echo "Dashboard is localhost-only at http://127.0.0.1:3000"
echo "Use an SSH tunnel to access it remotely."
