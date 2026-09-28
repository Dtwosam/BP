#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
ACCEPT="${PHASE15_ACCEPT_SECOND_CANARY_ZERO_FILL_COMPLETION:-}"

fail() {
  echo "PHASE15_V3_SECOND_CANARY_ZERO_FILL_COMPLETION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] || fail "explicit_zero_fill_completion_authorization_required"

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
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

SELF_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_second_canary_zero_fill_completion_cloudshell.sh")
RECON_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_second_canary_db_reconciliation_cloudshell.sh")
RUNNER_BLOB=$(git hash-object "$ROOT/scripts/run_phase15_v3_canary_prepare_watch.py")
UNIT_BLOB=$(git hash-object "$ROOT/deploy/bp-phase15-canary-prepare-watch.service")
CANARY_BLOB=$(git hash-object "$ROOT/src/bp_engine/execution/canary.py")
LIVE_BLOB=$(git hash-object "$ROOT/src/bp_engine/execution/live.py")

python3 - "$ROOT/PROJECT_STATE.json"   "$SELF_BLOB" "$RECON_BLOB" "$RUNNER_BLOB" "$UNIT_BLOB" "$CANARY_BLOB" "$LIVE_BLOB" <<'PY' ||
  fail "source_truth_not_authorized_for_exact_zero_fill_completion"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
completion_blob, recon_blob, runner_blob, unit_blob, canary_blob, live_blob = sys.argv[2:]
gate = state["phase_15_v3_live_canary"]
canary = gate["second_live_canary"]
auth = gate["second_live_canary_authorization"]
repair = gate["second_canary_db_reconciliation"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["second_order_authorized"] is False
assert auth["consumed"] is True
assert auth["network_submission_attempt_consumed"] is True
assert auth["third_order_authorized"] is False
assert canary["official_fill_state"] == "zero_fill_observed"
assert canary["confirmed_filled_shares"] == 0
assert canary["confirmed_filled_notional_usd"] == 0
assert canary["live_risk_ledger_reconciliation_complete"] is False
assert repair["status"] == "AUTHORIZED_READY_WITH_RUNTIME_FIX"
assert repair["authorized"] is True
assert repair["authorization_consumed"] is False
assert repair["helper_git_blob_sha"] == recon_blob
assert repair["completion_helper_git_blob_sha"] == completion_blob
assert repair["live_account_snapshot_runtime_fix_git_blob_sha"] == live_blob
assert repair["live_account_snapshot_runtime_fix_deployment_authorized"] is True
assert repair["live_account_snapshot_runtime_fix_deployed"] is False
assert repair["sidecar_runner_git_blob_sha"] == runner_blob
assert repair["sidecar_service_unit_git_blob_sha"] == unit_blob
assert repair["sidecar_canary_git_blob_sha"] == canary_blob
assert repair["does_not_authorize_telegram_approve"] is True
assert repair["does_not_authorize_executor_arm_or_invoke"] is True
assert repair["does_not_authorize_order_submission"] is True
assert repair["does_not_authorize_additional_network_attempt"] is True
assert repair["third_order_authorized"] is False
PY

file_sha256() {
  python3 - "$1" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
}

RUNNER_SHA256=$(file_sha256 "$ROOT/scripts/run_phase15_v3_canary_prepare_watch.py")
UNIT_SHA256=$(file_sha256 "$ROOT/deploy/bp-phase15-canary-prepare-watch.service")
CANARY_SHA256=$(file_sha256 "$ROOT/src/bp_engine/execution/canary.py")
LIVE_SHA256=$(file_sha256 "$ROOT/src/bp_engine/execution/live.py")

ARCHIVE=$(mktemp /tmp/bp-phase15-zero-fill-runtime.XXXXXX.tar.gz)
cleanup_local() { rm -f "$ARCHIVE"; }
trap cleanup_local EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD"   scripts/run_phase15_v3_canary_prepare_watch.py   deploy/bp-phase15-canary-prepare-watch.service   src/bp_engine/execution/canary.py   src/bp_engine/execution/live.py

ARCHIVE_SHA256=$(file_sha256 "$ARCHIVE")
REMOTE_ARCHIVE="/tmp/bp-phase15-zero-fill-runtime-$LOCAL_HEAD.tar.gz"

gcloud compute scp "$ARCHIVE" "$US_VM:$REMOTE_ARCHIVE"   --project="$PROJECT" --zone="$US_ZONE" --quiet ||
  fail "runtime_archive_upload_failed"

printf -v HEAD_Q '%q' "$LOCAL_HEAD"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA_Q '%q' "$ARCHIVE_SHA256"
printf -v RUNNER_SHA_Q '%q' "$RUNNER_SHA256"
printf -v UNIT_SHA_Q '%q' "$UNIT_SHA256"
printf -v CANARY_SHA_Q '%q' "$CANARY_SHA256"
printf -v LIVE_SHA_Q '%q' "$LIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail
umask 077

HELPER_HEAD="${BP_PHASE15_HELPER_HEAD:?}"
ARCHIVE="${BP_PHASE15_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_PHASE15_ARCHIVE_SHA256:?}"
RUNNER_SHA256="${BP_PHASE15_RUNNER_SHA256:?}"
UNIT_SHA256="${BP_PHASE15_UNIT_SHA256:?}"
CANARY_SHA256="${BP_PHASE15_CANARY_SHA256:?}"
LIVE_SHA256="${BP_PHASE15_LIVE_SHA256:?}"

SIDECAR=/opt/bp-phase15-canary-prepare-watch
RELEASES=$SIDECAR/releases
RELEASE=$RELEASES/$HELPER_HEAD-zero-fill-accounting
CURRENT=$SIDECAR/current
SERVICE=bp-phase15-canary-prepare-watch.service
V3_RUNTIME=/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770
BACKUP=$(mktemp -d /var/tmp/bp-phase15-zero-fill-runtime-rollback.XXXXXX)
COMMITTED=false
RELEASE_CREATED=false

fail() {
  echo "PHASE15_V3_SECOND_CANARY_RUNTIME_FIX=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

cleanup() {
  status=$?
  rm -f "$ARCHIVE"
  if [[ "$COMMITTED" == "true" ]]; then
    rm -rf "$BACKUP"
    return
  fi
  if [[ -f "$BACKUP/current-target" ]]; then
    ln -sfn "$(cat "$BACKUP/current-target")" "$CURRENT"
  fi
  if [[ "$RELEASE_CREATED" == "true" ]]; then
    rm -rf "$RELEASE"
  fi
  rm -rf "$BACKUP"
  trap - EXIT
  exit "$status"
}
trap cleanup EXIT

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ -r "$ARCHIVE" ]] || fail "runtime_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "runtime_archive_sha256_mismatch"
[[ -L "$CURRENT" ]] || fail "sidecar_current_symlink_missing"
[[ -x /opt/bp/.venv/bin/python ]] || fail "production_python_missing"
[[ -d "$V3_RUNTIME/src" ]] || fail "frozen_v3_runtime_missing"

if systemctl is-active --quiet "$SERVICE"; then
  fail "prepare_watcher_must_be_inactive"
fi

for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "core_service_not_active:$unit"
done

RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
readlink "$CURRENT" > "$BACKUP/current-target"

install -d -o root -g root -m 0755 "$RELEASES"
if [[ ! -e "$RELEASE" && ! -L "$RELEASE" ]]; then
  install -d -o root -g root -m 0755 "$RELEASE"
  RELEASE_CREATED=true
  tar -xzf "$ARCHIVE" -C "$RELEASE"
  chown -hR root:bp "$RELEASE"
  find "$RELEASE" -type d -exec chmod 0750 {} +
  find "$RELEASE" -type f -exec chmod 0640 {} +
fi

verify_sha() {
  local path=$1 expected=$2
  [[ -f "$path" ]] || fail "runtime_required_path_missing:$path"
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]] ||
    fail "runtime_file_sha256_mismatch:$path"
}

