#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"
RESULT_FILE="${PHASE15_CANARY_RESULT_FILE:-/tmp/bp-phase15-v3-canary-result.json}"

fail() {
  echo "PHASE15_V3_CANARY_RECORD=FAIL" >&2
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

[[ -f "$PREPARED_FILE" ]] || fail "prepared_file_missing"
[[ -f "$RESULT_FILE" ]] || fail "result_file_missing"

INTENT_ID=$(python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
with open(sys.argv[1],encoding="utf-8") as handle:
    payload=json.load(handle)
print(payload["intent_id"])
PY
)
RESULT_B64=$(base64 -w0 "$RESULT_FILE")
CANARY_SOURCE_B64=$(base64 -w0 "$ROOT/src/bp_engine/execution/canary.py")

RECORDED=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_SOURCE_B64='$CANARY_SOURCE_B64' CANARY_INTENT_ID='$INTENT_ID' CANARY_RESULT_B64='$RESULT_B64' /opt/bp/.venv/bin/python -" <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime
from sqlalchemy import create_engine
from bp_engine.config import Settings

module=types.ModuleType("phase15_canary_inline")
sys.modules[module.__name__]=module
source=base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(source, "<phase15_canary_inline>", "exec"), module.__dict__)
result=json.loads(base64.b64decode(os.environ["CANARY_RESULT_B64"]).decode("utf-8"))

settings=Settings(_env_file="/etc/bp/bp.env")
engine=create_engine(settings.database_url, pool_pre_ping=True)
try:
    report=module.record_canary_submission(
        engine=engine,
        intent_id=os.environ["CANARY_INTENT_ID"],
        observed_at=datetime.now(UTC),
        result=result,
    )
    print(json.dumps(report,sort_keys=True,default=str))
finally:
    engine.dispose()
PY
) || fail "record_command_failed"

echo "$RECORDED"
echo "PHASE15_V3_CANARY_RECORD=PASS"
