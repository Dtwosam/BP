#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${BP_FAST_LIVE_GCP_PROJECT:-${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}}"
US_ZONE="${BP_FAST_LIVE_RECORDER_ZONE:-${PHASE15_CANARY_US_ZONE:-us-east1-c}}"
US_VM="${BP_FAST_LIVE_RECORDER_VM:-${PHASE15_CANARY_US_VM:-bp-recorder}}"
EXEC_ZONE="${BP_FAST_LIVE_EXEC_ZONE:-${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}}"
EXEC_VM="${BP_FAST_LIVE_EXEC_VM:-${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}}"
ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_LEGACY_RETIREMENT:-}"

fail() {
  echo "PHASE15_FAST_LIVE_LEGACY_RETIREMENT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "I_ACCEPT_RETIRE_RECONCILED_LEGACY_TELEGRAM_TRANSPORT_FOR_FAST_LIVE" ]] ||
  fail "explicit_legacy_transport_retirement_authorization_required"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

readarray -t BINDINGS < <(
  python3 - "$ROOT/PROJECT_STATE.json" \
    "$ROOT/docs/evidence/phase-15-v3-second-canary-submission-zero-fill-readonly-20260928.json" \
    "$ROOT/docs/evidence/phase-15-v3-second-canary-zero-fill-completion-pass-production-20260928.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
submission = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
completion = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

gate = state["phase_15_v3_live_canary"]
auth = gate["second_live_canary_authorization"]
canary = gate["second_live_canary"]
recon = gate["second_canary_db_reconciliation"]
fast = gate["fast_live_preauthorization"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False

assert auth["consumed"] is True
assert auth["network_submission_attempt_consumed"] is True
assert auth["authorization_slot_consumed"] is True
assert auth["real_order_submitted"] is True
assert auth["accepted"] is True
assert auth["retry_authorized"] is False
assert auth["third_order_authorized"] is False

assert canary["status"] == "RECONCILED_ZERO_FILL"
assert canary["official_reconciliation_complete"] is True
assert canary["external_official_reconciliation_complete"] is True
assert canary["confirmed_filled_shares"] == 0
assert canary["confirmed_filled_notional_usd"] == 0
assert canary["open_order_count"] == 0
assert canary["order_still_open"] is False
assert canary["exposure_usd"] == 0
assert canary["live_risk_ledger_reconciliation_complete"] is True
assert canary["db_reconciliation_unresolved_count"] == 0
assert canary["db_reconciliation_critical_count"] == 0
assert canary["post_completion_total_exposure_usd"] == 0
assert canary["post_completion_unresolved_critical_reconciliation"] == 0

assert recon["status"] == "COMPLETED_PASS"
assert recon["production_db_reconciliation_status"] == "reconciled"
assert recon["production_db_reconciliation_unresolved_count"] == 0
assert recon["production_db_reconciliation_critical_count"] == 0
assert recon["completion_result"] == "PASS"
assert recon["third_order_authorized"] is False

assert fast["status"] == "AUTHORIZED_CONTINUOUS_SESSION"
assert fast["authorized"] is True
assert fast["authorization_mode"] == "auto-telegram-continuous-v1"
assert fast["requires_telegram_approval"] is True
assert fast["max_network_submission_attempts_per_intent"] == 1
assert fast["activation_performed"] is False
assert fast["runtime_authorization_created"] is False
assert fast["real_order_submitted"] is False

submitted = submission["submission"]
official = submission["official_fill_probe"]
assert submitted["intent_id"] == auth["intent_id"] == canary["intent_id"]
assert submitted["prediction_id"] == auth["prediction_id"] == canary["prediction_id"]
assert submitted["paper_order_id"] == auth["paper_order_id"] == canary["paper_order_id"]
assert submitted["external_order_id"] == auth["external_order_id"] == canary["external_order_id"]
assert submitted["accepted"] is True
assert submitted["real_order_submitted"] is True
assert submitted["network_submission_attempt_consumed"] is True
assert submitted["authorization_slot_consumed"] is True
assert submitted["retry_allowed"] is False
assert (submitted.get("cancellation") or {}).get("status") == "cancelled"
assert official["fill_state"] == "zero_fill_observed"
assert official["official_reconciliation_complete"] is True
assert official["open_order_count"] == 0
assert official["matching_trade_count"] == 0

assert completion["reconciliation"]["status"] == "reconciled"
assert completion["reconciliation"]["intent_id"] == auth["intent_id"]
assert completion["reconciliation"]["external_order_id"] == auth["external_order_id"]
assert completion["reconciliation"]["reconciliation_id"] == canary["db_reconciliation_id"]
assert completion["reconciliation"]["unresolved_count"] == 0
assert completion["reconciliation"]["critical_count"] == 0
assert completion["post_completion_account_snapshot"]["total_exposure_usd"] == "0"
assert completion["post_completion_account_snapshot"]["unresolved_critical_reconciliation"] == 0

print(auth["intent_id"])
print(submitted["request_sha256"])
print(auth["external_order_id"])
print(canary["db_reconciliation_id"])
PY
) || fail "source_truth_or_completion_evidence_invalid"

INTENT_ID="${BINDINGS[0]:-}"
REQUEST_SHA256="${BINDINGS[1]:-}"
EXTERNAL_ORDER_ID="${BINDINGS[2]:-}"
RECONCILIATION_ID="${BINDINGS[3]:-}"

[[ "$INTENT_ID" =~ ^live-intent-[0-9a-f]{32}$ ]] || fail "intent_id_invalid"
[[ "$REQUEST_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "request_sha256_invalid"
[[ "$EXTERNAL_ORDER_ID" =~ ^0x[0-9a-f]{64}$ ]] || fail "external_order_id_invalid"
[[ "$RECONCILIATION_ID" =~ ^live-reconciliation-[0-9a-f]{32}$ ]] ||
  fail "reconciliation_id_invalid"

echo "=== PRE-MUTATION EXECUTOR SAFETY ==="
HEALTH=$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh"
) || fail "executor_health_probe_failed"

python3 - "$HEALTH" <<'PY' || fail "executor_not_safe_idle"
import json
import sys

health = json.loads(sys.argv[1])
assert health["status"] == "ok"
assert health["kill_switch_engaged"] is True
assert health["activation_valid"] is False
assert health["submission_ready"] is False
assert health["live_order_submitted"] is False
assert (health.get("geoblock") or {}).get("blocked") is False
assert (health.get("geoblock") or {}).get("country") == "ZA"
account = health.get("account") or {}
assert account.get("clean_for_canary") is True
assert int(account.get("open_order_count", -1)) == 0
PY

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo test -f /etc/bp-canary/KILL &&
             sudo test -f /var/lib/bp-canary/fast-live/KILL &&
             sudo test ! -e /etc/bp-fast-live/authorization.json &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service && exit 40 || true;
             sudo systemctl is-enabled --quiet bp-phase15-fast-live-receiver.service && exit 41 || true" ||
  fail "executor_fast_live_not_safely_inactive"

echo "=== VERIFY TERMINAL LEGACY ATTEMPT ==="
EXEC_BINDING=$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="sudo python3 - '$INTENT_ID' '$REQUEST_SHA256' '$EXTERNAL_ORDER_ID'" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

intent_id, request_sha256, external_order_id = sys.argv[1:]
root = Path("/var/lib/bp-canary/telegram-live-handoff")
marker = root / "second-canary.attempt.json"

info = marker.lstat()
assert stat.S_ISREG(info.st_mode)
assert not stat.S_ISLNK(info.st_mode)
assert info.st_uid == 0

raw = marker.read_bytes()
payload = json.loads(raw.decode("utf-8"))
assert payload["status"] == "second_canary_network_attempt_starting"
assert payload["intent_id"] == intent_id
assert payload["request_sha256"] == request_sha256
assert payload["retry_allowed"] is False

identity = str(payload["package_identity"])
assert identity
result_path = root / f"{identity}.result.json"
failure_path = root / f"{identity}.failure.json"
attempt_path = root / f"{identity}.attempt.json"

assert result_path.is_file() and not result_path.is_symlink()
assert attempt_path.is_file() and not attempt_path.is_symlink()
assert not failure_path.exists()

result = json.loads(result_path.read_text(encoding="utf-8"))
attempt = json.loads(attempt_path.read_text(encoding="utf-8"))

assert result["status"] == "executor_result_recorded"
assert result["package_identity"] == identity
assert result["intent_id"] == intent_id
assert result["request_sha256"] == request_sha256
assert result["external_order_id"] == external_order_id
assert result["accepted"] is True
assert result["network_submission_attempt_consumed"] is True
assert result["authorization_slot_consumed"] is True
assert result["retry_allowed"] is False
assert result["executor_invoked"] is True
assert result["real_order_submitted"] is True
assert (result.get("cancellation") or {}).get("status") == "cancelled"
assert attempt["intent_id"] == intent_id
assert attempt["request_sha256"] == request_sha256
assert attempt["authorization_id"] == payload["authorization_id"]

print(identity)
print(hashlib.sha256(raw).hexdigest())
PY
) || fail "terminal_legacy_attempt_binding_invalid"

PACKAGE_IDENTITY=$(printf '%s\n' "$EXEC_BINDING" | sed -n '1p')
MARKER_SHA256=$(printf '%s\n' "$EXEC_BINDING" | sed -n '2p')
[[ -n "$PACKAGE_IDENTITY" ]] || fail "package_identity_missing"
[[ "$MARKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "marker_sha256_invalid"

echo "PACKAGE_IDENTITY=$PACKAGE_IDENTITY"
echo "LEGACY_MARKER_SHA256=$MARKER_SHA256"
echo "TERMINAL_LEGACY_ATTEMPT=PASS"

echo "=== VERIFY NO PENDING LEGACY RECORDER DELIVERY ==="
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo python3 -" <<'PY' || fail "legacy_recorder_delivery_pending"
from pathlib import Path

root = Path("/var/lib/bp/phase15-canary-telegram-transport")
outbox = root / "outbox"
published = root / "published"
failed = root / "failed"
pending = []
if outbox.is_dir():
    for path in sorted(outbox.glob("*.json")):
        if not (published / path.name).is_file() and not (failed / path.name).is_file():
            pending.append(path.name)
print(f"LEGACY_PENDING_OUTBOX_COUNT={len(pending)}")
assert not pending
PY

echo "=== VERIFY NO PENDING LEGACY EXECUTOR DELIVERY ==="
gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo python3 - '$PACKAGE_IDENTITY'" <<'PY' ||
  fail "legacy_executor_delivery_pending"
from pathlib import Path
import sys

terminal_identity = sys.argv[1]
root = Path("/var/lib/bp-telegram-transport")
ready = root / "ready"
processed = Path("/var/lib/bp-canary/telegram-execution-auth-processed")
failures = Path("/var/lib/bp-canary/telegram-execution-auth-failures")
authorized = Path("/var/lib/bp-canary/telegram-execution-authorized")
live = Path("/var/lib/bp-canary/telegram-live-handoff")

pending_ready = []
if ready.is_dir():
    for path in sorted(ready.iterdir()):
        if path.is_symlink() or not path.is_dir():
            continue
        name = path.name + ".json"
        if not (processed / name).is_file() and not (failures / name).is_file():
            pending_ready.append(path.name)

pending_authorized = []
if authorized.is_dir():
    for path in sorted(authorized.iterdir()):
        if path.is_symlink() or not path.is_dir():
            continue
        identity = path.name
        if identity == terminal_identity:
            continue
        if not (live / f"{identity}.result.json").is_file() and not (
            live / f"{identity}.failure.json"
        ).is_file():
            pending_authorized.append(identity)

print(f"LEGACY_PENDING_READY_COUNT={len(pending_ready)}")
print(f"LEGACY_PENDING_AUTHORIZED_COUNT={len(pending_authorized)}")
assert not pending_ready
assert not pending_authorized
PY

echo "=== RETIRE LEGACY EXECUTOR TRANSPORT ==="
gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo bash -s" <<'REMOTE' || fail "executor_legacy_transport_retirement_failed"
set -Eeuo pipefail
umask 077

install -d -o root -g root -m 0700 /etc/bp-canary
printf '%s\n' fast-live-legacy-retirement-safe-stop > /etc/bp-canary/KILL
install -d -o root -g root -m 0700 /var/lib/bp-canary/fast-live
printf '%s\n' fast-live-legacy-retirement-safe-stop > /var/lib/bp-canary/fast-live/KILL

services=(
  bp-phase15-telegram-privileged-handoff.service
  bp-phase15-telegram-execution-authorization-worker.service
  bp-phase15-telegram-transport-claim-worker.service
  bp-phase15-telegram-pubsub-streaming-receiver.service
)
systemctl stop "${services[@]}" >/dev/null 2>&1 || true
systemctl disable "${services[@]}" >/dev/null 2>&1 || true

rm -f \
  /etc/bp-telegram-transport/receiver.env \
  /etc/bp-telegram-transport/claim.env \
  /etc/bp-telegram-transport/execution-auth.env \
  /etc/bp-telegram-transport/privileged-handoff.env \
  /etc/bp-telegram-transport/transport.key \
  /etc/bp-telegram-transport/origin.key

for service in "${services[@]}"; do
  test "$(systemctl is-active "$service" 2>/dev/null || true)" != active
  test "$(systemctl is-enabled "$service" 2>/dev/null || true)" != enabled
done

test -f /etc/bp-canary/KILL
test -f /var/lib/bp-canary/fast-live/KILL
REMOTE

echo "=== RETIRE LEGACY RECORDER TRANSPORT ==="
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo bash -s" <<'REMOTE' || fail "recorder_legacy_transport_retirement_failed"
set -Eeuo pipefail

test -f /etc/bp/telegram-approval.env

systemctl stop bp-phase15-canary-telegram-approval.service
systemctl stop bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true
systemctl disable bp-phase15-telegram-pubsub-publisher.service >/dev/null 2>&1 || true

rm -f \
  /etc/bp/telegram-pubsub-publisher.env \
  /etc/bp/telegram-approval-handoff.env \
  /etc/bp-telegram-transport/transport.key \
  /etc/bp-telegram-transport/origin.key \
  /etc/bp-telegram-transport/project-state.json

test ! -e /etc/bp/telegram-approval-handoff.env
test -f /etc/bp/telegram-approval.env

systemctl start bp-phase15-canary-telegram-approval.service
sleep 2
systemctl is-active --quiet bp-phase15-canary-telegram-approval.service
systemctl is-enabled --quiet bp-phase15-canary-telegram-approval.service
test "$(systemctl is-active bp-phase15-telegram-pubsub-publisher.service 2>/dev/null || true)" != active
test "$(systemctl is-enabled bp-phase15-telegram-pubsub-publisher.service 2>/dev/null || true)" != enabled
REMOTE

echo "=== ARCHIVE CONSUMED LEGACY GLOBAL MARKER ==="
ARCHIVE_PATH=$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="sudo python3 - '$INTENT_ID' '$REQUEST_SHA256' '$MARKER_SHA256'" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

intent_id, request_sha256, expected_sha256 = sys.argv[1:]
root = Path("/var/lib/bp-canary/telegram-live-handoff")
marker = root / "second-canary.attempt.json"
archive = root / "archive"
archive.mkdir(mode=0o700, exist_ok=True)
os.chown(archive, 0, 0)
os.chmod(archive, 0o700)

info = marker.lstat()
assert stat.S_ISREG(info.st_mode)
assert not stat.S_ISLNK(info.st_mode)
raw = marker.read_bytes()
assert hashlib.sha256(raw).hexdigest() == expected_sha256
payload = json.loads(raw.decode("utf-8"))
assert payload["intent_id"] == intent_id
assert payload["request_sha256"] == request_sha256
assert payload["retry_allowed"] is False

target = archive / f"second-canary.attempt.{expected_sha256}.json"
assert not target.exists()
os.rename(marker, target)

fd = os.open(archive, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)

assert not marker.exists()
assert target.is_file()
assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha256
print(target)
PY
) || fail "legacy_attempt_marker_archive_failed"

[[ "$ARCHIVE_PATH" == /var/lib/bp-canary/telegram-live-handoff/archive/second-canary.attempt.*.json ]] ||
  fail "legacy_archive_path_invalid"

echo "LEGACY_ATTEMPT_ARCHIVE=$ARCHIVE_PATH"

echo "=== POST-RETIREMENT SAFETY ==="
POST_HEALTH=$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh"
) || fail "post_retirement_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' || fail "post_retirement_executor_not_safe_idle"
import json
import sys
health = json.loads(sys.argv[1])
assert health["status"] == "ok"
assert health["kill_switch_engaged"] is True
assert health["activation_valid"] is False
assert health["submission_ready"] is False
assert health["live_order_submitted"] is False
account = health.get("account") or {}
assert account.get("clean_for_canary") is True
assert int(account.get("open_order_count", -1)) == 0
PY

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo test ! -e /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json &&
             sudo test -f '$ARCHIVE_PATH' &&
             sudo test -f /etc/bp-canary/KILL &&
             sudo test -f /var/lib/bp-canary/fast-live/KILL &&
             sudo test ! -e /etc/bp-fast-live/authorization.json" ||
  fail "post_retirement_executor_state_invalid"

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo test -f /etc/bp/telegram-approval.env &&
             sudo test ! -e /etc/bp/telegram-approval-handoff.env &&
             sudo systemctl is-active --quiet bp-phase15-canary-telegram-approval.service &&
             sudo systemctl is-enabled --quiet bp-phase15-canary-telegram-approval.service" ||
  fail "post_retirement_recorder_state_invalid"

echo "SOURCE_MAIN=$HEAD"
echo "LEGACY_SECOND_CANARY_INTENT_ID=$INTENT_ID"
echo "LEGACY_SECOND_CANARY_RECONCILIATION_ID=$RECONCILIATION_ID"
echo "LEGACY_SECOND_CANARY_ZERO_FILL_RECONCILED=true"
echo "LEGACY_SECOND_CANARY_AUTHORIZATION_CONSUMED=true"
echo "LEGACY_ATTEMPT_MARKER_ARCHIVED=true"
echo "LEGACY_TRANSPORT_SERVICES_ACTIVE=false"
echo "LEGACY_TRANSPORT_RUNTIME_MATERIAL_PRESENT=false"
echo "TELEGRAM_CREDENTIAL_ENV_PRESERVED=true"
echo "TELEGRAM_LISTENER_ACTIVE=true"
echo "FAST_LIVE_KILL_SWITCH_ENGAGED=true"
echo "FAST_LIVE_RUNTIME_AUTHORIZATION_PRESENT=false"
echo "PUBSUB_RESOURCES_CHANGED=false"
echo "HISTORICAL_STATE_DELETED=false"
echo "NEW_NETWORK_SUBMISSION_ATTEMPT_PERFORMED=false"
echo "NEW_REAL_ORDER_SUBMITTED=false"
echo "PHASE15_FAST_LIVE_LEGACY_RETIREMENT=PASS"
