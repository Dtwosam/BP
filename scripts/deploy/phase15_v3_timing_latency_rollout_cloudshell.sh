#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_TIMING_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_TIMING_ZONE:-us-east1-c}"
VM="${PHASE15_TIMING_VM:-bp-recorder}"
AUTHORIZED_COMMIT="c1e8166c463dd0111098ca29e43cd8a106d11939"
RUNNER_FILE="scripts/run_v3_paper_execution.py"
UNIT_FILE="deploy/bp-v3-paper-execution.service"

fail() {
  echo "PHASE15_V3_TIMING_LATENCY_CLOUDSHELL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "${PHASE15_ACCEPT_V3_TIMING_LATENCY_ROLLOUT:-}" == "yes" ]] ||
  fail "explicit_timing_latency_rollout_acceptance_missing"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"

git fetch origin
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
git merge-base --is-ancestor "$AUTHORIZED_COMMIT" "$LOCAL_HEAD" ||
  fail "authorized_timing_commit_not_in_current_main"
git diff --quiet "$AUTHORIZED_COMMIT" "$LOCAL_HEAD" -- "$RUNNER_FILE" "$UNIT_FILE" ||
  fail "timing_runtime_binding_changed_after_authorized_commit"

RUNNER_SHA256=$(sha256sum "$RUNNER_FILE" | awk '{print $1}')
REMOTE_UPLOAD="/tmp/bp-phase15-v3-timing-${RUNNER_SHA256}.py"

gcloud compute scp "$RUNNER_FILE" "$VM:$REMOTE_UPLOAD"   --project="$PROJECT" --zone="$ZONE" --quiet

if ! gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="sudo env EXPECTED_RUNNER_SHA256='$RUNNER_SHA256' REMOTE_UPLOAD='$REMOTE_UPLOAD' AUTHORIZED_COMMIT='$AUTHORIZED_COMMIT' bash -s" <<'REMOTE'
set -Eeuo pipefail

CURRENT_LINK="/var/lib/bp/runtime/v3-paper-current"
EXPECTED_OLD_RUNTIME="/var/lib/bp/runtime/v3-paper-hotpath-206c5d3128855c2322a7c3a1fc59e294d52ba6dc"
NEW_RUNTIME="/var/lib/bp/runtime/v3-paper-timing-${AUTHORIZED_COMMIT}"
ACTIVATION="/var/lib/bp/v3-paper/activation.json"
ENV_FILE="/etc/bp/bp.env"
SAFETY_FILE="/etc/bp/bp-prospective-runtime-safety.env"
EVIDENCE_DIR="/var/lib/bp/evidence"
EXEC_UNIT="bp-v3-paper-execution.service"
RUNNER_REL="scripts/run_v3_paper_execution.py"

MUTATED=0
NEW_CREATED=0
SUCCESS=0
OLD_RUNTIME=""

fail() {
  echo "PHASE15_V3_TIMING_LATENCY_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local file=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

require_zero_money() {
  local file
  for file in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -f "$file" ]] || fail "missing_safety_file:$file"
    [[ "$(read_env "$file" MODE)" == "research" ]] || fail "mode_not_research:$file"
    [[ "$(read_env "$file" LIVE_TRADING_ENABLED)" == "false" ]] || fail "live_trading_not_false:$file"
    [[ "$(read_env "$file" MAX_TRADE_SIZE_USD)" == "0" ]] || fail "max_trade_size_not_zero:$file"
    [[ "$(read_env "$file" MAX_DAILY_LOSS_USD)" == "0" ]] || fail "max_daily_loss_not_zero:$file"
  done
}

rollback() {
  if [[ "$MUTATED" == "1" && -n "$OLD_RUNTIME" ]]; then
    echo "ROLLBACK=restoring_previous_v3_runtime" >&2
    ln -sfn "$OLD_RUNTIME" "$CURRENT_LINK" || true
    systemctl restart "$EXEC_UNIT" || true
  fi
  if [[ "$NEW_CREATED" == "1" && -d "$NEW_RUNTIME" ]]; then
    rm -rf "$NEW_RUNTIME" || true
  fi
}

on_exit() {
  local status=$?
  rm -f "$REMOTE_UPLOAD" || true
  if (( status != 0 )) && [[ "$SUCCESS" != "1" ]]; then
    rollback
  fi
  exit "$status"
}
trap on_exit EXIT

[[ -L "$CURRENT_LINK" ]] || fail "v3_current_link_missing"
OLD_RUNTIME=$(readlink -f "$CURRENT_LINK")

