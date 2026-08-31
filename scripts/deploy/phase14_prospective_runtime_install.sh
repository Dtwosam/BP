#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
BP_ROOT="${BP_ROOT:-/opt/bp}"
CANDIDATE_ROOT="${BP_CANDIDATE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
BP_USER="${BP_USER:-bp}"
BP_GROUP="${BP_GROUP:-bp}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"

PREDICTOR_UNIT_NAME=bp-live-predictor.service
OUTCOME_UNIT_NAME=bp-prospective-outcomes.service
PREDICTOR_UNIT_SRC="$CANDIDATE_ROOT/deploy/$PREDICTOR_UNIT_NAME"
OUTCOME_UNIT_SRC="$CANDIDATE_ROOT/deploy/$OUTCOME_UNIT_NAME"
PREDICTOR_UNIT_DST="/etc/systemd/system/$PREDICTOR_UNIT_NAME"
OUTCOME_UNIT_DST="/etc/systemd/system/$OUTCOME_UNIT_NAME"

BACKUP_DIR=""
ROLLBACK_ARMED=0
HAD_PREDICTOR_UNIT=0
HAD_OUTCOME_UNIT=0
PREDICTOR_WAS_ACTIVE=0
PREDICTOR_WAS_ENABLED=0
OUTCOME_WAS_ACTIVE=0
OUTCOME_WAS_ENABLED=0
OLD_HEAD=""
OLD_REF=""

fail() {
  echo "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  fail "root_required"
fi
if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  fail "expected_head_must_be_40_lowercase_hex"
fi
if [[ ! -d "$BP_ROOT/.git" ]]; then
  fail "missing_deployed_repository"
fi
if [[ ! -r "$ENV_FILE" || ! -x "$BP_ROOT/.venv/bin/python" ]]; then
  fail "missing_environment_or_python_runtime"
fi
if [[ -n "$(git -C "$BP_ROOT" status --porcelain)" ]]; then
  fail "deployed_checkout_not_clean"
fi

CANDIDATE_HEAD=$(git -C "$CANDIDATE_ROOT" rev-parse HEAD 2>/dev/null || true)
if [[ "$CANDIDATE_HEAD" != "$EXPECTED_HEAD" ]]; then
  fail "candidate_checkout_head_mismatch"
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || \
      "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || \
      "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  fail "research_zero_money_boundary_not_satisfied"
fi
if ! grep -q '^DATABASE_URL=.' "$ENV_FILE"; then
  fail "database_url_missing"
fi

CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
)
for service in "${CORE_SERVICES[@]}"; do
  if [[ "$(systemctl is-active "$service" 2>/dev/null || true)" != "active" ]]; then
    fail "core_service_not_active_before:$service"
  fi
done

validate_unit() {
  local unit=$1
  local module=$2
  local contract

  if [[ ! -f "$unit" ]]; then
    fail "missing_candidate_unit:$unit"
  fi
  for contract in \
    'User=bp' \
    'Group=bp' \
    'WorkingDirectory=/opt/bp' \
    'EnvironmentFile=/etc/bp/bp.env' \
    'Environment=MODE=research' \
    'Environment=LIVE_TRADING_ENABLED=false' \
    'Environment=MAX_TRADE_SIZE_USD=0' \
    'Environment=MAX_DAILY_LOSS_USD=0' \
    'Restart=always' \
    'NoNewPrivileges=true' \
    'ProtectHome=true' \
    'ProtectSystem=full'; do
    if ! grep -qx "$contract" "$unit"; then
      fail "candidate_unit_contract_violation:$unit:$contract"
    fi
  done
  if ! grep -q -- "-m $module run" "$unit"; then
    fail "candidate_unit_entrypoint_violation:$unit"
  fi
}

validate_unit "$PREDICTOR_UNIT_SRC" bp_engine.live_prediction
validate_unit "$OUTCOME_UNIT_SRC" bp_engine.prospective_outcomes

