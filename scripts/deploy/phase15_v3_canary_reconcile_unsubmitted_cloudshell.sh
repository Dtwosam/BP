#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"

fail() {
  echo "PHASE15_V3_CANARY_RECONCILE_UNSUBMITTED=FAIL" >&2
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

[[ "${PHASE15_ACCEPT_UNSUBMITTED_RECONCILIATION:-no}" == "yes" ]] ||
  fail "unsubmitted_reconciliation_not_explicitly_accepted"
[[ -f "$PREPARED_FILE" ]] || fail "prepared_file_missing"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' ||
  fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
master = state["phase_14_checkpoint"]["master_live_gate"]

assert state["source_of_truth_version"] == "0.14.180"
assert gate["phase15_canary_authorized"] is True
assert gate["manual_real_money_submission_required"] is True
assert gate["canary_order_submitted"] is False
assert gate["second_order_authorized"] is False
assert all(value == "pass" for value in master.values())
PY

INTENT_ID=$(python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["action"] == "submit"
market_end = datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
remaining = (market_end - datetime.now(UTC)).total_seconds()
assert remaining < 20
print(payload["intent_id"])
PY
) || fail "prepared_intent_still_armable_or_invalid"

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path

print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "executor_health_command_failed"

python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY' ||
  fail "executor_not_safe_for_unsubmitted_reconciliation"
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
expected_executor_sha256 = sys.argv[2]

assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["executor_sha256"] == expected_executor_sha256
assert payload["account"]["open_order_count"] == 0
assert Decimal(str(payload["account"]["collateral_balance_usd"])) >= Decimal("5")
assert payload["account"]["clean_for_canary"] is True
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
PY

CANARY_SOURCE_B64=$(base64 -w0 "$ROOT/src/bp_engine/execution/canary.py")
HEALTH_B64=$(printf '%s' "$HEALTH" | base64 -w0)

RECONCILED=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_SOURCE_B64='$CANARY_SOURCE_B64' CANARY_HEALTH_B64='$HEALTH_B64' CANARY_INTENT_ID='$INTENT_ID' /opt/bp/.venv/bin/python -" <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime

from sqlalchemy import create_engine

from bp_engine.config import Settings

module = types.ModuleType("phase15_canary_inline")
sys.modules[module.__name__] = module
source = base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(source, "<phase15_canary_inline>", "exec"), module.__dict__)
health = json.loads(
    base64.b64decode(os.environ["CANARY_HEALTH_B64"]).decode("utf-8")
)

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
try:
    report = module.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=os.environ["CANARY_INTENT_ID"],
        observed_at=datetime.now(UTC),
        reason="prepared_market_no_longer_armable",
        executor_health=health,
    )
    print(json.dumps(report, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
) || fail "unsubmitted_reconciliation_command_failed"

python3 - "$RECONCILED" "$INTENT_ID" <<'PY' ||
  fail "unsubmitted_reconciliation_result_invalid"
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["status"] in ("reconciled", "already_reconciled")
assert payload["intent_id"] == sys.argv[2]
assert payload["event_type"] == "closed_before_submission"
assert payload["submission_attempt_consumed"] is False
PY

echo "$RECONCILED"
echo "KILL_SWITCH_ENGAGED=true"
echo "LIVE_ORDER_SUBMITTED=false"
echo "SUBMISSION_ATTEMPT_CONSUMED=false"
echo "PHASE15_V3_CANARY_RECONCILE_UNSUBMITTED=PASS"
