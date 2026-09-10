#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_B_RESUME_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
VM="${PHASE14_V2_GATE_B_RESUME_VM:-bp-recorder}"
ZONE="${PHASE14_V2_GATE_B_RESUME_ZONE:-us-east1-c}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_RESUME_DEPLOYED_HEAD:-e9c7afc1536880e4612cb6e3d1a7282fa37c69f5}"
ENV_FILE="${PHASE14_V2_GATE_B_RESUME_ENV_FILE:-/etc/bp/bp.env}"
PARTIAL_DIR="${PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR:?set exact failed Gate B evidence directory}"
PLAN_SHA256="${PHASE14_V2_GATE_B_RESUME_PLAN_SHA256:?set exact frozen plan.json SHA-256}"
APPROVAL="${PHASE14_V2_GATE_B_RESUME_APPROVAL:?set explicit SHA-bound resume approval}"

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"
git fetch --quiet origin main
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git rev-parse origin/main)
test "$LOCAL_HEAD" = "$REMOTE_MAIN"
test -z "$(git status --porcelain)"

if [[ ! "$PLAN_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "invalid frozen plan SHA-256" >&2
  exit 2
fi
case "$PARTIAL_DIR" in
  /var/lib/bp/evidence/phase14-v2-gate-b-*) ;;
  *) echo "partial directory is outside the canonical Gate B evidence root" >&2; exit 2 ;;
esac
EXPECTED_APPROVAL="I_APPROVE_PHASE14_V2_GATE_B_RESUME:${LOCAL_HEAD}:${PLAN_SHA256}"
test "$APPROVAL" = "$EXPECTED_APPROVAL"

gcloud config set project "$PROJECT" >/dev/null
gcloud auth print-access-token >/dev/null

ARCHIVE=$(mktemp "$HOME/bp-v2-gate-b-resume-${LOCAL_HEAD:0:12}.XXXXXX.tar.gz")
trap 'rm -f "$ARCHIVE"' EXIT
git archive --format=tar.gz -o "$ARCHIVE" "$LOCAL_HEAD"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-v2-gate-b-resume-${LOCAL_HEAD}.tar.gz"

gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet

printf -v HELPER_HEAD_Q '%q' "$LOCAL_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v PARTIAL_DIR_Q '%q' "$PARTIAL_DIR"
printf -v PLAN_SHA256_Q '%q' "$PLAN_SHA256"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA256_Q '%q' "$ARCHIVE_SHA256"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

HELPER_HEAD=${PHASE14_V2_GATE_B_RESUME_HELPER_HEAD:?}
DEPLOYED_HEAD=${PHASE14_V2_GATE_B_RESUME_DEPLOYED_HEAD:?}
ENV_FILE=${PHASE14_V2_GATE_B_RESUME_ENV_FILE:?}
PARTIAL_DIR=${PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR:?}
PLAN_SHA256=${PHASE14_V2_GATE_B_RESUME_PLAN_SHA256:?}
ARCHIVE=${PHASE14_V2_GATE_B_RESUME_ARCHIVE:?}
ARCHIVE_SHA256=${PHASE14_V2_GATE_B_RESUME_ARCHIVE_SHA256:?}
REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)

fail() {
  echo "PHASE14_V2_GATE_B_RESUME=FAIL" >&2
  echo "REASON=$1" >&2
  if [[ -f "$PARTIAL_DIR/holdout.json" ]]; then
    echo "HOLDOUT_TOUCHED=true" >&2
  else
    echo "HOLDOUT_TOUCHED=false" >&2
  fi
  exit 1
}

case "$PARTIAL_DIR" in
  /var/lib/bp/evidence/phase14-v2-gate-b-*) ;;
  *) fail "invalid_partial_dir" ;;
esac
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -r "$SAFETY_FILE" ]] || fail "runtime_safety_file_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"
[[ -d "$PARTIAL_DIR" ]] || fail "partial_dir_missing"
test -f "$PARTIAL_DIR/plan.json" || fail "frozen_plan_missing"
test ! -e "$PARTIAL_DIR/selection.json" || fail "selection_already_present"
test ! -e "$PARTIAL_DIR/holdout.json" || fail "holdout_already_present"
test ! -e "$PARTIAL_DIR/summary.json" || fail "summary_already_present"
[[ "$(sha256sum "$PARTIAL_DIR/plan.json" | awk '{print $1}')" == "$PLAN_SHA256" ]] || fail "frozen_plan_sha256_mismatch"

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}
require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
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
require_services() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-active --quiet bp-storage-maintenance.timer || fail "maintenance_timer_not_active"
  systemctl is-active --quiet bp-storage-disk-health.timer || fail "disk_health_timer_not_active"
  systemctl is-active --quiet bp-v2-forward-coverage.timer || fail "v2_timer_not_active"
}
require_research_zero_money
require_services

RUNTIME_ROOT=$(mktemp -d /var/tmp/bp-v2-gate-b-resume.XXXXXX)
AUDIT=$(mktemp /var/tmp/bp-v2-gate-b-resume-audit.XXXXXX.json)
cleanup() {
  rm -rf "$RUNTIME_ROOT" "$AUDIT" "$ARCHIVE"
}
trap cleanup EXIT
chmod 0755 "$RUNTIME_ROOT"
tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"
[[ -f "$RUNTIME_ROOT/scripts/run_v2_gate_b_label_recovery.py" ]] || fail "candidate_label_audit_runner_missing"
[[ -f "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" ]] || fail "candidate_gate_b_runner_missing"