verify_sha "$RELEASE/scripts/run_phase15_v3_canary_prepare_watch.py" "$RUNNER_SHA256"
verify_sha "$RELEASE/deploy/bp-phase15-canary-prepare-watch.service" "$UNIT_SHA256"
verify_sha "$RELEASE/src/bp_engine/execution/canary.py" "$CANARY_SHA256"
verify_sha "$RELEASE/src/bp_engine/execution/live.py" "$LIVE_SHA256"

runuser -u bp -- env   PYTHONDONTWRITEBYTECODE=1   PYTHONPATH="$V3_RUNTIME/src"   /opt/bp/.venv/bin/python - "$RELEASE/src/bp_engine/execution/live.py" <<'PY'
import sys
import types
from pathlib import Path
import bp_engine.execution as execution_package

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
module = types.ModuleType("bp_engine.execution.live")
module.__package__ = "bp_engine.execution"
sys.modules[module.__name__] = module
execution_package.live = module
exec(compile(source, "<phase15-zero-fill-live-runtime>", "exec"), module.__dict__)
assert hasattr(module, "_official_zero_fill_reconciled_intents")
assert hasattr(module, "_account_snapshot")
print("LIVE_ACCOUNT_RUNTIME_IMPORT=PASS")
PY

ln -sfn "$RELEASE" "$CURRENT"

