#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_FORWARD_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_FORWARD_ZONE:-us-east1-c}"
VM="${PHASE14_V2_FORWARD_VM:-bp-recorder}"
BRANCH="${PHASE14_V2_FORWARD_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_V2_FORWARD_HEAD:-}"
EXPECTED_FROM_HEAD="${PHASE14_V2_FORWARD_FROM_HEAD:-d077e45f24704e6038c947169c84527e954de975}"
ENV_FILE="${PHASE14_V2_FORWARD_ENV_FILE:-/etc/bp/bp.env}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V2_FORWARD_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V2_FORWARD_FROM_HEAD must be the exact expected deployed SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_V2_FORWARD_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_V2_FORWARD_ENV_FILE must be an absolute path" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run this helper from Google Cloud Shell" >&2
  exit 2
fi
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

gcloud config set project "$PROJECT" >/dev/null

printf -v HEAD_Q '%q' "$EXPECTED_HEAD"
printf -v FROM_HEAD_Q '%q' "$EXPECTED_FROM_HEAD"
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

SHA="${PHASE14_V2_FORWARD_HEAD:?}"
EXPECTED_FROM_HEAD="${PHASE14_V2_FORWARD_FROM_HEAD:?}"
BRANCH="${PHASE14_V2_FORWARD_BRANCH:?}"
ENV_FILE="${PHASE14_V2_FORWARD_ENV_FILE:?}"
REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
SERVICE_UNIT=bp-v2-forward-coverage.service
TIMER_UNIT=bp-v2-forward-coverage.timer
SERVICE_PATH="/etc/systemd/system/$SERVICE_UNIT"
TIMER_PATH="/etc/systemd/system/$TIMER_UNIT"
EVIDENCE_DIR=/var/lib/bp/evidence/
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)

ROLLBACK_ARMED=0
OLD_HEAD=""
OLD_BRANCH=""
SERVICE_PREEXISTED=0
TIMER_PREEXISTED=0
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
SERVICE_BACKUP=""
TIMER_BACKUP=""
DISK_BEFORE=""
DISK_AFTER=""
COVERAGE_FILE=""
EVIDENCE_TMP=""

