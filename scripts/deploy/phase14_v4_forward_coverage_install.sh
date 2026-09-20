#!/usr/bin/env bash
set -Eeuo pipefail

SHA="${PHASE14_V4_FORWARD_HEAD:-}"
BRANCH="${PHASE14_V4_FORWARD_BRANCH:-main}"
REPO="${PHASE14_V4_FORWARD_REPO:-/opt/bp}"
ENV_FILE="${PHASE14_V4_FORWARD_ENV_FILE:-/etc/bp/bp.env}"
SAFETY_FILE="${PHASE14_V4_FORWARD_SAFETY_FILE:-/etc/bp/bp-prospective-runtime-safety.env}"
RUNTIME_ROOT="${PHASE14_V4_FORWARD_RUNTIME_ROOT:-/var/lib/bp/runtime}"
EVIDENCE_DIR="${PHASE14_V4_FORWARD_EVIDENCE_DIR:-/var/lib/bp/evidence}"
SERVICE_UNIT=bp-v4-forward-coverage.service
TIMER_UNIT=bp-v4-forward-coverage.timer
SERVICE_PATH="/etc/systemd/system/$SERVICE_UNIT"
TIMER_PATH="/etc/systemd/system/$TIMER_UNIT"
CURRENT_LINK="$RUNTIME_ROOT/v4-forward-current"

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V4_FORWARD_HEAD must be an exact 40-character verified main SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_V4_FORWARD_BRANCH contains unsupported characters" >&2
  exit 2
