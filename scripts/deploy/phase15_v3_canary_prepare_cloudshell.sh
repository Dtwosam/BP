#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
MAX_WAIT_SECONDS="${PHASE15_CANARY_MAX_WAIT_SECONDS:-1800}"
POLL_SECONDS="${PHASE15_CANARY_POLL_SECONDS:-2}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"

fail() {
  echo "PHASE15_V3_CANARY_PREPARE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' || fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path
state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate=state["phase_15_v3_live_canary"]
master=state["phase_14_checkpoint"]["master_live_gate"]
assert state["source_of_truth_version"] == "0.14.180"
assert gate["status"] == "ENGINEERING_READY_HOST_PASS"
assert gate["phase15_canary_authorized"] is True
assert gate["max_trade_size_usd"] == 10
assert gate["max_total_exposure_usd"] == 10
assert gate["max_daily_loss_usd"] == 10
assert gate["max_consecutive_losses"] == 1
assert gate["max_accepted_orders"] == 1
assert gate["strategy_target_notional_usd"] == 5
assert gate["manual_real_money_submission_required"] is True
assert gate["live_trading_enabled"] is False
assert all(value == "pass" for value in master.values())
PY

HEALTH=$(printf '%s' '{"action":"health"}' |   gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null)   || fail "executor_health_command_failed"

python3 - "$HEALTH" <<'PY' || fail "executor_health_failed"
import json
import sys
payload=json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["private_key_configured"] is True
assert payload["sdk_import_ok"] is True
assert payload["live_order_submitted"] is False
PY

ACTIVATED_AT=$(python3 - <<'PY'
from datetime import UTC, datetime
print(datetime.now(UTC).isoformat())
PY
)
CANARY_SOURCE_B64=$(base64 -w0 "$ROOT/src/bp_engine/execution/canary.py")
DEADLINE=$(( $(date +%s) + MAX_WAIT_SECONDS ))

prepare_once() {
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_SOURCE_B64='$CANARY_SOURCE_B64' CANARY_ACTIVATED_AT='$ACTIVATED_AT' /opt/bp/.venv/bin/python -" <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime
from sqlalchemy import create_engine
from bp_engine.config import Settings
from bp_engine.execution.live import InterlockDecision

module=types.ModuleType("phase15_canary_inline")
sys.modules[module.__name__]=module
source=base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(source, "<phase15_canary_inline>", "exec"), module.__dict__)

settings=Settings(_env_file="/etc/bp/bp.env")
engine=create_engine(settings.database_url, pool_pre_ping=True)
try:
    report=module.prepare_next_canary(
        engine=engine,
        activated_at=datetime.fromisoformat(os.environ["CANARY_ACTIVATED_AT"]),
        observed_at=datetime.now(UTC),
        interlock=InterlockDecision(eligible=True, reasons=()),
        api_healthy=True,
    )
    print(json.dumps(report, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
}

echo "ACTIVATED_AT=$ACTIVATED_AT"
echo "Waiting for the next NEW frozen-V3 trade order. Historical paper orders are excluded."

while (( $(date +%s) < DEADLINE )); do
  PREPARED=$(prepare_once) || fail "canary_prepare_command_failed"
  STATUS=$(python3 - "$PREPARED" <<'PY'
import json
import sys
print(json.loads(sys.argv[1])["status"])
PY
)

  if [[ "$STATUS" == "waiting" || "$STATUS" == "skipped" ]]; then
    if [[ "$STATUS" == "skipped" ]]; then
      echo "$PREPARED"
    fi
    sleep "$POLL_SECONDS"
    continue
  fi
  if [[ "$STATUS" == "stopped" ]]; then
    echo "$PREPARED"
    echo "PHASE15_V3_CANARY_PREPARE=ALREADY_COMPLETE"
    exit 0
  fi
  [[ "$STATUS" == "prepared" ]] || {
    echo "$PREPARED" >&2
    fail "canary_prepare_blocked"
  }

  umask 077
  python3 - "$PREPARED" "$PREPARED_FILE" <<'PY'
import json
import os
import sys

payload=json.loads(sys.argv[1])
payload["action"]="submit"
path=sys.argv[2]
with open(path,"w",encoding="utf-8") as handle:
    json.dump(payload,handle,separators=(",",":"))
os.chmod(path,0o600)
print(json.dumps({
    "intent_id":payload["intent_id"],
    "prediction_id":payload["prediction_id"],
    "paper_order_id":payload["paper_order_id"],
    "target_notional_usd":payload["request"]["target_notional_usd"],
    "limit_price":payload["request"]["limit_price"],
    "requested_shares":payload["request"]["requested_shares"],
    "selected_side":payload["request"]["selected_side"],
    "market_end_at":payload["market_end_at"],
},indent=2,sort_keys=True))
PY

  echo "PREPARED_FILE=$PREPARED_FILE"
  echo "NO_REAL_ORDER_SUBMITTED=true"
  echo "PHASE15_V3_CANARY_PREPARE=PASS"
  exit 0
done

fail "no_eligible_v3_trade_within_wait_window"
