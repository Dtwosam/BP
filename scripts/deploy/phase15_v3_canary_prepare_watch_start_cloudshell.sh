#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
MAX_WAIT_SECONDS="${PHASE15_CANARY_MAX_WAIT_SECONDS:-7200}"
POLL_SECONDS="${PHASE15_CANARY_POLL_SECONDS:-0.5}"
ACCEPT="${PHASE15_ACCEPT_PERSISTENT_PREPARE_WATCH:-}"

fail_local() {
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_START=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] || fail_local "explicit_persistent_prepare_authorization_required"
[[ "$MAX_WAIT_SECONDS" =~ ^[0-9]+$ ]] || fail_local "max_wait_seconds_invalid"
(( MAX_WAIT_SECONDS >= 1 && MAX_WAIT_SECONDS <= 7200 )) || fail_local "max_wait_seconds_out_of_range"
python3 - "$POLL_SECONDS" <<'PY' || fail_local "poll_seconds_invalid"
import sys
value=float(sys.argv[1])
assert 0.5 <= value <= 10
PY

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail_local "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail_local "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || fail_local "gcloud_auth_missing"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' || fail_local "source_truth_not_authorized"
import json
import sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate=state["phase_15_v3_live_canary"]
first=gate.get("first_live_canary") or {}
second=gate.get("second_live_canary_authorization") or {}
stage=gate.get("telegram_transport_stage") or {}
activation=gate.get("telegram_transport_activation_authorization") or {}
watch=gate["persistent_prepare_watch"]
master=state["phase_14_checkpoint"]["master_live_gate"]
assert state["source_of_truth_version"] == "0.14.180"
assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["phase15_canary_authorized"] is True
assert gate["canary_order_submitted"] is True
assert first.get("official_reconciliation_complete") is True
assert gate.get("pending_unsubmitted_intent") is None
assert gate.get("v3_strategy_mutation_performed") is False
assert gate["second_order_authorized"] is True
assert gate["automated_real_money_submission"] is True
assert gate["manual_real_money_submission_required"] is False
assert gate["telegram_one_tap_submission_authorized"] is True
assert gate["telegram_persistent_execution_transport_authorized"] is True
assert gate["telegram_pubsub_transport_authorized"] is True
assert stage.get("status") == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
assert stage.get("activation_pass") is True
assert stage.get("activation_consumed") is True
assert stage.get("executor_safe_idle") is True
assert stage.get("real_order_submitted") is False
assert stage.get("waiting_for_fresh_telegram_approval") is True
assert activation.get("status") == "ACTIVATED_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
assert activation.get("activation_result") == "PASS"
assert activation.get("authorization_consumed") is True
assert activation.get("waiting_for_fresh_telegram_approval") is True
assert activation.get("telegram_approval_performed") is False
assert activation.get("executor_armed") is False
assert activation.get("order_submission_performed") is False
assert activation.get("real_order_submitted") is False
assert second.get("status") == "AUTHORIZED_NOT_SUBMITTED"
assert second.get("strategy_target_notional_usd") == 5
assert second.get("max_network_submission_attempts") == 1
assert second.get("requires_fresh_telegram_approval") is True
assert second.get("requires_official_reconciliation_before_any_third_order") is True
assert watch["authorized"] is True
assert watch["max_wait_seconds"] == 7200
assert watch["prepare_only"] is True
assert watch["arm_automated"] is False
assert watch["submission_automated"] is False
assert all(value == "pass" for value in master.values())
PY

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