fi
for path in "$REPO" "$ENV_FILE" "$SAFETY_FILE" "$RUNTIME_ROOT" "$EVIDENCE_DIR"; do
  [[ "$path" == /* ]] || {
    echo "all production paths must be absolute: $path" >&2
    exit 2
  }
done
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi

VERSION_DIR="$RUNTIME_ROOT/v4-forward-$SHA"
STAGING_DIR="$RUNTIME_ROOT/.v4-forward-$SHA.staging"
OLD_DEPLOYED_HEAD=""
OLD_LINK_TARGET=""
RECORDER_PID_BEFORE=""
RECORDER_PID_AFTER=""
SERVICE_PREEXISTED=0
TIMER_PREEXISTED=0
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
SERVICE_BACKUP=""
TIMER_BACKUP=""
CYCLE_FILE=""
DISK_BEFORE=""
DISK_AFTER=""
ROLLBACK_ARMED=0

fail() {
  echo "PHASE14_V4_FORWARD_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_research_zero_money() {
  [[ -f "$ENV_FILE" ]] || fail "environment_file_missing"
  [[ -f "$SAFETY_FILE" ]] || fail "prospective_safety_file_missing"
  local mode live trade loss safe_mode safe_live safe_trade safe_loss
  mode=$(read_env "$ENV_FILE" MODE)
  live=$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)
  trade=$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)
  loss=$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)
  safe_mode=$(read_env "$SAFETY_FILE" MODE)
  safe_live=$(read_env "$SAFETY_FILE" LIVE_TRADING_ENABLED)
  safe_trade=$(read_env "$SAFETY_FILE" MAX_TRADE_SIZE_USD)
  safe_loss=$(read_env "$SAFETY_FILE" MAX_DAILY_LOSS_USD)
  [[ "$mode" == "research" ]] || fail "mode_not_research"
  [[ "$live" == "false" ]] || fail "live_trading_not_false"
  [[ "$trade" == "0" ]] || fail "max_trade_size_not_zero"
  [[ "$loss" == "0" ]] || fail "max_daily_loss_not_zero"
  [[ "$safe_mode" == "research" ]] || fail "safety_mode_not_research"
  [[ "$safe_live" == "false" ]] || fail "safety_live_trading_not_false"
  [[ "$safe_trade" == "0" ]] || fail "safety_max_trade_size_not_zero"
  [[ "$safe_loss" == "0" ]] || fail "safety_max_daily_loss_not_zero"
}

require_services_active() {
  local unit
  for unit in bp-recorder.service bp-postgres.service; do
    systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
  done
}

run_disk_health() {
  local output=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py"       disk-health --env-file "$ENV_FILE" > "$output"; then
    cat "$output" >&2 || true
    fail "disk_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$output" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(f"disk status is not ok: {payload.get('status')!r}")
PY
}

capture_previous_state() {
  if [[ -L "$CURRENT_LINK" ]]; then
    OLD_LINK_TARGET=$(readlink "$CURRENT_LINK")
  fi
  if [[ -f "$SERVICE_PATH" ]]; then
    SERVICE_PREEXISTED=1
    SERVICE_BACKUP=$(mktemp /var/tmp/bp-v4-forward-service.XXXXXX)
    cp -a "$SERVICE_PATH" "$SERVICE_BACKUP"
  fi
  if [[ -f "$TIMER_PATH" ]]; then
    TIMER_PREEXISTED=1
    TIMER_BACKUP=$(mktemp /var/tmp/bp-v4-forward-timer.XXXXXX)
    cp -a "$TIMER_PATH" "$TIMER_BACKUP"
  fi
  systemctl is-enabled --quiet "$TIMER_UNIT" 2>/dev/null && TIMER_WAS_ENABLED=1 || true
  systemctl is-active --quiet "$TIMER_UNIT" 2>/dev/null && TIMER_WAS_ACTIVE=1 || true
}

rollback() {
  set +e
  echo "PHASE14_V4_FORWARD_ROLLBACK=START" >&2
  systemctl disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true
  if (( SERVICE_PREEXISTED )); then
    cp -a "$SERVICE_BACKUP" "$SERVICE_PATH"
  else
    rm -f "$SERVICE_PATH"
  fi
  if (( TIMER_PREEXISTED )); then
    cp -a "$TIMER_BACKUP" "$TIMER_PATH"
  else
    rm -f "$TIMER_PATH"
  fi
  if [[ -n "$OLD_LINK_TARGET" ]]; then
    ln -sfn "$OLD_LINK_TARGET" "$CURRENT_LINK"
  else
    rm -f "$CURRENT_LINK"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  if (( TIMER_WAS_ENABLED )); then
    systemctl enable "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi
  if (( TIMER_WAS_ACTIVE )); then
    systemctl start "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi
  echo "PHASE14_V4_FORWARD_ROLLBACK=COMPLETE" >&2
}

cleanup() {
  rm -rf "$STAGING_DIR"
  rm -f "${SERVICE_BACKUP:-}" "${TIMER_BACKUP:-}"     "${CYCLE_FILE:-}" "${DISK_BEFORE:-}" "${DISK_AFTER:-}"
}

on_exit() {
  local status=$?
  if (( status != 0 && ROLLBACK_ARMED == 1 )); then
    rollback
  fi
  cleanup
  exit "$status"
}
trap on_exit EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
OLD_DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD)
require_research_zero_money
require_services_active
RECORDER_PID_BEFORE=$(systemctl show --property=MainPID --value bp-recorder.service)
[[ "$RECORDER_PID_BEFORE" =~ ^[1-9][0-9]*$ ]] || fail "invalid_recorder_pid_before"

DISK_BEFORE=$(mktemp /var/tmp/bp-v4-forward-disk-before.XXXXXX.json)
run_disk_health "$DISK_BEFORE"

git -C "$REPO" fetch --quiet origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
REMOTE_HEAD=$(git -C "$REPO" rev-parse "origin/$BRANCH")
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "remote_branch_head_mismatch:$REMOTE_HEAD"

for required_path in   src/bp_engine/features/v4_models.py   src/bp_engine/features/v4_service.py   src/bp_engine/features/v4_coverage.py   src/bp_engine/features/v4_forward.py   src/bp_engine/features/v4_forward_cli.py   scripts/run_v4_forward_coverage.py   deploy/bp-v4-forward-coverage.service   deploy/bp-v4-forward-coverage.timer; do
  git -C "$REPO" cat-file -e "$SHA:$required_path" || fail "candidate_path_missing:$required_path"
done

capture_previous_state
ROLLBACK_ARMED=1

install -d -o root -g bp -m 0755 "$RUNTIME_ROOT"
if [[ ! -d "$VERSION_DIR" ]]; then
  rm -rf "$STAGING_DIR"
  install -d -o root -g bp -m 0755 "$STAGING_DIR"
  git -C "$REPO" archive "$SHA" | tar -x -C "$STAGING_DIR"
  chown -R root:bp "$STAGING_DIR"
  chmod -R a-w "$STAGING_DIR"
  chmod -R a+rX "$STAGING_DIR"
  mv "$STAGING_DIR" "$VERSION_DIR"
fi

ln -sfn "$VERSION_DIR" "$CURRENT_LINK"
install -o root -g root -m 0644   "$VERSION_DIR/deploy/$SERVICE_UNIT" "$SERVICE_PATH"
install -o root -g root -m 0644   "$VERSION_DIR/deploy/$TIMER_UNIT" "$TIMER_PATH"
systemctl daemon-reload

CYCLE_FILE=$(mktemp /var/tmp/bp-v4-forward-cycle.XXXXXX.json)
if ! sudo -u bp env     MODE=research     LIVE_TRADING_ENABLED=false     MAX_TRADE_SIZE_USD=0     MAX_DAILY_LOSS_USD=0     PYTHONPATH="$VERSION_DIR/src"     "$REPO/.venv/bin/python"     "$VERSION_DIR/scripts/run_v4_forward_coverage.py"     once --env-file "$ENV_FILE" > "$CYCLE_FILE"; then
  cat "$CYCLE_FILE" >&2 || true
  fail "initial_v4_forward_cycle_failed"
fi

"$REPO/.venv/bin/python" - "$CYCLE_FILE" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("epoch") != "2026-09-20T12:40:53+00:00":
    raise SystemExit("unexpected V4 prospective epoch")
for key in (
    "future_cutoff_violation_count",
    "polymarket_predictor_key_count",
    "regime_invariant_violation_count",
):
    if int(payload.get(key, -1)) != 0:
        raise SystemExit(f"{key} must remain zero")
if payload.get("policy_selected") is not False:
    raise SystemExit("policy_selected must remain false")
if payload.get("training_run") is not False:
    raise SystemExit("training_run must remain false")
if payload.get("automatic_promotion") is not False:
    raise SystemExit("automatic_promotion must remain false")
PY

# Prove the installed unit can run an idempotent follow-up cycle.
systemctl start "$SERVICE_UNIT"
systemctl enable --now "$TIMER_UNIT"
systemctl is-enabled --quiet "$TIMER_UNIT" || fail "collector_timer_not_enabled"
systemctl is-active --quiet "$TIMER_UNIT" || fail "collector_timer_not_active"

require_research_zero_money
require_services_active
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$OLD_DEPLOYED_HEAD" ]]   || fail "deployed_checkout_changed"

DISK_AFTER=$(mktemp /var/tmp/bp-v4-forward-disk-after.XXXXXX.json)
run_disk_health "$DISK_AFTER"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v4-forward-coverage-${SHA:0:12}-$STAMP.json"
"$REPO/.venv/bin/python" -   "$CYCLE_FILE" "$DISK_BEFORE" "$DISK_AFTER" "$EVIDENCE_PATH"   "$OLD_DEPLOYED_HEAD" "$SHA" "$VERSION_DIR" "$RECORDER_PID_BEFORE" "$RECORDER_PID_AFTER" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    cycle_path,
    before_path,
    after_path,
    output_path,
    deployed_head,
    candidate,
    runtime,
    recorder_pid_before,
    recorder_pid_after,
) = sys.argv[1:]
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "deployed_checkout_head_unchanged": deployed_head,
    "candidate_sha": candidate,
    "runtime_path": runtime,
    "feature_version": "core-v4-regime-aware",
    "prospective_epoch_start": "2026-09-20T12:40:53Z",
    "service_unit": "bp-v4-forward-coverage.service",
    "timer_unit": "bp-v4-forward-coverage.timer",
    "timer_enabled": True,
    "timer_active": True,
    "cycle": json.loads(Path(cycle_path).read_text(encoding="utf-8")),
    "disk_health_before": json.loads(Path(before_path).read_text(encoding="utf-8")).get("status"),
    "disk_health_after": json.loads(Path(after_path).read_text(encoding="utf-8")).get("status"),
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "training_run": False,
        "automatic_promotion": False,
        "model_activation": False,
    },
    "recorder_pid_before": int(recorder_pid_before),
    "recorder_pid_after": int(recorder_pid_after),
    "recorder_restarted": recorder_pid_before != recorder_pid_after,
    "production_checkout_changed": False,
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
chown bp:bp "$EVIDENCE_PATH"
chmod 0640 "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

echo "PHASE14_V4_FORWARD_ROLLOUT=PASS"
echo "DEPLOYED_CHECKOUT_HEAD=$OLD_DEPLOYED_HEAD"
echo "CANDIDATE_HEAD=$SHA"
echo "RUNTIME_PATH=$VERSION_DIR"
echo "SERVICE_UNIT=$SERVICE_UNIT"
echo "TIMER_UNIT=$TIMER_UNIT"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
cat "$CYCLE_FILE"