systemctl is-active --quiet "$SERVICE" && fail "prepare_watcher_started_unexpectedly"

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_restarted"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_restarted"
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]] || fail "paper_executor_restarted"

COMMITTED=true
trap - EXIT
rm -f "$ARCHIVE"
rm -rf "$BACKUP"

echo "RUNTIME_RELEASE=$RELEASE"
echo "RUNTIME_CURRENT=$(readlink -f "$CURRENT")"
echo "PREPARE_WATCHER_ACTIVE=false"
echo "CORE_SERVICE_PIDS_PRESERVED=true"
echo "FROZEN_V3_RUNTIME_MUTATED=false"
echo "ORDER_SUBMISSION_PERFORMED=false"
echo "NETWORK_SUBMISSION_ATTEMPT_CONSUMED_BY_RUNTIME_FIX=false"
echo "PHASE15_V3_SECOND_CANARY_RUNTIME_FIX=PASS"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | python3 -c '
import base64
import sys
sys.stdout.write(base64.b64encode(sys.stdin.buffer.read()).decode("ascii"))
')

RUNTIME_RESULT=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env BP_PHASE15_HELPER_HEAD=$HEAD_Q BP_PHASE15_ARCHIVE=$ARCHIVE_Q BP_PHASE15_ARCHIVE_SHA256=$ARCHIVE_SHA_Q BP_PHASE15_RUNNER_SHA256=$RUNNER_SHA_Q BP_PHASE15_UNIT_SHA256=$UNIT_SHA_Q BP_PHASE15_CANARY_SHA256=$CANARY_SHA_Q BP_PHASE15_LIVE_SHA256=$LIVE_SHA_Q bash") ||
  fail "runtime_fix_deployment_failed"

printf '%s
' "$RUNTIME_RESULT"

RECON_RESULT=$(
  PHASE15_ACCEPT_SECOND_CANARY_DB_RECONCILIATION=yes     bash "$ROOT/scripts/deploy/phase15_v3_second_canary_db_reconciliation_cloudshell.sh"
) || fail "db_reconciliation_failed_after_runtime_fix"

printf '%s
' "$RECON_RESULT"

LIVE_SOURCE_B64=$(python3 - "$ROOT/src/bp_engine/execution/live.py" <<'PY'
import base64
import sys
from pathlib import Path
print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii"))
PY
)

ACCOUNT_VERIFY=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' LIVE_SOURCE_B64='$LIVE_SOURCE_B64' /opt/bp/.venv/bin/python -" <<'PY'
from __future__ import annotations

import base64
import json
import os
import sys
import types
from datetime import UTC, datetime

from sqlalchemy import create_engine

import bp_engine.execution as execution_package
from bp_engine.config import Settings

source = base64.b64decode(os.environ["LIVE_SOURCE_B64"]).decode("utf-8")
module = types.ModuleType("bp_engine.execution.live")
module.__package__ = "bp_engine.execution"
sys.modules[module.__name__] = module
execution_package.live = module
exec(compile(source, "<phase15-zero-fill-live-verify>", "exec"), module.__dict__)

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
try:
    with engine.connect() as connection:
        account = module._account_snapshot(connection, observed_at=datetime.now(UTC))
    print(json.dumps({
        "total_exposure_usd": str(account.total_exposure_usd),
        "realized_daily_pnl_usd": str(account.realized_daily_pnl_usd),
        "consecutive_losses": account.consecutive_losses,
        "last_order_at": (
            account.last_order_at.isoformat()
            if account.last_order_at is not None else None
        ),
        "unresolved_critical_reconciliation": (
            account.unresolved_critical_reconciliation
        ),
    }, sort_keys=True))
finally:
    engine.dispose()
PY
) || fail "post_reconciliation_account_snapshot_verification_failed"

python3 - "$ACCOUNT_VERIFY" <<'PY' ||
  fail "post_reconciliation_account_snapshot_not_clean"
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
assert Decimal(payload["total_exposure_usd"]) == 0
assert payload["unresolved_critical_reconciliation"] == 0
PY

echo "$ACCOUNT_VERIFY"
echo "TELEGRAM_APPROVAL_PERFORMED=false"
echo "EXECUTOR_ARMED=false"
echo "EXECUTOR_INVOKED=false"
echo "ORDER_SUBMISSION_PERFORMED=false"
echo "ADDITIONAL_NETWORK_ATTEMPT_CONSUMED=false"
echo "THIRD_ORDER_AUTHORIZED=false"
echo "GLOBAL_LIVE_TRADING_ENABLED=false"
echo "PHASE15_V3_SECOND_CANARY_ZERO_FILL_COMPLETION=PASS"