OLD_HEAD=$(git -C "$BP_ROOT" rev-parse HEAD)
OLD_REF=$(git -C "$BP_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
BACKUP_DIR=$(mktemp -d /var/tmp/bp-phase14-prospective-runtime-backup.XXXXXX)

if [[ -e "$PREDICTOR_UNIT_DST" ]]; then
  cp "$PREDICTOR_UNIT_DST" "$BACKUP_DIR/$PREDICTOR_UNIT_NAME"
  HAD_PREDICTOR_UNIT=1
fi
if [[ -e "$OUTCOME_UNIT_DST" ]]; then
  cp "$OUTCOME_UNIT_DST" "$BACKUP_DIR/$OUTCOME_UNIT_NAME"
  HAD_OUTCOME_UNIT=1
fi
systemctl is-active --quiet "$PREDICTOR_UNIT_NAME" && PREDICTOR_WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$PREDICTOR_UNIT_NAME" && PREDICTOR_WAS_ENABLED=1 || true
systemctl is-active --quiet "$OUTCOME_UNIT_NAME" && OUTCOME_WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$OUTCOME_UNIT_NAME" && OUTCOME_WAS_ENABLED=1 || true

restore_checkout() {
  if [[ -n "$OLD_REF" ]]; then
    git -C "$BP_ROOT" checkout --force "$OLD_REF" >/dev/null 2>&1 || true
    if [[ "$(git -C "$BP_ROOT" rev-parse HEAD 2>/dev/null || true)" != "$OLD_HEAD" ]]; then
      git -C "$BP_ROOT" checkout --detach --force "$OLD_HEAD" >/dev/null 2>&1 || true
    fi
  else
    git -C "$BP_ROOT" checkout --detach --force "$OLD_HEAD" >/dev/null 2>&1 || true
  fi
}

restore_unit_state() {
  local unit_name=$1
  local was_enabled=$2
  local was_active=$3

  if (( was_enabled )); then
    systemctl enable "$unit_name" >/dev/null 2>&1 || true
  else
    systemctl disable "$unit_name" >/dev/null 2>&1 || true
  fi
  if (( was_active )); then
    systemctl start "$unit_name" >/dev/null 2>&1 || true
  fi
}

rollback_phase14_prospective_runtime() {
  set +e
  echo "Rolling back Phase 14 prospective research runtime..." >&2
  systemctl stop "$PREDICTOR_UNIT_NAME" >/dev/null 2>&1 || true
  systemctl stop "$OUTCOME_UNIT_NAME" >/dev/null 2>&1 || true

  if (( HAD_PREDICTOR_UNIT )); then
    cp "$BACKUP_DIR/$PREDICTOR_UNIT_NAME" "$PREDICTOR_UNIT_DST"
  else
    rm -f "$PREDICTOR_UNIT_DST"
  fi
  if (( HAD_OUTCOME_UNIT )); then
    cp "$BACKUP_DIR/$OUTCOME_UNIT_NAME" "$OUTCOME_UNIT_DST"
  else
    rm -f "$OUTCOME_UNIT_DST"
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  restore_checkout
  restore_unit_state "$PREDICTOR_UNIT_NAME" "$PREDICTOR_WAS_ENABLED" "$PREDICTOR_WAS_ACTIVE"
  restore_unit_state "$OUTCOME_UNIT_NAME" "$OUTCOME_WAS_ENABLED" "$OUTCOME_WAS_ACTIVE"
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback_phase14_prospective_runtime
  fi
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  set -e
  exit "$rc"
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

ROLLBACK_ARMED=1
git -C "$BP_ROOT" checkout --detach --force "$EXPECTED_HEAD"
if [[ "$(git -C "$BP_ROOT" rev-parse HEAD)" != "$EXPECTED_HEAD" ]]; then
  fail "deployed_head_did_not_switch_to_candidate"
fi
if [[ -n "$(git -C "$BP_ROOT" status --porcelain)" ]]; then
  fail "candidate_checkout_not_clean_after_switch"
fi

run_as_bp "$BP_ROOT/.venv/bin/python" -c \
  'import bp_engine.live_prediction; import bp_engine.prospective_outcomes; print("PROSPECTIVE_RUNTIME_IMPORTS=PASS")'

install -m 0644 "$PREDICTOR_UNIT_SRC" "$PREDICTOR_UNIT_DST"
install -m 0644 "$OUTCOME_UNIT_SRC" "$OUTCOME_UNIT_DST"
systemctl daemon-reload
systemctl enable --now "$PREDICTOR_UNIT_NAME"
systemctl enable --now "$OUTCOME_UNIT_NAME"

wait_active() {
  local service=$1
  local ready=0
  for _ in $(seq 1 30); do
    if [[ "$(systemctl is-active "$service" 2>/dev/null || true)" == "active" ]]; then
      ready=1
      break
    fi
    sleep 1
  done
  if (( ! ready )); then
    systemctl --no-pager --full status "$service" >&2 || true
    journalctl -u "$service" -n 80 --no-pager >&2 || true
    fail "service_did_not_become_active:$service"
  fi
}

wait_active "$PREDICTOR_UNIT_NAME"
wait_active "$OUTCOME_UNIT_NAME"
sleep 3
for service in "$PREDICTOR_UNIT_NAME" "$OUTCOME_UNIT_NAME"; do
  if [[ "$(systemctl is-active "$service" 2>/dev/null || true)" != "active" ]]; then
    journalctl -u "$service" -n 80 --no-pager >&2 || true
    fail "prospective_service_not_stably_active:$service"
  fi
done

for service in "${CORE_SERVICES[@]}"; do
  if [[ "$(systemctl is-active "$service" 2>/dev/null || true)" != "active" ]]; then
    fail "core_service_not_active_after:$service"
  fi
done

SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-prospective-runtime-snapshot.XXXXXX.json)
if ! curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE"; then
  rm -f "$SNAPSHOT_FILE"
  fail "dashboard_snapshot_unavailable"
fi
"$BP_ROOT/.venv/bin/python" - "$SNAPSHOT_FILE" <<'PY'
from __future__ import annotations

import json
import sys
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
    raise SystemExit("dashboard paper execution unavailable")
PY
rm -f "$SNAPSHOT_FILE"

if [[ "$(git -C "$BP_ROOT" rev-parse HEAD)" != "$EXPECTED_HEAD" ]]; then
  fail "deployed_head_changed_after_install"
fi

MODE_AFTER=$(read_env MODE)
LIVE_TRADING_ENABLED_AFTER=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD_AFTER=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD_AFTER=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE_AFTER" != "research" || \
      "$LIVE_TRADING_ENABLED_AFTER" != "false" || \
      "$MAX_TRADE_SIZE_USD_AFTER" != "0" || \
      "$MAX_DAILY_LOSS_USD_AFTER" != "0" ]]; then
  fail "research_zero_money_boundary_changed_after_install"
fi

ROLLBACK_ARMED=0
install -d -o "$BP_USER" -g "$BP_GROUP" /var/lib/bp/evidence
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_file="/var/lib/bp/evidence/phase14-prospective-runtime-install-$stamp.txt"
{
  echo "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS"
  echo "OLD_HEAD=$OLD_HEAD"
  echo "NEW_HEAD=$(git -C "$BP_ROOT" rev-parse HEAD)"
  echo "PREDICTOR_ACTIVE=$(systemctl is-active "$PREDICTOR_UNIT_NAME")"
  echo "PREDICTOR_ENABLED=$(systemctl is-enabled "$PREDICTOR_UNIT_NAME")"
  echo "OUTCOME_ACTIVE=$(systemctl is-active "$OUTCOME_UNIT_NAME")"
  echo "OUTCOME_ENABLED=$(systemctl is-enabled "$OUTCOME_UNIT_NAME")"
  for service in "${CORE_SERVICES[@]}"; do
    echo "CORE_SERVICE=${service}:$(systemctl is-active "$service")"
  done
  echo "MODE=$MODE_AFTER"
  echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED_AFTER"
  echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD_AFTER"
  echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD_AFTER"
} | tee "$evidence_file"
chown "$BP_USER:$BP_GROUP" "$evidence_file"
chmod 0640 "$evidence_file"

echo "EVIDENCE_FILE=$evidence_file"
echo "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS"
