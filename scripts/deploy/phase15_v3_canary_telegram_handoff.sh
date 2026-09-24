#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"

fail() {
  echo "PHASE15_V3_CANARY_TELEGRAM_HANDOFF=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail "expected_prepared_and_approval_paths"
PREPARED_FILE="$1"
APPROVAL_FILE="$2"
[[ -r "$PREPARED_FILE" ]] || fail "prepared_file_missing"
[[ -r "$APPROVAL_FILE" ]] || fail "approval_file_missing"
DISPATCH_CLAIM_FILE="${PHASE15_TELEGRAM_DISPATCH_CLAIM_FILE:-}"
[[ -n "$DISPATCH_CLAIM_FILE" ]] || fail "dispatch_claim_file_not_configured"
[[ -r "$DISPATCH_CLAIM_FILE" ]] || fail "dispatch_claim_file_missing"
[[ "${PHASE15_ACCEPT_TELEGRAM_REAL_MONEY:-no}" == "yes" ]] ||
  fail "telegram_real_money_not_explicitly_accepted"

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

ARM_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_arm_cloudshell.sh"
RECORD_HELPER="$ROOT/scripts/deploy/phase15_v3_canary_record_cloudshell.sh"
[[ -x "$ARM_HELPER" || -f "$ARM_HELPER" ]] || fail "arm_helper_missing"
[[ -x "$RECORD_HELPER" || -f "$RECORD_HELPER" ]] || fail "record_helper_missing"

PYTHONPATH="$ROOT/src" python3 - "$ROOT/PROJECT_STATE.json" "$PREPARED_FILE" "$APPROVAL_FILE" "$DISPATCH_CLAIM_FILE" <<'PY' ||
  fail "telegram_handoff_not_authorized_by_source_truth_or_dispatch_claim"
import json
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_approval import validate_approved_handoff
from bp_engine.execution.telegram_origin_attestation import (
    execution_approval,
    payload_sha256,
)
from bp_engine.execution.telegram_pre_execution import source_truth_sha256

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
prepared = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
approval = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
claim_path = Path(sys.argv[4])
info = claim_path.lstat()
assert not stat.S_ISLNK(info.st_mode)
assert stat.S_ISREG(info.st_mode)
assert stat.S_IMODE(info.st_mode) in (0o600, 0o640)
claim = json.loads(claim_path.read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate.get("second_order_authorized") is True
assert gate.get("automated_real_money_submission") is True
assert gate.get("manual_real_money_submission_required") is False
assert gate.get("telegram_one_tap_submission_authorized") is True
assert gate.get("telegram_persistent_execution_transport_authorized") is True
assert gate.get("telegram_pubsub_transport_authorized") is True

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

binding = validate_approved_handoff(
    prepared,
    approval=approval,
    observed_at=datetime.now(UTC),
)
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

claimed_at = datetime.fromisoformat(str(claim["claimed_at"])).astimezone(UTC)
assert claimed_at <= datetime.now(UTC)
PY

DISPATCH_CLAIM_SHA256=$(sha256sum "$DISPATCH_CLAIM_FILE" | awk '{print $1}')
STATE_DIR=$(dirname "$APPROVAL_FILE")
SUBMISSION_MARKER="$STATE_DIR/submission-attempt.json"
RESULT_FILE="$STATE_DIR/executor-result.json"

ARMED=false
reengage_kill_switch() {
  gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command="sudo sh -c 'printf %s\\n telegram-handoff-safe-stop > /etc/bp-canary/KILL; chmod 0600 /etc/bp-canary/KILL'"     >/dev/null 2>&1 || true
}
cleanup() {
  if [[ "$ARMED" == "true" ]]; then
    reengage_kill_switch
  fi
}
trap cleanup EXIT

PHASE15_ACCEPT_REAL_MONEY=yes PHASE15_CANARY_PREPARED_FILE="$PREPARED_FILE"   bash "$ARM_HELPER" >/dev/null
ARMED=true

[[ "$(sha256sum "$DISPATCH_CLAIM_FILE" | awk '{print $1}')" == "$DISPATCH_CLAIM_SHA256" ]] ||
  fail "dispatch_claim_changed_after_arm"

PYTHONPATH="$ROOT/src" python3 - "$PREPARED_FILE" "$APPROVAL_FILE" "$DISPATCH_CLAIM_FILE" <<'PY' ||
  fail "approval_or_prepared_binding_changed_after_arm"
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.telegram_approval import validate_approved_handoff

prepared = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
approval = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
claim = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
binding = validate_approved_handoff(
    prepared,
    approval=approval,
    observed_at=datetime.now(UTC),
)
for field in ("intent_id", "prediction_id", "paper_order_id", "request_sha256"):
    assert str(claim[field]) == str(binding[field])
assert claim["status"] == "dispatch_claimed"
assert claim["retry_allowed"] is False
assert claim["executor_invoked"] is False
assert claim["real_order_submitted"] is False
assert str(prepared.get("authorization_id") or "").startswith("phase15-v3-canary-")
PY

python3 - "$SUBMISSION_MARKER" "$PREPARED_FILE" "$APPROVAL_FILE" <<'PY' ||
  fail "submission_attempt_already_exists"
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

prepared = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
approval = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
payload = {
    "schema_version": 1,
    "status": "submission_attempt_starting",
    "intent_id": str(prepared["intent_id"]),
    "request_sha256": str(approval["request_sha256"]),
    "started_at": datetime.now(UTC).isoformat(),
    "retry_allowed": False,
}
path = Path(sys.argv[1])
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
fd = os.open(path, flags, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\n")
PY

rm -f "$RESULT_FILE"
set +e
gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command='sudo /opt/bp-canary/executor.sh'   < "$PREPARED_FILE" > "$RESULT_FILE"
submit_rc=$?
set -e

reengage_kill_switch
ARMED=false

PYTHONPATH="$ROOT/src" python3 - "$PREPARED_FILE" "$APPROVAL_FILE" "$RESULT_FILE" "$submit_rc" <<'PY' ||
  fail "submission_result_missing_malformed_or_unbound"
import json
import sys
from pathlib import Path

prepared = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
approval = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
result = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
submit_rc = int(sys.argv[4])

for name in ("intent_id", "prediction_id", "paper_order_id", "authorization_id"):
    assert str(result[name]) == str(prepared[name])
assert str(result["request_sha256"]) == str(approval["request_sha256"])
for name in ("accepted", "status", "code", "executor_sha256", "geoblock", "account_preflight"):
    assert name in result
assert submit_rc == 0
PY

PHASE15_CANARY_RESULT_FILE="$RESULT_FILE" PHASE15_CANARY_PREPARED_FILE="$PREPARED_FILE"   bash "$RECORD_HELPER" >/dev/null

trap - EXIT
echo "PHASE15_V3_CANARY_TELEGRAM_HANDOFF=PASS"
echo "INTENT_ID=$(python3 - "$PREPARED_FILE" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["intent_id"])
PY
)"
echo "DO_NOT_RETRY=true"
