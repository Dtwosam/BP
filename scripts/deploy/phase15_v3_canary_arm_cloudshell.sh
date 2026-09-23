#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"

fail() {
  echo "PHASE15_V3_CANARY_ARM=FAIL" >&2
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

[[ "${PHASE15_ACCEPT_REAL_MONEY:-no}" == "yes" ]]   || fail "real_money_not_explicitly_accepted"
[[ -f "$PREPARED_FILE" ]] || fail "prepared_file_missing"

python3 - "$ROOT/PROJECT_STATE.json" "$PREPARED_FILE" <<'PY'   || fail "prepared_payload_not_authorized"
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
master = state["phase_14_checkpoint"]["master_live_gate"]
payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
request = payload["request"]

assert state["source_of_truth_version"] == "0.14.180"
assert gate["phase15_canary_authorized"] is True
assert gate["manual_real_money_submission_required"] is True
assert gate["max_trade_size_usd"] == 10
assert gate["max_total_exposure_usd"] == 10
assert gate["max_daily_loss_usd"] == 10
assert gate["max_accepted_orders"] == 1
assert gate["strategy_target_notional_usd"] == 5
assert gate["canary_order_submitted"] is False
assert gate["second_order_authorized"] is False
assert all(value == "pass" for value in master.values())

assert payload["action"] == "submit"
assert payload["policy"]["policy_version"] == "v3-live-canary-v1"
assert Decimal(str(payload["policy"]["max_trade_size_usd"])) == Decimal("10")
assert Decimal(str(payload["policy"]["max_total_exposure_usd"])) == Decimal("10")
assert Decimal(str(payload["policy"]["max_daily_loss_usd"])) == Decimal("10")
assert int(payload["policy"]["max_consecutive_losses"]) == 1
assert int(payload["policy"]["max_submission_attempts"]) == 1
assert Decimal(str(request["target_notional_usd"])) == Decimal("5")
assert Decimal(str(request["limit_price"])) * Decimal(str(request["requested_shares"])) <= Decimal("10")

market_end = datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
assert (market_end - datetime.now(UTC)).total_seconds() >= 20
PY

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
REQUEST_SHA256=$(python3 - "$PREPARED_FILE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
encoded=json.dumps(
    payload["request"],
    sort_keys=True,
    separators=(",",":"),
    ensure_ascii=True,
).encode("utf-8")
print(hashlib.sha256(encoded).hexdigest())
PY
)
read -r INTENT_ID PREDICTION_ID PAPER_ORDER_ID < <(
python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
from pathlib import Path
payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload["intent_id"], payload["prediction_id"], payload["paper_order_id"])
PY
)

AUTHORIZATION_ID=$(python3 - <<'PY'
import secrets
print("phase15-v3-canary-" + secrets.token_hex(12))
PY
)
ISSUED_AT=$(python3 - <<'PY'
from datetime import UTC, datetime
print(datetime.now(UTC).isoformat())
PY
)
EXPIRES_AT=$(python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
market_end=datetime.fromisoformat(payload["market_end_at"]).astimezone(UTC)
now=datetime.now(UTC)
expires=min(now + timedelta(seconds=45), market_end - timedelta(seconds=10))
if expires <= now:
    raise SystemExit("prepared market is too close to expiry")
print(expires.isoformat())
PY
) || fail "activation_expiry_invalid"

TMP_DIR=$(mktemp -d)
ARMED=false
cleanup() {
  status=$?
  rm -rf "$TMP_DIR"
  if [[ "$status" -ne 0 && "$ARMED" == "true" ]]; then
    gcloud compute ssh "$VM"       --project="$PROJECT"       --zone="$ZONE"       --quiet       --command="sudo sh -c 'printf %s\\n arm-failure-reengaged > /etc/bp-canary/KILL; chmod 0600 /etc/bp-canary/KILL'"       >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
umask 077
MANIFEST="$TMP_DIR/activation.json"
python3 - "$MANIFEST" "$LOCAL_HEAD" "$EXECUTOR_SHA256" "$AUTHORIZATION_ID" "$ISSUED_AT" "$EXPIRES_AT" "$INTENT_ID" "$PREDICTION_ID" "$PAPER_ORDER_ID" "$REQUEST_SHA256" <<'PY'
import json
import sys
from pathlib import Path

payload={
    "authorized": True,
    "git_sha": sys.argv[2],
    "executor_sha256": sys.argv[3],
    "authorization_id": sys.argv[4],
    "issued_at": sys.argv[5],
    "expires_at": sys.argv[6],
    "intent_id": sys.argv[7],
    "prediction_id": sys.argv[8],
    "paper_order_id": sys.argv[9],
    "request_sha256": sys.argv[10],
    "source_prediction_version": "v3-frozen-paper-v1",
    "source_execution_version": "paper-execution-v3-frozen-v1",
    "max_trade_size_usd": "10",
    "max_total_exposure_usd": "10",
    "max_daily_loss_usd": "10",
    "max_submission_attempts": 1,
}
Path(sys.argv[1]).write_text(json.dumps(payload,sort_keys=True),encoding="utf-8")
PY
chmod 600 "$MANIFEST"

python3 - "$PREPARED_FILE" "$AUTHORIZATION_ID" <<'PY'
import json
import os
import sys
from pathlib import Path

path=Path(sys.argv[1])
payload=json.loads(path.read_text(encoding="utf-8"))
payload["authorization_id"]=sys.argv[2]
path.write_text(json.dumps(payload,separators=(",",":")),encoding="utf-8")
os.chmod(path,0o600)
PY

REMOTE_UPLOAD="/tmp/bp-canary-activation-$$.json"
gcloud compute scp "$MANIFEST" "$VM:$REMOTE_UPLOAD"   --project="$PROJECT"   --zone="$ZONE"   --quiet >/dev/null

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="sudo install -o root -g root -m 0600 '$REMOTE_UPLOAD' /etc/bp-canary/activation.json && rm -f '$REMOTE_UPLOAD' && sudo rm -f /etc/bp-canary/KILL"
ARMED=true

HEALTH=$(printf '%s' '{"action":"health"}' |   gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh')   || fail "executor_health_command_failed"

python3 - "$HEALTH" "$AUTHORIZATION_ID" "$EXECUTOR_SHA256" <<'PY' || fail "executor_not_armed"
import json
import sys
from decimal import Decimal
payload=json.loads(sys.argv[1])
expected_executor_sha256=sys.argv[3]
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["executor_sha256"] == expected_executor_sha256
assert payload["account"]["open_order_count"] == 0
assert Decimal(str(payload["account"]["collateral_balance_usd"])) >= Decimal("5")
assert payload["account"]["clean_for_canary"] is True
assert payload["activation_valid"] is True
assert payload["kill_switch_engaged"] is False
assert payload["submission_ready"] is True
PY

trap - EXIT
rm -rf "$TMP_DIR"

echo "$HEALTH"
echo "AUTHORIZATION_ID=$AUTHORIZATION_ID"
echo "ACTIVATION_EXPIRES_AT=$EXPIRES_AT"
echo "PREPARED_FILE=$PREPARED_FILE"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_V3_CANARY_ARM=PASS"