if [[ "$OLD_RUNTIME" == "$NEW_RUNTIME" ]]; then
  [[ -f "$NEW_RUNTIME/$RUNNER_REL" ]] || fail "current_timing_runner_missing"
  [[ "$(sha256sum "$NEW_RUNTIME/$RUNNER_REL" | awk '{print $1}')" == "$EXPECTED_RUNNER_SHA256" ]] ||
    fail "current_timing_runner_sha_mismatch"
  require_zero_money
  echo "NEW_RUNTIME=$NEW_RUNTIME"
  echo "LIVE_TRADING_ENABLED=false"
  echo "MAX_TRADE_SIZE_USD=0"
  echo "MAX_DAILY_LOSS_USD=0"
  echo "PHASE15_V3_TIMING_LATENCY_ROLLOUT=ALREADY_CURRENT"
  SUCCESS=1
  exit 0
fi

[[ "$OLD_RUNTIME" == "$EXPECTED_OLD_RUNTIME" ]] ||
  fail "unexpected_current_runtime:$OLD_RUNTIME"

for unit in   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service   bp-prospective-outcomes.service   bp-v4-forward-coverage.timer
do
  systemctl is-active --quiet "$unit" || fail "unit_not_active:$unit"
done
require_zero_money

python3 - "$ACTIVATION" <<'PY' || fail "activation_identity_invalid"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "model_sha256": "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7",
    "prediction_version": "v3-frozen-paper-v1",
    "execution_version": "paper-execution-v3-frozen-v1",
    "paper_starting_cash_usd": "100.00",
    "paper_target_notional_usd": "5.00",
    "real_money_usd": "0.00",
    "automatic_promotion": False,
}
for key, value in expected.items():
    assert payload.get(key) == value, key
PY

[[ -f "$REMOTE_UPLOAD" ]] || fail "uploaded_runner_missing"
[[ "$(sha256sum "$REMOTE_UPLOAD" | awk '{print $1}')" == "$EXPECTED_RUNNER_SHA256" ]] ||
  fail "uploaded_runner_sha_mismatch"
grep -q '"--poll-seconds"' "$REMOTE_UPLOAD" || fail "runner_poll_argument_missing"
grep -q '"1"' "$REMOTE_UPLOAD" || fail "runner_one_second_poll_missing"

RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
EXEC_PID_BEFORE=$(systemctl show -p MainPID --value "$EXEC_UNIT")

[[ ! -e "$NEW_RUNTIME" ]] || fail "new_runtime_already_exists"
cp -a "$OLD_RUNTIME" "$NEW_RUNTIME"
NEW_CREATED=1
install -o root -g bp -m 0444 "$REMOTE_UPLOAD" "$NEW_RUNTIME/$RUNNER_REL"

[[ "$(sha256sum "$NEW_RUNTIME/$RUNNER_REL" | awk '{print $1}')" == "$EXPECTED_RUNNER_SHA256" ]] ||
  fail "installed_runner_sha_mismatch"

DIFF_OUTPUT=$(diff -qr "$OLD_RUNTIME" "$NEW_RUNTIME" || true)
DIFF_COUNT=$(printf '%s\n' "$DIFF_OUTPUT" | sed '/^$/d' | wc -l)
[[ "$DIFF_COUNT" -eq 1 ]] || {
  printf '%s\n' "$DIFF_OUTPUT" >&2
  fail "runtime_diff_not_single_file"
}
printf '%s\n' "$DIFF_OUTPUT" | grep -q "$RUNNER_REL" ||
  fail "runtime_diff_not_runner"

chmod -R a-w "$NEW_RUNTIME"
OBSERVE_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ln -sfn "$NEW_RUNTIME" "$CURRENT_LINK"
MUTATED=1

systemctl restart "$EXEC_UNIT"
systemctl is-active --quiet "$EXEC_UNIT" || fail "v3_executor_not_active_after_restart"
[[ "$(readlink -f "$CURRENT_LINK")" == "$NEW_RUNTIME" ]] ||
  fail "runtime_link_not_updated"

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
EXEC_PID_AFTER=$(systemctl show -p MainPID --value "$EXEC_UNIT")

