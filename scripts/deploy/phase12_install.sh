#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
BP_ROOT="${BP_ROOT:-/opt/bp}"
BP_USER="${BP_USER:-bp}"
BP_GROUP="${BP_GROUP:-bp}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
PAPER_ONCE_TIMEOUT_SECONDS="${PHASE12_PAPER_ONCE_TIMEOUT_SECONDS:-120}"
DASHBOARD_DIR="$BP_ROOT/apps/dashboard"
NODE_ROOT="$BP_ROOT/.node"
PAPER_UNIT_NAME=bp-paper-execution.service
PAPER_UNIT_SRC="$BP_ROOT/deploy/systemd/$PAPER_UNIT_NAME"
PAPER_UNIT_DST="/etc/systemd/system/$PAPER_UNIT_NAME"
BACKUP_DIR=""
OLD_NEXT=""
ROLLBACK_ARMED=0
HAD_PAPER_UNIT=0
PAPER_WAS_ACTIVE=0
PAPER_WAS_ENABLED=0
WEB_WAS_ACTIVE=0
API_WAS_ACTIVE=0

if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 12 install must run as root" >&2
  exit 2
fi
if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if ! [[ "$PAPER_ONCE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PHASE12_PAPER_ONCE_TIMEOUT_SECONDS must be a positive integer" >&2
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
actual_head=$(git -C "$BP_ROOT" rev-parse HEAD)
if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "Phase 12 install candidate mismatch" >&2
  echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
  echo "ACTUAL_HEAD=$actual_head" >&2
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
if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 12 install requires research mode, live disabled, and zero real-money limits" >&2
  exit 3
fi
if ! grep -q '^DATABASE_URL=.' "$ENV_FILE"; then
  echo "DATABASE_URL is missing from $ENV_FILE" >&2
  exit 3
fi

for service in \
  bp-recorder.service \
  bp-postgres.service \
  bp-dashboard-api.service \
  bp-dashboard-web.service; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "$service must be active before Phase 12 installation" >&2
    exit 4
  fi
done

required_files=(
  "$PAPER_UNIT_SRC"
  "$BP_ROOT/src/bp_engine/execution/cli.py"
  "$BP_ROOT/src/bp_engine/execution/service.py"
  "$BP_ROOT/src/bp_engine/dashboard/repository.py"
  "$BP_ROOT/migrations/0011_paper_execution.sql"
  "$BP_ROOT/migrations/0012_paper_replay_indexes.sql"
  "$BP_ROOT/scripts/deploy/ensure_phase12_replay_indexes.py"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "Missing Phase 12 deployment file: $path" >&2
    exit 2
  fi
done

for contract in \
  'User=bp' \
  'Group=bp' \
  'Environment=MODE=research' \
  'Environment=LIVE_TRADING_ENABLED=false' \
  'Environment=MAX_TRADE_SIZE_USD=0' \
  'Environment=MAX_DAILY_LOSS_USD=0' \
  'NoNewPrivileges=true' \
  'ProtectHome=true' \
  'ProtectSystem=full' \
  'IPAddressDeny=any' \
  'IPAddressAllow=localhost'; do
  if ! grep -qx "$contract" "$PAPER_UNIT_SRC"; then
    echo "Paper worker unit violates contract: $contract" >&2
    exit 4
  fi
done
if grep -Eq -- '--host|--port' "$PAPER_UNIT_SRC"; then
  echo "Paper worker must not expose a listener" >&2
  exit 4
fi
if [[ ! -x "$NODE_ROOT/bin/node" || "$($NODE_ROOT/bin/node --version)" != "v24.20.0" ]]; then
  echo "Phase 12 requires the accepted Phase 11 Node 24.20.0 runtime" >&2
  exit 4
fi

BACKUP_DIR=$(mktemp -d /var/tmp/bp-phase12-install-backup.XXXXXX)
if [[ -e "$PAPER_UNIT_DST" ]]; then
  cp "$PAPER_UNIT_DST" "$BACKUP_DIR/$PAPER_UNIT_NAME"
  HAD_PAPER_UNIT=1
fi
systemctl is-active --quiet "$PAPER_UNIT_NAME" && PAPER_WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$PAPER_UNIT_NAME" && PAPER_WAS_ENABLED=1 || true
systemctl is-active --quiet bp-dashboard-api.service && API_WAS_ACTIVE=1 || true
systemctl is-active --quiet bp-dashboard-web.service && WEB_WAS_ACTIVE=1 || true

rollback_phase12() {
  set +e
  echo "Rolling back Phase 12-owned runtime changes..." >&2
  systemctl stop "$PAPER_UNIT_NAME" >/dev/null 2>&1 || true
  if (( HAD_PAPER_UNIT )); then
    cp "$BACKUP_DIR/$PAPER_UNIT_NAME" "$PAPER_UNIT_DST"
  else
    rm -f "$PAPER_UNIT_DST"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if (( PAPER_WAS_ENABLED )); then
    systemctl enable "$PAPER_UNIT_NAME" >/dev/null 2>&1 || true
  else
    systemctl disable "$PAPER_UNIT_NAME" >/dev/null 2>&1 || true
  fi
  if (( PAPER_WAS_ACTIVE )); then
    systemctl start "$PAPER_UNIT_NAME" >/dev/null 2>&1 || true
  fi

  if [[ -n "$OLD_NEXT" && -d "$OLD_NEXT" ]]; then
    rm -rf "$DASHBOARD_DIR/.next"
    mv "$OLD_NEXT" "$DASHBOARD_DIR/.next"
    OLD_NEXT=""
  fi
  if (( API_WAS_ACTIVE )); then
    systemctl restart bp-dashboard-api.service >/dev/null 2>&1 || true
  fi
  if (( WEB_WAS_ACTIVE )); then
    systemctl restart bp-dashboard-web.service >/dev/null 2>&1 || true
  fi
  set -e
}

cleanup() {
  local rc=$?
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback_phase12
  fi
  if [[ -n "$OLD_NEXT" && -d "$OLD_NEXT" ]]; then
    rm -rf "$OLD_NEXT"
  fi
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  set -e
}
trap cleanup EXIT

run_as_bp() {
  sudo -u "$BP_USER" env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    BP_ENV_FILE="$ENV_FILE" \
    "$@"
}

paper_once() {
  local output=$1
  local rc=0
  set +e
  run_as_bp timeout --signal=TERM --kill-after=10s "${PAPER_ONCE_TIMEOUT_SECONDS}s" \
    "$BP_ROOT/.venv/bin/python" -m bp_engine.execution --once > "$output"
  rc=$?
  set -e
  if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
    echo "Phase 12 paper execution --once timed out" >&2
  fi
  return "$rc"
}

"$BP_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -e "$BP_ROOT"

run_as_bp "$BP_ROOT/.venv/bin/python" - <<'PY'
from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.storage import schema

engine = create_engine(get_settings().database_url)
schema.metadata.create_all(engine)
engine.dispose()
print("PAPER_SCHEMA=READY")
PY

run_as_bp "$BP_ROOT/.venv/bin/python" \
  "$BP_ROOT/scripts/deploy/ensure_phase12_replay_indexes.py"

NODE_PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" install --ignore-scripts --no-audit --no-fund --package-lock=false
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" test
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" run typecheck

ROLLBACK_ARMED=1
systemctl stop bp-dashboard-web.service
if [[ -d "$DASHBOARD_DIR/.next" ]]; then
  OLD_NEXT="$BACKUP_DIR/dashboard-next"
  mv "$DASHBOARD_DIR/.next" "$OLD_NEXT"
fi
env PATH="$NODE_PATH" NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" run build
install -d -o "$BP_USER" -g "$BP_GROUP" "$DASHBOARD_DIR/.next/cache"
chgrp -R "$BP_GROUP" "$DASHBOARD_DIR/node_modules" "$DASHBOARD_DIR/.next"
chmod -R g+rX "$DASHBOARD_DIR/node_modules" "$DASHBOARD_DIR/.next"
chown -R "$BP_USER:$BP_GROUP" "$DASHBOARD_DIR/.next/cache"

install -m 0644 "$PAPER_UNIT_SRC" "$PAPER_UNIT_DST"
systemctl daemon-reload

paper_once /var/tmp/bp-phase12-install-once.json
paper_once /var/tmp/bp-phase12-install-rerun.json

systemctl restart bp-dashboard-api.service
systemctl start bp-dashboard-web.service
systemctl enable --now "$PAPER_UNIT_NAME"

for service in bp-dashboard-api.service bp-dashboard-web.service "$PAPER_UNIT_NAME"; do
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
    echo "$service did not become active" >&2
    exit 5
  fi
done

api_ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8787/health >/dev/null; then
    api_ready=1
    break
  fi
  sleep 1
done
if (( ! api_ready )); then
  echo "Phase 12 dashboard API did not become healthy" >&2
  exit 5
fi
web_ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3000/ >/dev/null; then
    web_ready=1
    break
  fi
  sleep 1
done
if (( ! web_ready )); then
  echo "Phase 12 dashboard web did not become healthy" >&2
  exit 5
fi

snapshot=$(mktemp /var/tmp/bp-phase12-snapshot.XXXXXX.json)
curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$snapshot"
"$BP_ROOT/.venv/bin/python" - "$snapshot" <<'PY'
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = snapshot.get("mode") or {}
if mode.get("trading_mode") != "RESEARCH":
    raise SystemExit("dashboard left RESEARCH mode")
if mode.get("live_trading_enabled") is not False:
    raise SystemExit("dashboard reports live trading enabled")
if mode.get("execution_available") is not False:
    raise SystemExit("dashboard reports real execution available")
if mode.get("paper_execution_available") is not True:
    raise SystemExit("dashboard does not expose Phase 12 paper execution")
paper = snapshot.get("paper_pnl") or {}
if paper.get("status") != "AVAILABLE":
    raise SystemExit("paper account evidence is unavailable")
reconciliation = paper.get("reconciliation") or {}
if reconciliation.get("status") != "OK" or int(reconciliation.get("violation_count", -1)) != 0:
    raise SystemExit(f"paper reconciliation failed: {reconciliation!r}")
if Decimal(str(paper.get("current_cash"))) < Decimal("0"):
    raise SystemExit("paper cash is negative")
for key in ("paper_orders", "paper_fills", "paper_settlements"):
    if not isinstance(snapshot.get(key), list):
        raise SystemExit(f"dashboard {key} is not a list")
PY
rm -f "$snapshot" /var/tmp/bp-phase12-install-once.json /var/tmp/bp-phase12-install-rerun.json

for service in bp-recorder.service bp-postgres.service bp-dashboard-api.service bp-dashboard-web.service "$PAPER_UNIT_NAME"; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "$service is not active after Phase 12 installation" >&2
    exit 7
  fi
done

run_as_bp "$BP_ROOT/.venv/bin/python" - <<'PY' > /var/tmp/bp-phase12-install-reconciliation.json
from __future__ import annotations

import json
from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.dashboard.repository import PostgresDashboardRepository

engine = create_engine(get_settings().database_url)
evidence = PostgresDashboardRepository(engine).get_paper_execution_evidence()
reconciliation = evidence["paper_pnl"]["reconciliation"]
if reconciliation["status"] != "OK" or int(reconciliation["violation_count"]) != 0:
    raise SystemExit(f"reconciliation violation: {reconciliation!r}")
print(json.dumps({
    "paper_orders": len(evidence["paper_orders"]),
    "paper_fills": len(evidence["paper_fills"]),
    "paper_settlements": len(evidence["paper_settlements"]),
    "reconciliation": reconciliation,
}, sort_keys=True, default=str))
engine.dispose()
PY

ROLLBACK_ARMED=0
install -d -o "$BP_USER" -g "$BP_GROUP" /var/lib/bp/evidence
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_file="/var/lib/bp/evidence/phase12-install-$stamp.txt"
{
  echo "PHASE12_INSTALL=PASS"
  echo "HEAD=$(git -C "$BP_ROOT" rev-parse HEAD)"
  echo "PAPER_WORKER_STATUS=$(systemctl is-active "$PAPER_UNIT_NAME")"
  echo "DASHBOARD_API_STATUS=$(systemctl is-active bp-dashboard-api.service)"
  echo "DASHBOARD_WEB_STATUS=$(systemctl is-active bp-dashboard-web.service)"
  echo "RECORDER_STATUS=$(systemctl is-active bp-recorder.service)"
  echo "POSTGRES_STATUS=$(systemctl is-active bp-postgres.service)"
  cat /var/tmp/bp-phase12-install-reconciliation.json
} | tee "$evidence_file"
rm -f /var/tmp/bp-phase12-install-reconciliation.json
chown "$BP_USER:$BP_GROUP" "$evidence_file"
chmod 0640 "$evidence_file"

echo "PHASE12_INSTALL=PASS"
echo "Paper execution worker is money-disabled and active."
