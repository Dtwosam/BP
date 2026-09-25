#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-}"
APPROVAL_FILE="${PHASE15_TELEGRAM_APPROVAL_FILE:-}"
DISPATCH_CLAIM_FILE="${PHASE15_TELEGRAM_DISPATCH_CLAIM_FILE:-}"

fail() {
  echo "PHASE15_V3_CANARY_TELEGRAM_ARM=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "${PHASE15_ACCEPT_TELEGRAM_REAL_MONEY:-no}" == "yes" ]] ||
  fail "telegram_real_money_not_explicitly_accepted"
[[ -n "$PREPARED_FILE" && -r "$PREPARED_FILE" ]] || fail "prepared_file_missing"
[[ -n "$APPROVAL_FILE" && -r "$APPROVAL_FILE" ]] || fail "approval_file_missing"
[[ -n "$DISPATCH_CLAIM_FILE" && -r "$DISPATCH_CLAIM_FILE" ]] ||
  fail "dispatch_claim_file_missing"

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

VALIDATION=$(
  PYTHONPATH="$ROOT/src" python3 -     "$ROOT/PROJECT_STATE.json"     "$PREPARED_FILE"     "$APPROVAL_FILE"     "$DISPATCH_CLAIM_FILE" <<'PY'
import hashlib
import json
import re
import stat
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bp_engine.execution.telegram_approval import validate_approved_handoff
from bp_engine.execution.telegram_origin_attestation import (
    execution_approval,
    payload_sha256,
)
from bp_engine.execution.telegram_pre_execution import source_truth_sha256

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
prepared_path = Path(sys.argv[2])
approval_path = Path(sys.argv[3])
claim_path = Path(sys.argv[4])
prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
approval = json.loads(approval_path.read_text(encoding="utf-8"))
claim_info = claim_path.lstat()
assert not stat.S_ISLNK(claim_info.st_mode)
assert stat.S_ISREG(claim_info.st_mode)
assert stat.S_IMODE(claim_info.st_mode) in (0o600, 0o640)
claim = json.loads(claim_path.read_text(encoding="utf-8"))

gate = state["phase_15_v3_live_canary"]
master = state["phase_14_checkpoint"]["master_live_gate"]
first = gate.get("first_live_canary") or {}

assert str(state.get("source_of_truth_version") or "")
assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["phase15_canary_authorized"] is True
assert gate["canary_order_submitted"] is True
assert first.get("official_reconciliation_complete") is True
assert gate.get("pending_unsubmitted_intent") is None
assert gate.get("v3_strategy_mutation_performed") is False

assert gate.get("second_order_authorized") is True
assert gate.get("automated_real_money_submission") is True
assert gate.get("manual_real_money_submission_required") is False
assert gate.get("telegram_one_tap_submission_authorized") is True
assert gate.get("telegram_persistent_execution_transport_authorized") is True
assert gate.get("telegram_pubsub_transport_authorized") is True
assert gate.get("wallet_material_allowed_on_us_host") is False
assert all(value == "pass" for value in master.values())

assert gate["max_trade_size_usd"] == 10
assert gate["max_total_exposure_usd"] == 10
assert gate["max_daily_loss_usd"] == 10
assert gate["strategy_target_notional_usd"] == 5
assert gate["max_submission_attempts_per_arm"] == 1
assert gate["max_submission_attempts"] == 1

request = prepared["request"]
policy = prepared["policy"]
assert prepared["action"] == "submit"
assert policy["policy_version"] == "v3-live-canary-v1"
assert Decimal(str(policy["max_trade_size_usd"])) == Decimal("10")
assert Decimal(str(policy["max_total_exposure_usd"])) == Decimal("10")
assert Decimal(str(policy["max_daily_loss_usd"])) == Decimal("10")
assert int(policy["max_consecutive_losses"]) == 1
assert int(policy["max_submission_attempts"]) == 1
assert Decimal(str(request["target_notional_usd"])) == Decimal("5")
assert (
    Decimal(str(request["limit_price"]))
    * Decimal(str(request["requested_shares"]))
    <= Decimal("10")
)

binding = validate_approved_handoff(
    prepared,
    approval=approval,
    observed_at=datetime.now(UTC),
)

expected_claim_fields = {
    "schema_version",
    "status",
    "dispatch_ticket_sha256",
    "authorization_report_sha256",
    "source_truth_sha256",
    "transport_key_id",
    "origin_key_id",
    "intent_id",
    "prediction_id",
    "paper_order_id",
    "request_sha256",
    "prepared_sha256",
    "approval_sha256",
    "approval_source_sha256",
    "origin_attestation_sha256",
    "expires_at",
    "claimed_at",
    "retry_allowed",
    "executor_invoked",
    "real_order_submitted",
}
assert set(claim) == expected_claim_fields
assert claim["schema_version"] == 1
assert claim["status"] == "dispatch_claimed"
assert claim["retry_allowed"] is False
assert claim["executor_invoked"] is False
assert claim["real_order_submitted"] is False
assert claim["source_truth_sha256"] == source_truth_sha256(state)
for field in ("intent_id", "prediction_id", "paper_order_id", "request_sha256"):
    assert str(claim[field]) == str(binding[field])
assert claim["prepared_sha256"] == payload_sha256(prepared)
assert claim["approval_sha256"] == payload_sha256(execution_approval(approval))
assert claim["approval_source_sha256"] == payload_sha256(approval)
for name in (
    "dispatch_ticket_sha256",
    "authorization_report_sha256",
    "source_truth_sha256",
    "request_sha256",
    "prepared_sha256",
    "approval_sha256",
    "approval_source_sha256",
    "origin_attestation_sha256",
):
    assert re.fullmatch(r"[0-9a-f]{64}", str(claim[name])) is not None
assert str(claim["transport_key_id"])
assert str(claim["origin_key_id"])

now = datetime.now(UTC)
market_end = datetime.fromisoformat(str(prepared["market_end_at"])).astimezone(UTC)
claim_expires = datetime.fromisoformat(str(claim["expires_at"])).astimezone(UTC)
claimed_at = datetime.fromisoformat(str(claim["claimed_at"])).astimezone(UTC)
assert claimed_at <= now
assert claimed_at < claim_expires
assert now < claim_expires
assert (market_end - now).total_seconds() >= 15

request_encoded = json.dumps(
    request,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
).encode()
request_sha256 = hashlib.sha256(request_encoded).hexdigest()
assert request_sha256 == str(binding["request_sha256"])

print(json.dumps(
    {
        "intent_id": str(binding["intent_id"]),
        "prediction_id": str(binding["prediction_id"]),
        "paper_order_id": str(binding["paper_order_id"]),
        "request_sha256": request_sha256,
        "claim_expires_at": claim_expires.isoformat(),
        "market_end_at": market_end.isoformat(),
        "max_trade_size_usd": str(gate["max_trade_size_usd"]),
        "max_total_exposure_usd": str(gate["max_total_exposure_usd"]),
        "max_daily_loss_usd": str(gate["max_daily_loss_usd"]),
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
) || fail "telegram_arm_source_truth_or_dispatch_validation_failed"

readarray -t BINDING < <(
  python3 - "$VALIDATION" <<'PY'
import json
import sys
payload=json.loads(sys.argv[1])
for name in (
    "intent_id",
    "prediction_id",
    "paper_order_id",
    "request_sha256",
    "claim_expires_at",
    "market_end_at",
    "max_trade_size_usd",
    "max_total_exposure_usd",
    "max_daily_loss_usd",
):
    print(payload[name])
PY
)
INTENT_ID="${BINDING[0]}"
PREDICTION_ID="${BINDING[1]}"
PAPER_ORDER_ID="${BINDING[2]}"
REQUEST_SHA256="${BINDING[3]}"
CLAIM_EXPIRES_AT="${BINDING[4]}"
MARKET_END_AT="${BINDING[5]}"
MAX_TRADE_SIZE_USD="${BINDING[6]}"
MAX_TOTAL_EXPOSURE_USD="${BINDING[7]}"
MAX_DAILY_LOSS_USD="${BINDING[8]}"

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
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
EXPIRES_AT=$(
  python3 - "$CLAIM_EXPIRES_AT" "$MARKET_END_AT" <<'PY'
import sys
from datetime import UTC, datetime, timedelta

claim_expires=datetime.fromisoformat(sys.argv[1]).astimezone(UTC)
market_end=datetime.fromisoformat(sys.argv[2]).astimezone(UTC)
now=datetime.now(UTC)
expires=min(
    now + timedelta(seconds=45),
    market_end - timedelta(seconds=10),
    claim_expires,
)
if expires <= now:
    raise SystemExit("telegram dispatch authorization expired before arm")
print(expires.isoformat())
PY
) || fail "telegram_activation_expiry_invalid"

TMP_DIR=$(mktemp -d)
ARMED=false
cleanup() {
  status=$?
  rm -rf "$TMP_DIR"
  if [[ "$status" -ne 0 && "$ARMED" == "true" ]]; then
    gcloud compute ssh "$VM"       --project="$PROJECT"       --zone="$ZONE"       --quiet       --command="sudo sh -c 'printf %s\\n telegram-arm-failure-reengaged > /etc/bp-canary/KILL; chmod 0600 /etc/bp-canary/KILL'"       >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

MANIFEST="$TMP_DIR/activation.json"
python3 -   "$MANIFEST"   "$LOCAL_HEAD"   "$EXECUTOR_SHA256"   "$AUTHORIZATION_ID"   "$ISSUED_AT"   "$EXPIRES_AT"   "$INTENT_ID"   "$PREDICTION_ID"   "$PAPER_ORDER_ID"   "$REQUEST_SHA256"   "$MAX_TRADE_SIZE_USD"   "$MAX_TOTAL_EXPOSURE_USD"   "$MAX_DAILY_LOSS_USD" <<'PY'
import json
import os
import sys
from pathlib import Path

payload = {
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
    "max_trade_size_usd": sys.argv[11],
    "max_total_exposure_usd": sys.argv[12],
    "max_daily_loss_usd": sys.argv[13],
    "max_submission_attempts": 1,
}
path=Path(sys.argv[1])
path.write_text(json.dumps(payload,sort_keys=True),encoding="utf-8")
os.chmod(path,0o600)
PY

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

REMOTE_UPLOAD="/tmp/bp-canary-telegram-activation-$$.json"
gcloud compute scp "$MANIFEST" "$VM:$REMOTE_UPLOAD"   --project="$PROJECT"   --zone="$ZONE"   --quiet >/dev/null

ARMED=true
gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="sudo install -o root -g root -m 0600 '$REMOTE_UPLOAD' /etc/bp-canary/activation.json && rm -f '$REMOTE_UPLOAD' && sudo rm -f /etc/bp-canary/KILL"

HEALTH=$(
  printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh'
) || fail "executor_health_command_failed"

python3 - "$HEALTH" "$AUTHORIZATION_ID" "$EXECUTOR_SHA256" <<'PY' ||
  fail "executor_not_armed"
import json
import sys
from decimal import Decimal

payload=json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["executor_sha256"] == sys.argv[3]
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
echo "DISPATCH_CLAIM_FILE=$DISPATCH_CLAIM_FILE"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_V3_CANARY_TELEGRAM_ARM=PASS"