[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_pid_changed"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_pid_changed"
[[ "$EXEC_PID_AFTER" != "$EXEC_PID_BEFORE" ]] || fail "executor_pid_did_not_change"
require_zero_money

REPORTS_TMP=$(mktemp)
deadline=$(( $(date +%s) + 120 ))
report_count=0
while (( $(date +%s) < deadline )); do
  journalctl -u "$EXEC_UNIT" _PID="$EXEC_PID_AFTER"     --since "$OBSERVE_SINCE" --no-pager -o json > "$REPORTS_TMP"
  report_count=$(python3 - "$REPORTS_TMP" <<'PY'
import json
import sys
count = 0
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        try:
            row = json.loads(line)
            payload = json.loads(row.get("MESSAGE", ""))
        except Exception:
            continue
        if "examined_predictions" in payload:
            count += 1
print(count)
PY
)
  (( report_count >= 6 )) && break
  sleep 2
done
(( report_count >= 6 )) || fail "insufficient_executor_reports_after_rollout"

TIMING_JSON=$(python3 - "$REPORTS_TMP" <<'PY'
import json
import statistics
import sys

rows = []
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        try:
            row = json.loads(line)
            payload = json.loads(row.get("MESSAGE", ""))
        except Exception:
            continue
        if "examined_predictions" in payload:
            rows.append((int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, payload))

if len(rows) < 6:
    raise SystemExit("insufficient_executor_reports_after_rollout")
rows.sort(key=lambda item: item[0])
steady = rows[-5:]
gaps = [round(b[0] - a[0], 3) for a, b in zip(steady, steady[1:])]
median_gap = statistics.median(gaps)
max_gap = max(gaps)
if median_gap > 6:
    raise SystemExit(f"executor_median_steady_gap_too_high:{median_gap}")
if max_gap > 10:
    raise SystemExit(f"executor_max_steady_gap_too_high:{max_gap}")
print(json.dumps({
    "report_count": len(rows),
    "steady_cycle_gaps_seconds": gaps,
    "median_steady_cycle_gap_seconds": median_gap,
    "max_steady_cycle_gap_seconds": max_gap,
    "latest_examined_predictions": steady[-1][1]["examined_predictions"],
    "latest_existing_orders": steady[-1][1]["existing_orders"],
    "latest_skipped_predictions": steady[-1][1]["skipped_predictions"],
}, sort_keys=True))
PY
) || fail "executor_cycle_timing_validation_failed"
rm -f "$REPORTS_TMP"

for unit in   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service   bp-prospective-outcomes.service   bp-v4-forward-coverage.timer
do
  systemctl is-active --quiet "$unit" || fail "postcheck_unit_not_active:$unit"
done
require_zero_money

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE="$EVIDENCE_DIR/phase15-v3-timing-latency-rollout-$STAMP.json"

python3 -   "$EVIDENCE" "$AUTHORIZED_COMMIT" "$EXPECTED_RUNNER_SHA256"   "$OLD_RUNTIME" "$NEW_RUNTIME"   "$RECORDER_PID_BEFORE" "$RECORDER_PID_AFTER"   "$PREDICTOR_PID_BEFORE" "$PREDICTOR_PID_AFTER"   "$EXEC_PID_BEFORE" "$EXEC_PID_AFTER" "$TIMING_JSON" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    path, authorized_commit, runner_sha256, old_runtime, new_runtime,
    recorder_pid_before, recorder_pid_after,
    predictor_pid_before, predictor_pid_after,
    executor_pid_before, executor_pid_after, timing_raw,
) = sys.argv[1:]

payload = {
    "observed_at": datetime.now(UTC).isoformat(),
    "authorized_commit": authorized_commit,
    "runner_sha256": runner_sha256,
    "old_runtime": old_runtime,
    "new_runtime": new_runtime,
    "scope": "frozen_v3_paper_execution_polling_only",
    "poll_seconds": 1,
    "strategy_changed": False,
    "model_changed": False,
    "selected_offset_changed": False,
    "risk_policy_changed": False,
    "canary_submission_code_changed": False,
    "live_trading_enabled": False,
    "max_trade_size_usd": 0,
    "max_daily_loss_usd": 0,
    "recorder_pid_before": int(recorder_pid_before),
    "recorder_pid_after": int(recorder_pid_after),
    "predictor_pid_before": int(predictor_pid_before),
    "predictor_pid_after": int(predictor_pid_after),
    "executor_pid_before": int(executor_pid_before),
    "executor_pid_after": int(executor_pid_after),
    "timing": json.loads(timing_raw),
}
Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

chown bp:bp "$EVIDENCE"
chmod 0640 "$EVIDENCE"
SUCCESS=1

echo "$TIMING_JSON"
echo "OLD_RUNTIME=$OLD_RUNTIME"
echo "NEW_RUNTIME=$NEW_RUNTIME"
echo "EVIDENCE_FILE=$EVIDENCE"
echo "RECORDER_PID_PRESERVED=true"
echo "PREDICTOR_PID_PRESERVED=true"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
echo "PHASE15_V3_TIMING_LATENCY_ROLLOUT=PASS"
REMOTE
then
  fail "remote_timing_latency_rollout_failed_or_rolled_back"
fi

echo "PHASE15_V3_TIMING_LATENCY_CLOUDSHELL=PASS"