fail() {
  echo "PHASE14_V2_FORWARD_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

validate_deployed_checkout() {
  local entry
  local code
  local path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

validate_rollout_scope() {
  local path
  local source_seen=0
  local service_seen=0
  local timer_seen=0
  local helper_seen=0

  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      PROJECT_STATE.json|docs/*|.github/workflows/ci.yml)
        ;;
      src/bp_engine/features/v2_forward.py)
        source_seen=1
        ;;
      src/bp_engine/features/v2_forward_cli.py|scripts/run_v2_forward_coverage.py)
        ;;
      deploy/bp-v2-forward-coverage.service)
        service_seen=1
        ;;
      deploy/bp-v2-forward-coverage.timer)
        timer_seen=1
        ;;
      scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh)
        helper_seen=1
        ;;
      tests/features/test_v2_forward.py|tests/features/test_v2_forward_cli.py)
        ;;
      tests/deploy/test_phase14_v2_forward_coverage_deployment.py)
        ;;
      *)
        fail "unexpected_rollout_path:$path"
        ;;
    esac
  done < <(git -C "$REPO" diff --name-only "$OLD_HEAD" "$SHA")

  (( source_seen )) || fail "forward_collector_source_change_missing"
  (( service_seen )) || fail "forward_collector_service_change_missing"
  (( timer_seen )) || fail "forward_collector_timer_change_missing"
  (( helper_seen )) || fail "forward_collector_helper_change_missing"

  local frozen_path
  for frozen_path in \
    src/bp_engine/features/service.py \
    src/bp_engine/live_prediction \
    src/bp_engine/calibration \
    src/bp_engine/execution; do
    if ! git -C "$REPO" diff --quiet "$OLD_HEAD" "$SHA" -- "$frozen_path"; then
      fail "frozen_v1_path_changed:$frozen_path"
    fi
  done
}

require_research_zero_money() {
  local mode
  local live_trading_enabled
  local max_trade_size_usd
  local max_daily_loss_usd
  local safe_mode
  local safe_live
  local safe_trade
  local safe_loss

  [[ -f "$ENV_FILE" ]] || fail "environment_file_missing"
  [[ -f "$SAFETY_FILE" ]] || fail "prospective_safety_file_missing"

  mode=$(read_env "$ENV_FILE" MODE)
  live_trading_enabled=$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)
  max_trade_size_usd=$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)
  max_daily_loss_usd=$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)
  safe_mode=$(read_env "$SAFETY_FILE" MODE)
  safe_live=$(read_env "$SAFETY_FILE" LIVE_TRADING_ENABLED)
  safe_trade=$(read_env "$SAFETY_FILE" MAX_TRADE_SIZE_USD)
  safe_loss=$(read_env "$SAFETY_FILE" MAX_DAILY_LOSS_USD)

  if [[ "$mode" != "research" || \
        "$live_trading_enabled" != "false" || \
        "$max_trade_size_usd" != "0" || \
        "$max_daily_loss_usd" != "0" || \
        "$safe_mode" != "research" || \
        "$safe_live" != "false" || \
        "$safe_trade" != "0" || \
        "$safe_loss" != "0" ]]; then
    fail "research_zero_money_boundary_not_satisfied"
  fi
}

require_services_active() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
}

run_disk_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" \
      disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "disk_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(f"disk status is not ok: {payload.get('status')!r}")
PY
}

capture_unit_state() {
  if [[ -f "$SERVICE_PATH" ]]; then
    SERVICE_PREEXISTED=1
    SERVICE_BACKUP=$(mktemp /var/tmp/bp-v2-forward-service.XXXXXX)
    cp -a "$SERVICE_PATH" "$SERVICE_BACKUP"
  fi
  if [[ -f "$TIMER_PATH" ]]; then
    TIMER_PREEXISTED=1
    TIMER_BACKUP=$(mktemp /var/tmp/bp-v2-forward-timer.XXXXXX)
    cp -a "$TIMER_PATH" "$TIMER_BACKUP"
  fi
  if systemctl is-enabled --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ENABLED=1
  fi
  if systemctl is-active --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ACTIVE=1
  fi
}

restore_checkout() {
  [[ -n "$OLD_HEAD" ]] || return 0
  if [[ -n "$OLD_BRANCH" ]]; then
    git -C "$REPO" checkout --force "$OLD_BRANCH" >/dev/null 2>&1 || true
    git -C "$REPO" reset --hard "$OLD_HEAD" >/dev/null 2>&1 || true
  else
    git -C "$REPO" checkout --detach --force "$OLD_HEAD" >/dev/null 2>&1 || true
  fi
}

rollback() {
  set +e
  echo "PHASE14_V2_FORWARD_ROLLBACK=START" >&2
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
  systemctl daemon-reload >/dev/null 2>&1 || true

  restore_checkout

  if (( TIMER_WAS_ENABLED )); then
    systemctl enable "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi
  if (( TIMER_WAS_ACTIVE )); then
    systemctl start "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi

  local service
  for service in "${CORE_SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$service"; then
      echo "ROLLBACK_WARNING=service_not_active:$service" >&2
    fi
  done
  echo "PHASE14_V2_FORWARD_ROLLBACK=COMPLETE" >&2
}

cleanup() {
  rm -f "${SERVICE_BACKUP:-}" "${TIMER_BACKUP:-}" \
    "${DISK_BEFORE:-}" "${DISK_AFTER:-}" "${COVERAGE_FILE:-}" "${EVIDENCE_TMP:-}"
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
validate_deployed_checkout
require_research_zero_money
require_services_active

DISK_BEFORE=$(mktemp /var/tmp/bp-v2-forward-disk-before.XXXXXX.json)
run_disk_health "$DISK_BEFORE"

OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)
OLD_BRANCH=$(git -C "$REPO" symbolic-ref --quiet --short HEAD || true)
[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]] || fail "unexpected_deployed_head:$OLD_HEAD"

git -C "$REPO" fetch --quiet origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
REMOTE_HEAD=$(git -C "$REPO" rev-parse "origin/$BRANCH")
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "remote_branch_head_mismatch:$REMOTE_HEAD"
git -C "$REPO" cat-file -e "$SHA^{commit}" || fail "candidate_commit_missing"
git -C "$REPO" merge-base --is-ancestor "$OLD_HEAD" "$SHA" || fail "candidate_not_descendant"
validate_rollout_scope

for required_path in \
  deploy/bp-v2-forward-coverage.service \
  deploy/bp-v2-forward-coverage.timer \
  scripts/run_v2_forward_coverage.py \
  src/bp_engine/features/v2_forward.py \
  src/bp_engine/features/v2_forward_cli.py; do
  git -C "$REPO" cat-file -e "$SHA:$required_path" || fail "candidate_path_missing:$required_path"
done

capture_unit_state
ROLLBACK_ARMED=1

git -C "$REPO" checkout --detach --force "$SHA" >/dev/null
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "candidate_checkout_failed"

install -o root -g root -m 0644 "$REPO/deploy/$SERVICE_UNIT" "$SERVICE_PATH"
install -o root -g root -m 0644 "$REPO/deploy/$TIMER_UNIT" "$TIMER_PATH"
systemctl daemon-reload

# The manual systemd cycle proves the installed unit can execute successfully.
systemctl start "$SERVICE_UNIT"

# A second bounded idempotent cycle captures deterministic JSON for invariant checks.
COVERAGE_FILE=$(mktemp /var/tmp/bp-v2-forward-coverage.XXXXXX.json)
if ! sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$REPO/src" \
    "$REPO/.venv/bin/python" "$REPO/scripts/run_v2_forward_coverage.py" \
    once --env-file "$ENV_FILE" > "$COVERAGE_FILE"; then
  cat "$COVERAGE_FILE" >&2 || true
  fail "bounded_manual_coverage_cycle_failed"
fi

"$REPO/.venv/bin/python" - "$COVERAGE_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if int(payload.get("future_cutoff_violation_count", -1)) != 0:
    raise SystemExit("future_cutoff_violation_count must remain zero")
if payload.get("policy_selected") is not False:
    raise SystemExit("policy_selected must remain false")
if payload.get("automatic_promotion") is not False:
    raise SystemExit("automatic_promotion must remain false")
if int(payload.get("coverage_row_count", 0)) < 4:
    raise SystemExit("coverage must preserve at least the proven Gate A rows")
if int(payload.get("coverage_market_count", 0)) < 1:
    raise SystemExit("coverage must preserve at least the proven Gate A market")
PY

systemctl enable --now "$TIMER_UNIT"
systemctl is-enabled --quiet "$TIMER_UNIT" || fail "collector_timer_not_enabled"
systemctl is-active --quiet "$TIMER_UNIT" || fail "collector_timer_not_active"

require_research_zero_money
require_services_active
DISK_AFTER=$(mktemp /var/tmp/bp-v2-forward-disk-after.XXXXXX.json)
run_disk_health "$DISK_AFTER"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_changed_post_rollout"

install -d -o bp -g bp -m 0750 "${EVIDENCE_DIR%/}"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-v2-forward-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" - \
  "$COVERAGE_FILE" "$DISK_BEFORE" "$DISK_AFTER" "$EVIDENCE_TMP" \
  "$OLD_HEAD" "$SHA" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

coverage_path, before_path, after_path, output_path, old_head, new_head = sys.argv[1:]
coverage = json.loads(Path(coverage_path).read_text(encoding="utf-8"))
disk_before = json.loads(Path(before_path).read_text(encoding="utf-8"))
disk_after = json.loads(Path(after_path).read_text(encoding="utf-8"))
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "deployed_from_sha": old_head,
    "candidate_sha": new_head,
    "service_unit": "bp-v2-forward-coverage.service",
    "timer_unit": "bp-v2-forward-coverage.timer",
    "timer_enabled": True,
    "timer_active": True,
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
    },
    "core_services_verified_active": [
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
    ],
    "disk_health_before": disk_before.get("status"),
    "disk_health_after": disk_after.get("status"),
    "coverage": coverage,
    "immutable_market_features_preserved": True,
    "policy_selected": False,
    "automatic_promotion": False,
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="${EVIDENCE_DIR}phase14-v2-forward-coverage-${SHA:0:12}-${STAMP}.json"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

echo "PHASE14_V2_FORWARD_ROLLOUT=PASS"
echo "FROM_HEAD=$OLD_HEAD"
echo "HEAD=$SHA"
echo "SERVICE_UNIT=$SERVICE_UNIT"
echo "TIMER_UNIT=$TIMER_UNIT"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
REMOTE

gcloud compute ssh "$VM" \
  --zone "$ZONE" \
  --command "sudo env PHASE14_V2_FORWARD_HEAD=$HEAD_Q PHASE14_V2_FORWARD_FROM_HEAD=$FROM_HEAD_Q PHASE14_V2_FORWARD_BRANCH=$BRANCH_Q PHASE14_V2_FORWARD_ENV_FILE=$ENV_FILE_Q bash -s" \
  <<< "$REMOTE_SCRIPT"