HEALTH=$(printf '%s' '{"action":"health"}' | \
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" \
    --zone="$EXEC_ZONE" \
    --quiet \
    --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) \
  || fail_local "executor_health_command_failed"

read -r OFFICIAL_OPEN_ORDER_COUNT COLLATERAL_BALANCE_USD < <(
python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY'
import json
import sys
from decimal import Decimal
payload=json.loads(sys.argv[1])
expected=sys.argv[2]
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["private_key_configured"] is True
assert payload["sdk_import_ok"] is True
assert payload["executor_sha256"] == expected
assert payload["account"]["open_order_count"] == 0
assert Decimal(str(payload["account"]["collateral_balance_usd"])) >= Decimal("5")
assert payload["account"]["clean_for_canary"] is True
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
print(payload["account"]["open_order_count"], payload["account"]["collateral_balance_usd"])
PY
) || fail_local "executor_health_failed"

ACTIVATED_AT=$(python3 - <<'PY'
from datetime import UTC, datetime
print(datetime.now(UTC).isoformat())
PY
)
RUN_ID="phase15-prepare-watch-$(date -u +%Y%m%dT%H%M%SZ)-${LOCAL_HEAD:0:8}"
ARCHIVE=$(mktemp /tmp/bp-phase15-prepare-watch.XXXXXX.tar.gz)
cleanup() { rm -f "$ARCHIVE"; }
trap cleanup EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD" \
  scripts/run_phase15_v3_canary_prepare_watch.py \
  deploy/bp-phase15-canary-prepare-watch.service \
  src/bp_engine/execution/live.py \
  src/bp_engine/execution/canary.py
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-phase15-prepare-watch-$LOCAL_HEAD.tar.gz"
gcloud compute scp "$ARCHIVE" "$US_VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet

printf -v HEAD_Q '%q' "$LOCAL_HEAD"
printf -v RUN_ID_Q '%q' "$RUN_ID"
printf -v ACTIVATED_AT_Q '%q' "$ACTIVATED_AT"
printf -v OPEN_Q '%q' "$OFFICIAL_OPEN_ORDER_COUNT"
printf -v COLLATERAL_Q '%q' "$COLLATERAL_BALANCE_USD"
printf -v WAIT_Q '%q' "$MAX_WAIT_SECONDS"
printf -v POLL_Q '%q' "$POLL_SECONDS"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA_Q '%q' "$ARCHIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${BP_PHASE15_HELPER_HEAD:?}"
RUN_ID="${BP_PHASE15_RUN_ID:?}"
ACTIVATED_AT="${BP_PHASE15_ACTIVATED_AT:?}"
OFFICIAL_OPEN_ORDER_COUNT="${BP_PHASE15_OFFICIAL_OPEN_ORDER_COUNT:?}"
COLLATERAL_BALANCE_USD="${BP_PHASE15_COLLATERAL_BALANCE_USD:?}"
MAX_WAIT_SECONDS="${BP_PHASE15_MAX_WAIT_SECONDS:?}"
POLL_SECONDS="${BP_PHASE15_POLL_SECONDS:?}"
ARCHIVE="${BP_PHASE15_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_PHASE15_ARCHIVE_SHA256:?}"

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
V3_RUNTIME=/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770
SIDECAR=/opt/bp-phase15-canary-prepare-watch
RELEASES=$SIDECAR/releases
RELEASE=$RELEASES/$HELPER_HEAD
CURRENT=$SIDECAR/current
STATE_ROOT=/var/lib/bp/phase15-canary-prepare-watch
RUN_DIR=$STATE_ROOT/runs/$RUN_ID
CURRENT_ENV=$STATE_ROOT/current.env
CURRENT_RUN=$STATE_ROOT/current-run
SERVICE=bp-phase15-canary-prepare-watch.service
SERVICE_PATH=/etc/systemd/system/$SERVICE

fail() {
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_START=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_research_zero_money() {
  local path
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    [[ "$(read_env "$path" MODE)" == "research" ]] || fail "mode_not_research:$path"
    [[ "$(read_env "$path" LIVE_TRADING_ENABLED)" == "false" ]] || fail "live_enabled:$path"
    [[ "$(read_env "$path" MAX_TRADE_SIZE_USD)" == "0" ]] || fail "trade_limit_nonzero:$path"
    [[ "$(read_env "$path" MAX_DAILY_LOSS_USD)" == "0" ]] || fail "loss_limit_nonzero:$path"
  done
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -d "$V3_RUNTIME/src" ]] || fail "frozen_v3_runtime_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] \
  || fail "candidate_archive_sha256_mismatch"
[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$OFFICIAL_OPEN_ORDER_COUNT" == "0" ]] || fail "official_open_orders_present"
require_research_zero_money

for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
done

if systemctl is-active --quiet "$SERVICE"; then
  fail "persistent_prepare_watch_already_active"
fi

install -d -o root -g root -m 0755 "$RELEASES"
if [[ ! -d "$RELEASE" ]]; then
  install -d -o root -g root -m 0755 "$RELEASE"
  tar -xzf "$ARCHIVE" -C "$RELEASE"
fi
for path in \
  scripts/run_phase15_v3_canary_prepare_watch.py \
  deploy/bp-phase15-canary-prepare-watch.service \
  src/bp_engine/execution/live.py \
  src/bp_engine/execution/canary.py
do
  [[ -f "$RELEASE/$path" ]] || fail "release_required_path_missing:$path"
done

install -d -o bp -g bp -m 0700 "$STATE_ROOT"
install -d -o bp -g bp -m 0700 "$STATE_ROOT/runs"
[[ ! -e "$RUN_DIR" ]] || fail "run_dir_already_exists"
install -d -o bp -g bp -m 0700 "$RUN_DIR"

ENV_TMP=$(mktemp /var/tmp/bp-phase15-prepare-watch-env.XXXXXX)
cat > "$ENV_TMP" <<EOF
BP_PHASE15_PREPARE_RUN_DIR=$RUN_DIR
BP_PHASE15_PREPARE_RUN_ID=$RUN_ID
BP_PHASE15_PREPARE_HELPER_HEAD=$HELPER_HEAD
BP_PHASE15_PREPARE_ACTIVATED_AT=$ACTIVATED_AT
BP_PHASE15_PREPARE_OFFICIAL_OPEN_ORDER_COUNT=$OFFICIAL_OPEN_ORDER_COUNT
BP_PHASE15_PREPARE_COLLATERAL_BALANCE_USD=$COLLATERAL_BALANCE_USD
BP_PHASE15_PREPARE_MAX_WAIT_SECONDS=$MAX_WAIT_SECONDS
BP_PHASE15_PREPARE_POLL_SECONDS=$POLL_SECONDS
BP_PHASE15_PREPARE_AUTHORIZED_SUBMISSION_ATTEMPT_LIMIT=2
BP_PHASE15_PREPARE_AUTHORIZED_ACCEPTED_ORDER_LIMIT=2
EOF
install -o root -g bp -m 0640 "$ENV_TMP" "$CURRENT_ENV"
rm -f "$ENV_TMP"
printf '%s\n' "$RUN_DIR" > "$CURRENT_RUN"
chown root:bp "$CURRENT_RUN"
chmod 0640 "$CURRENT_RUN"

ln -sfn "$RELEASE" "$CURRENT"
install -o root -g root -m 0644 "$RELEASE/deploy/$SERVICE" "$SERVICE_PATH"
systemctl daemon-reload
systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
systemctl start "$SERVICE"
sleep 1

ACTIVE=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
if [[ "$ACTIVE" != "active" && ! -r "$RUN_DIR/status.json" ]]; then
  journalctl -u "$SERVICE" -n 50 --no-pager >&2 || true
  fail "watcher_failed_before_status"
fi

require_research_zero_money
for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "service_changed:$unit"
done

[[ -r "$RUN_DIR/status.json" ]] && cat "$RUN_DIR/status.json" || true
echo "RUN_ID=$RUN_ID"
echo "REMOTE_RUN_DIR=$RUN_DIR"
echo "SERVICE_ACTIVE=$ACTIVE"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "ARM_AUTOMATED=false"
echo "SUBMISSION_AUTOMATED=false"
echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_START=PASS"
rm -f "$ARCHIVE"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" \
  --zone="$US_ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env BP_PHASE15_HELPER_HEAD=$HEAD_Q BP_PHASE15_RUN_ID=$RUN_ID_Q BP_PHASE15_ACTIVATED_AT=$ACTIVATED_AT_Q BP_PHASE15_OFFICIAL_OPEN_ORDER_COUNT=$OPEN_Q BP_PHASE15_COLLATERAL_BALANCE_USD=$COLLATERAL_Q BP_PHASE15_MAX_WAIT_SECONDS=$WAIT_Q BP_PHASE15_POLL_SECONDS=$POLL_Q BP_PHASE15_ARCHIVE=$ARCHIVE_Q BP_PHASE15_ARCHIVE_SHA256=$ARCHIVE_SHA_Q bash"

echo "The watcher now runs on bp-recorder independently of Cloud Shell."
echo "It is prepare-only, bounded to at most $MAX_WAIT_SECONDS seconds, and is not enabled across VM reboot."
