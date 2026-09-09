#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_B_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_GATE_B_ZONE:-us-east1-c}"
VM="${PHASE14_V2_GATE_B_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V2_GATE_B_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_DEPLOYED_HEAD:-}"
ENV_FILE="${PHASE14_V2_GATE_B_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_STORAGE_EVIDENCE:-}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_STORAGE_EVIDENCE_SHA256:-}"

fail_local() {
  echo "PHASE14_V2_GATE_B_RESEARCH=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"
[[ "$ENV_FILE" == /* ]] || fail_local "env_file_must_be_absolute"
[[ "$STORAGE_EVIDENCE" == /* ]] || fail_local "storage_evidence_path_must_be_absolute"
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail_local "storage_evidence_sha256_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"

REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

ARCHIVE=$(mktemp /tmp/bp-v2-gate-b-candidate.XXXXXX.tar.gz)
cleanup_local() {
  rm -f "$ARCHIVE"
}
trap cleanup_local EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$HELPER_HEAD"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-v2-gate-b-${HELPER_HEAD}.tar.gz"

gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v REMOTE_ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA256_Q '%q' "$ARCHIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_V2_GATE_B_HELPER_HEAD:?}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_DEPLOYED_HEAD:?}"
ENV_FILE="${PHASE14_V2_GATE_B_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_STORAGE_EVIDENCE_SHA256:?}"
ARCHIVE="${PHASE14_V2_GATE_B_ARCHIVE:?}"
ARCHIVE_SHA256="${PHASE14_V2_GATE_B_ARCHIVE_SHA256:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
RECORDER_UNIT=bp-recorder.service
V2_TIMER=bp-v2-forward-coverage.timer
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
RUNTIME_ROOT=""
RUN_DIR=""
DISK_BEFORE=""
DISK_AFTER=""

fail() {
  local artifact path upper
  echo "PHASE14_V2_GATE_B_RESEARCH=FAIL" >&2
  echo "REASON=$1" >&2
  if [[ -n "$RUN_DIR" ]]; then
    echo "PARTIAL_EVIDENCE_DIR=$RUN_DIR" >&2
    for artifact in plan selection holdout summary; do
      path="$RUN_DIR/$artifact.json"
      upper=${artifact^^}
      if [[ -f "$path" ]]; then
        echo "${upper}_PRESENT=true" >&2
      else
        echo "${upper}_PRESENT=false" >&2
      fi
    done
    if [[ -f "$RUN_DIR/holdout.json" ]]; then
      echo "HOLDOUT_TOUCHED=true" >&2
    else
      echo "HOLDOUT_TOUCHED=false" >&2
    fi
  fi
  exit 1
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  [[ -n "$RUNTIME_ROOT" ]] && rm -rf "$RUNTIME_ROOT"
  rm -f "$ARCHIVE"
  [[ -n "$DISK_BEFORE" ]] && rm -f "$DISK_BEFORE"
  [[ -n "$DISK_AFTER" ]] && rm -f "$DISK_AFTER"
  set -e
  exit "$rc"
}
trap cleanup EXIT

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode=$(read_env "$path" MODE)
    live=$(read_env "$path" LIVE_TRADING_ENABLED)
    trade=$(read_env "$path" MAX_TRADE_SIZE_USD)
    loss=$(read_env "$path" MAX_DAILY_LOSS_USD)
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
  done
}

validate_deployed_checkout() {
  local entry code path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    fi
  done < <(git -c safe.directory="$REPO" -C "$REPO" status --porcelain --untracked-files=all)
}

read_recorder_config_workers() {
  sudo -u bp env -u RECORDER_WRITER_WORKERS "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import sys

from bp_engine.config import Settings

print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PY
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" \
      disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("storage health status is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not satisfied: {name}")
PY
}

require_services() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-active --quiet "$MAINTENANCE_TIMER" || fail "maintenance_timer_not_active"
  systemctl is-active --quiet "$DISK_HEALTH_TIMER" || fail "disk_health_timer_not_active"
  systemctl is-active --quiet "$V2_TIMER" || fail "v2_timer_not_active"
  systemctl is-enabled --quiet "$V2_TIMER" || fail "v2_timer_not_enabled"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
chmod 0644 "$ARCHIVE"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"

validate_deployed_checkout
require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"

DISK_BEFORE=$(mktemp /var/tmp/bp-v2-gate-b-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR="$EVIDENCE_DIR/phase14-v2-gate-b-$STAMP"
[[ ! -e "$RUN_DIR" ]] || fail "evidence_run_dir_exists"
install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR" "$RUN_DIR"

RUNTIME_ROOT="/var/tmp/bp-v2-gate-b-${HELPER_HEAD:0:12}-$$"
install -d -o bp -g bp -m 0750 "$RUNTIME_ROOT"
sudo -u bp tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"

[[ -f "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" ]] || fail "candidate_gate_b_runner_missing"
[[ -f "$RUNTIME_ROOT/src/bp_engine/v2_research/service.py" ]] || fail "candidate_gate_b_service_missing"

PLAN="$RUN_DIR/plan.json"
SELECTION="$RUN_DIR/selection.json"
HOLDOUT="$RUN_DIR/holdout.json"
SUMMARY="$RUN_DIR/summary.json"

run_candidate() {
  sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$RUNTIME_ROOT/src" \
    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" \
    --env-file "$ENV_FILE" "$@"
}

run_candidate plan --output "$PLAN" >/dev/null || fail "gate_b_plan_failed"
"$REPO/.venv/bin/python" - "$PLAN" <<'PY' || fail "gate_b_plan_verification_failed"
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("labels_read") is not False:
    raise SystemExit("plan read labels")
if payload.get("coverage_input_sha256") != "aab75574aa7faf18e65358353403e5ec1a2b89dd42424eb7b0e3329bf683b099":
    raise SystemExit("coverage preregistration hash changed")
if payload.get("freshness_candidates_seconds") != [1, 2, 5, 10]:
    raise SystemExit("freshness candidate grid changed")
if len(payload.get("folds") or []) < 3:
    raise SystemExit("fewer than three eligible ordinary folds")
if not (payload.get("final") or {}).get("holdout_condition_ids"):
    raise SystemExit("final holdout is empty")
PY

run_candidate prepare --plan "$PLAN" --output "$SELECTION" >/dev/null || fail "gate_b_prepare_failed"
"$REPO/.venv/bin/python" - "$SELECTION" <<'PY' || fail "gate_b_prepare_verification_failed"
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("stage") != "prepared_validation_frozen":
    raise SystemExit("selection stage is not frozen")
if payload.get("holdout_labels_read") is not False:
    raise SystemExit("prepare read final-holdout labels")
if payload.get("holdout_evaluated") is not False:
    raise SystemExit("prepare evaluated final holdout")
if payload.get("gate_b_authorized") is not False:
    raise SystemExit("prepare authorized Gate B")
if payload.get("automatic_promotion") is not False:
    raise SystemExit("prepare enabled automatic promotion")
PY

run_candidate evaluate-holdout \
  --plan "$PLAN" \
  --selection "$SELECTION" \
  --output "$HOLDOUT" >/dev/null || fail "gate_b_holdout_failed"
"$REPO/.venv/bin/python" - "$HOLDOUT" <<'PY' || fail "gate_b_holdout_verification_failed"
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("stage") != "final_holdout_evaluated":
    raise SystemExit("holdout stage is not final")
if payload.get("holdout_labels_read") is not True:
    raise SystemExit("holdout labels were not read")
if payload.get("holdout_evaluated_once") is not True:
    raise SystemExit("holdout one-shot marker missing")
if payload.get("gate_b_authorized") is not False:
    raise SystemExit("holdout artifact authorized Gate B")
if payload.get("automatic_promotion") is not False:
    raise SystemExit("holdout artifact enabled automatic promotion")
PY

require_research_zero_money
require_services
[[ "$(read_recorder_config_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"

DISK_AFTER=$(mktemp /var/tmp/bp-v2-gate-b-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

"$REPO/.venv/bin/python" - \
  "$PLAN" "$SELECTION" "$HOLDOUT" "$DISK_BEFORE" "$DISK_AFTER" "$SUMMARY" \
  "$HELPER_HEAD" "$DEPLOYED_HEAD" "$STORAGE_EVIDENCE" "$STORAGE_EVIDENCE_SHA256" \
  "$ARCHIVE_SHA256" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    plan_path,
    selection_path,
    holdout_path,
    before_path,
    after_path,
    output_path,
    helper_head,
    deployed_head,
    storage_evidence,
    storage_evidence_sha256,
    archive_sha256,
) = sys.argv[1:]

def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

plan = load(plan_path)
selection = load(selection_path)
holdout = load(holdout_path)
final_selection = selection["final"]["selection"]
holdout_eval = holdout["holdout_evaluation"]

selected = final_selection.get("selected") if final_selection.get("policy") == "trade_threshold" else None
selection_summary = {
    "policy": final_selection.get("policy"),
    "reason": final_selection.get("reason"),
    "offset_seconds": selected.get("offset_seconds") if selected else None,
    "max_last_trade_age_seconds": (
        selected.get("max_last_trade_age_seconds") if selected else None
    ),
    "calibration_method": (
        (selected.get("calibration") or {}).get("method") if selected else None
    ),
    "min_edge": (
        (selected.get("edge") or {}).get("min_edge") if selected else None
    ),
}

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "deployed_head": deployed_head,
    "candidate_archive_sha256": archive_sha256,
    "storage_evidence": storage_evidence,
    "storage_evidence_sha256": storage_evidence_sha256,
    "database_access": "read_only_transaction_each_stage",
    "production_checkout_mutated": False,
    "production_database_mutated_by_gate_b": False,
    "plan": {
        "artifact": plan_path,
        "sha256": sha(plan_path),
        "plan_sha256": plan["plan_sha256"],
        "market_count": plan["market_count"],
        "fold_count": len(plan["folds"]),
        "final_holdout_market_count": len(plan["final"]["holdout_condition_ids"]),
        "labels_read": False,
    },
    "selection": {
        "artifact": selection_path,
        "sha256": sha(selection_path),
        "selection_sha256": selection["selection_sha256"],
        "holdout_labels_read": False,
        "summary": selection_summary,
    },
    "holdout": {
        "artifact": holdout_path,
        "sha256": sha(holdout_path),
        "holdout_evidence_sha256": holdout["holdout_evidence_sha256"],
        "market_count": len(holdout["holdout_condition_ids"]),
        "evaluation": holdout_eval,
    },
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "gate_b_authorized": False,
        "automatic_promotion": False,
    },
    "storage_before": load(before_path),
    "storage_after": load(after_path),
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
chown bp:bp "$SUMMARY"
chmod 0640 "$SUMMARY"

echo "PHASE14_V2_GATE_B_RESEARCH=PASS"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PLAN_FILE=$PLAN"
echo "SELECTION_FILE=$SELECTION"
echo "HOLDOUT_FILE=$HOLDOUT"
echo "SUMMARY_FILE=$SUMMARY"
"$REPO/.venv/bin/python" - "$SUMMARY" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
plan = payload["plan"]
selection = payload["selection"]["summary"]
holdout = payload["holdout"]["evaluation"]
edge = holdout.get("edge_metrics") or {}
print(f"MARKETS={plan['market_count']}")
print(f"ORDINARY_FOLDS={plan['fold_count']}")
print(f"FINAL_HOLDOUT_MARKETS={plan['final_holdout_market_count']}")
print(f"FINAL_POLICY={selection['policy']}")
print(f"SELECTED_OFFSET_SECONDS={selection['offset_seconds']}")
print(f"SELECTED_MAX_LAST_TRADE_AGE_SECONDS={selection['max_last_trade_age_seconds']}")
print(f"SELECTED_CALIBRATION={selection['calibration_method']}")
print(f"SELECTED_MIN_EDGE={selection['min_edge']}")
print(f"HOLDOUT_TRADE_COUNT={edge.get('trade_count', 0)}")
print(f"HOLDOUT_AFTER_COST_PNL={edge.get('realized_pnl_after_assumed_costs', 0.0)}")
print("GATE_B_AUTHORIZED=false")
print("AUTOMATIC_PROMOTION=false")
PY
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "CANDIDATE_ARCHIVE_SHA256=$ARCHIVE_SHA256"
echo "Running read-only Phase 14 V2 Gate B research package."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_B_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_GATE_B_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_GATE_B_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_ARCHIVE=$REMOTE_ARCHIVE_Q PHASE14_V2_GATE_B_ARCHIVE_SHA256=$ARCHIVE_SHA256_Q bash"