run_label_audit() {
  sudo -u bp env \
    MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$RUNTIME_ROOT/src" \
    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_label_recovery.py" \
    audit --env-file "$ENV_FILE" --plan "$PARTIAL_DIR/plan.json"
}
run_research() {
  sudo -u bp env \
    MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 \
    PYTHONPATH="$RUNTIME_ROOT/src" \
    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" \
    --env-file "$ENV_FILE" "$@"
}

run_label_audit audit > "$AUDIT" 2>/dev/null || run_label_audit > "$AUDIT" || fail "non_holdout_label_audit_failed"
"$REPO/.venv/bin/python" - "$AUDIT" <<'PY' || fail "non_holdout_labels_not_ready"
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("holdout_touched") is not False:
    raise SystemExit("holdout touched during label audit")
if payload.get("missing_label_count") != 0:
    raise SystemExit("canonical non-holdout labels still missing")
PY

PLAN="$PARTIAL_DIR/plan.json"
SELECTION="$PARTIAL_DIR/selection.json"
HOLDOUT="$PARTIAL_DIR/holdout.json"
SUMMARY="$PARTIAL_DIR/summary.json"

run_research prepare --plan "$PLAN" --output "$SELECTION" >/dev/null || fail "gate_b_prepare_failed"
"$REPO/.venv/bin/python" - "$SELECTION" <<'PY' || fail "gate_b_prepare_verification_failed"
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("stage") != "prepared_validation_frozen":
    raise SystemExit("selection is not frozen")
if payload.get("holdout_labels_read") is not False or payload.get("holdout_evaluated") is not False:
    raise SystemExit("prepare touched final holdout")
if payload.get("gate_b_authorized") is not False or payload.get("automatic_promotion") is not False:
    raise SystemExit("prepare crossed authorization boundary")
PY

test ! -e "$HOLDOUT" || fail "holdout_preexists_before_evaluation"
run_research evaluate-holdout --plan "$PLAN" --selection "$SELECTION" --output "$HOLDOUT" >/dev/null || fail "gate_b_holdout_failed"
"$REPO/.venv/bin/python" - "$HOLDOUT" <<'PY' || fail "gate_b_holdout_verification_failed"
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("stage") != "final_holdout_evaluated":
    raise SystemExit("holdout stage is not final")
if payload.get("holdout_labels_read") is not True or payload.get("holdout_evaluated_once") is not True:
    raise SystemExit("one-shot holdout markers missing")
if payload.get("gate_b_authorized") is not False or payload.get("automatic_promotion") is not False:
    raise SystemExit("holdout artifact crossed promotion boundary")
PY

require_research_zero_money
require_services
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"

"$REPO/.venv/bin/python" - "$PLAN" "$SELECTION" "$HOLDOUT" "$SUMMARY" "$HELPER_HEAD" "$DEPLOYED_HEAD" "$PLAN_SHA256" <<'PY'
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
plan_path, selection_path, holdout_path, output_path, helper_head, deployed_head, frozen_plan_file_sha = sys.argv[1:]
def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
plan = load(plan_path)
selection = load(selection_path)
holdout = load(holdout_path)
final_selection = selection["final"]["selection"]
selected = final_selection.get("selected") if final_selection.get("policy") == "trade_threshold" else None
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "resumed_from_frozen_plan": True,
    "helper_head": helper_head,
    "deployed_head": deployed_head,
    "database_access": "read_only_gate_b_stages",
    "production_checkout_mutated": False,
    "production_database_mutated_by_resume": False,
    "plan": {
        "artifact": plan_path,
        "sha256": sha(plan_path),
        "authorized_file_sha256": frozen_plan_file_sha,
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
        "policy": final_selection.get("policy"),
        "offset_seconds": selected.get("offset_seconds") if selected else None,
        "max_last_trade_age_seconds": selected.get("max_last_trade_age_seconds") if selected else None,
        "min_edge": ((selected.get("edge") or {}).get("min_edge") if selected else None),
    },
    "holdout": {
        "artifact": holdout_path,
        "sha256": sha(holdout_path),
        "holdout_evidence_sha256": holdout["holdout_evidence_sha256"],
        "market_count": len(holdout["holdout_condition_ids"]),
        "evaluation": holdout["holdout_evaluation"],
    },
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "gate_b_authorized": False,
        "automatic_promotion": False,
    },
}
Path(output_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chown bp:bp "$SUMMARY"
chmod 0640 "$SUMMARY"

echo "PHASE14_V2_GATE_B_RESUME=PASS"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"
echo "PLAN_FILE=$PLAN"
echo "PLAN_SHA256=$PLAN_SHA256"
echo "SELECTION_FILE=$SELECTION"
echo "HOLDOUT_FILE=$HOLDOUT"
echo "SUMMARY_FILE=$SUMMARY"
echo "HOLDOUT_TOUCHED=true"
echo "GATE_B_AUTHORIZED=false"
echo "AUTOMATIC_PROMOTION=false"
REMOTE

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$LOCAL_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PARTIAL_EVIDENCE_DIR=$PARTIAL_DIR"
echo "PLAN_SHA256=$PLAN_SHA256"
echo "CANDIDATE_ARCHIVE_SHA256=$ARCHIVE_SHA256"

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_B_RESUME_HELPER_HEAD=$HELPER_HEAD_Q PHASE14_V2_GATE_B_RESUME_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_GATE_B_RESUME_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_RESUME_PARTIAL_DIR=$PARTIAL_DIR_Q PHASE14_V2_GATE_B_RESUME_PLAN_SHA256=$PLAN_SHA256_Q PHASE14_V2_GATE_B_RESUME_ARCHIVE=$ARCHIVE_Q PHASE14_V2_GATE_B_RESUME_ARCHIVE_SHA256=$ARCHIVE_SHA256_Q bash"
