#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"
RESULT_FILE="${PHASE15_CANARY_RESULT_FILE:-/tmp/bp-phase15-v3-canary-result.json}"

fail() {
  echo "PHASE15_V3_CANARY_INTERACTIVE_OPERATOR=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
[[ -r /dev/tty && -w /dev/tty ]] || fail "interactive_terminal_required"

START_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_prepare_watch_start_cloudshell.sh"
STATUS_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_prepare_watch_status_cloudshell.sh"
FOLLOW_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_prepare_watch_follow_cloudshell.sh"
ARM_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_arm_cloudshell.sh"
RECORD_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_record_cloudshell.sh"
RECONCILE_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh"

for helper in   "$START_HELPER"   "$STATUS_HELPER"   "$FOLLOW_HELPER"   "$ARM_HELPER"   "$RECORD_HELPER"   "$RECONCILE_HELPER"
do
  [[ -f "$helper" ]] || fail "required_helper_missing"
done

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' || fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path

state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate=state["phase_15_v3_live_canary"]
assert state["source_of_truth_version"] == "0.14.180"
assert gate["phase15_canary_authorized"] is True
assert gate["manual_real_money_submission_required"] is True
assert gate["automated_real_money_submission"] is False
assert gate["strategy_target_notional_usd"] == 5
assert gate["max_trade_size_usd"] == 10
assert gate["max_total_exposure_usd"] == 10
assert gate["max_daily_loss_usd"] == 10
assert gate["max_submission_attempts"] == 1
assert gate["canary_order_submitted"] is False
assert gate["second_order_authorized"] is False
PY

confirm_exact() {
  local expected="$1"
  local prompt="$2"
  local reply
  printf '%s\n' "$prompt" >/dev/tty
  IFS= read -r reply </dev/tty || fail "operator_input_closed"
  [[ "$reply" == "$expected" ]] || fail "operator_declined_${expected,,}"
}

reengage_kill_switch() {
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command="sudo sh -c 'printf %s\\n interactive-operator-safe-stop > /etc/bp-canary/KILL; chmod 0600 /etc/bp-canary/KILL'"     >/dev/null 2>&1 || true
}

ARMED=false
cleanup() {
  if [[ "$ARMED" == "true" ]]; then
    reengage_kill_switch
  fi
}
trap cleanup EXIT

recover_current_prepared_for_reconciliation() {
  local prepared_b64
  prepared_b64=$(
    gcloud compute ssh "$US_VM"       --project="$PROJECT"       --zone="$US_ZONE"       --quiet       --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
STATE_ROOT=/var/lib/bp/phase15-canary-prepare-watch
CURRENT_RUN=$STATE_ROOT/current-run
[[ -r "$CURRENT_RUN" ]]
RUN_DIR=$(cat "$CURRENT_RUN")
[[ "$RUN_DIR" == "$STATE_ROOT"/runs/* ]]
[[ -r "$RUN_DIR/prepared.json" ]]
base64 -w0 "$RUN_DIR/prepared.json"
REMOTE
  ) || fail "stale_prepared_payload_recovery_failed"

  umask 077
  printf '%s' "$prepared_b64" | base64 -d > "$PREPARED_FILE"
  chmod 0600 "$PREPARED_FILE"

  python3 - "$PREPARED_FILE" <<'PY' || fail "stale_prepared_payload_invalid"
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["status"] == "prepared"
assert payload["action"] == "submit"
assert payload["policy"]["policy_version"] == "v3-live-canary-v1"
assert int(payload["policy"]["max_submission_attempts"]) == 1
assert Decimal(str(payload["request"]["target_notional_usd"])) == Decimal("5")
market_end=datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
assert (market_end-datetime.now(UTC)).total_seconds() < 20
print("STALE_PREPARED_PAYLOAD_VALID=true")
print("INTENT_ID="+str(payload["intent_id"]))
PY
}

status_output=''
status_rc=0
set +e
status_output=$(bash "$STATUS_HELPER" 2>&1)
status_rc=$?
set -e
printf '%s\n' "$status_output"

candidate_ready=false
watcher_running=false

if grep -q '^PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=PASS$' <<<"$status_output"; then
  candidate_ready=true
elif grep -q '^PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=RUNNING$' <<<"$status_output"; then
  watcher_running=true
elif grep -Eq '^PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=(PREPARED_BUT_STALE|FAILED_AFTER_INTENT)$' <<<"$status_output"; then
  recover_current_prepared_for_reconciliation
  stale_intent=$(python3 - "$PREPARED_FILE" <<'PY'
import json,sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["intent_id"])
PY
)
  confirm_exact "RECONCILE"     "Stale unsubmitted intent $stale_intent blocks a new watcher. Type RECONCILE to close it before submission:"
  PHASE15_ACCEPT_UNSUBMITTED_RECONCILIATION=yes     bash "$RECONCILE_HELPER"
  rm -f "$PREPARED_FILE"
else
  if [[ "$status_rc" -ne 0 ]]; then
    echo "No reusable active/fresh watcher state was found; a new prepare-only watcher can be started."
  fi
fi

if [[ "$candidate_ready" != "true" && "$watcher_running" != "true" ]]; then
  confirm_exact "START"     "Type START to launch one bounded prepare-only watcher (no arm and no submission):"
  PHASE15_ACCEPT_PERSISTENT_PREPARE_WATCH=yes     PHASE15_CANARY_MAX_WAIT_SECONDS="${PHASE15_CANARY_MAX_WAIT_SECONDS:-7200}"     bash "$START_HELPER"
  watcher_running=true
fi

if [[ "$candidate_ready" != "true" ]]; then
  [[ "$watcher_running" == "true" ]] || fail "watcher_not_running"
  echo "Waiting for one fresh frozen-V3 candidate..."
  bash "$FOLLOW_HELPER"
fi

[[ -f "$PREPARED_FILE" ]] || fail "prepared_file_missing_after_follow"

readarray -t candidate < <(
python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
request=payload["request"]
assert payload["status"] == "prepared"
assert payload["action"] == "submit"
assert payload["policy"]["policy_version"] == "v3-live-canary-v1"
assert int(payload["policy"]["max_submission_attempts"]) == 1
assert Decimal(str(request["target_notional_usd"])) == Decimal("5")
market_end=datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
remaining=(market_end-datetime.now(UTC)).total_seconds()
assert remaining >= 20
print(payload["intent_id"])
print(payload.get("selected_side") or "")
print(request["limit_price"])
print(request["requested_shares"])
print(f"{remaining:.6f}")
PY
) || fail "fresh_prepared_payload_invalid"

INTENT_ID="${candidate[0]}"
SELECTED_SIDE="${candidate[1]}"
LIMIT_PRICE="${candidate[2]}"
REQUESTED_SHARES="${candidate[3]}"
SECONDS_REMAINING="${candidate[4]}"

echo "CANDIDATE_INTENT_ID=$INTENT_ID"
echo "CANDIDATE_SIDE=$SELECTED_SIDE"
echo "CANDIDATE_LIMIT_PRICE=$LIMIT_PRICE"
echo "CANDIDATE_REQUESTED_SHARES=$REQUESTED_SHARES"
echo "CANDIDATE_TARGET_NOTIONAL_USD=5"
echo "CANDIDATE_SECONDS_TO_MARKET_END=$SECONDS_REMAINING"

confirm_exact "ARM"   "Type ARM to arm exactly the displayed $5 intent. Arming submits no order:"

arm_output=$(mktemp)
if ! PHASE15_ACCEPT_REAL_MONEY=yes bash "$ARM_HELPER" | tee "$arm_output"; then
  rm -f "$arm_output"
  fail "arm_failed_no_submission_attempted"
fi
grep -qx 'REAL_ORDER_SUBMITTED=false' "$arm_output" ||
  { rm -f "$arm_output"; fail "arm_result_missing_no_order_proof"; }
grep -qx 'PHASE15_V3_CANARY_ARM=PASS' "$arm_output" ||
  { rm -f "$arm_output"; fail "arm_result_not_pass"; }
rm -f "$arm_output"
ARMED=true

python3 - "$PREPARED_FILE" "$INTENT_ID" <<'PY' || fail "armed_payload_binding_invalid"
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["intent_id"] == sys.argv[2]
assert str(payload.get("authorization_id") or "").startswith("phase15-v3-canary-")
market_end=datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
assert (market_end-datetime.now(UTC)).total_seconds() >= 10
PY

confirm_exact "SUBMIT"   "ARMED. Type SUBMIT to make the single authorized network submission for this exact intent. Anything else aborts and re-engages the kill switch:"

ATTEMPT_MARKER="/tmp/bp-phase15-v3-canary-submission-attempted-${INTENT_ID}"
if ! (set -o noclobber; printf '%s\n' "$INTENT_ID" > "$ATTEMPT_MARKER") 2>/dev/null; then
  fail "local_submission_attempt_marker_already_exists"
fi
chmod 0600 "$ATTEMPT_MARKER"
rm -f "$RESULT_FILE"

echo "SUBMISSION_ATTEMPT_STARTING=true"
echo "DO_NOT_RETRY_IF_OUTPUT_IS_MISSING_OR_AMBIGUOUS=true"

set +e
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command='sudo /opt/bp-canary/executor.sh'   < "$PREPARED_FILE" | tee "$RESULT_FILE"
submit_rc=${PIPESTATUS[0]}
set -e

# Re-engage locally as a belt-and-suspenders action. The executor itself consumes
# the one-shot arm before its SDK submission attempt.
reengage_kill_switch
ARMED=false

if ! python3 - "$PREPARED_FILE" "$RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

prepared=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
result=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
for name in ("intent_id","prediction_id","paper_order_id","authorization_id"):
    assert str(result[name]) == str(prepared[name])
for name in ("accepted","status","code","request_sha256","executor_sha256","geoblock","account_preflight"):
    assert name in result
PY
then
  echo "SUBMISSION_RESULT_AMBIGUOUS=true"
  echo "SUBMISSION_COMMAND_EXIT_CODE=$submit_rc"
  echo "DO_NOT_RETRY=true"
  echo "KILL_SWITCH_REENGAGED_REQUESTED=true"
  fail "submission_result_missing_malformed_or_unbound"
fi

PHASE15_CANARY_RESULT_FILE="$RESULT_FILE"   PHASE15_CANARY_PREPARED_FILE="$PREPARED_FILE"   bash "$RECORD_HELPER"

echo "SUBMISSION_COMMAND_EXIT_CODE=$submit_rc"
echo "DO_NOT_RETRY=true"
echo "PHASE15_V3_CANARY_INTERACTIVE_OPERATOR=PASS"
