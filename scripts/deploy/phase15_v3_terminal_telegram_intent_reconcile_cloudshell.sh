#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
ACCEPT="${PHASE15_ACCEPT_TERMINAL_INTENT_RECONCILIATION:-}"

fail() {
  echo "PHASE15_V3_TERMINAL_INTENT_RECONCILIATION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_terminal_intent_reconciliation_authorization_required"

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

readarray -t AUTH < <(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
recon = gate.get("terminal_telegram_intent_reconciliation") or {}
second = gate.get("second_live_canary_authorization") or {}
watch = gate.get("persistent_prepare_watch") or {}
stage = gate.get("telegram_transport_stage") or {}

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second.get("status") == "AUTHORIZED_NOT_SUBMITTED"
assert second.get("max_network_submission_attempts") == 1
assert stage.get("status") == "PRODUCTION_ACTIVE_WAITING_FOR_FRESH_TELEGRAM_APPROVAL"
assert stage.get("executor_safe_idle") is True
assert stage.get("real_order_submitted") is False
assert watch.get("second_canary_network_attempt_consumed") is False
assert watch.get("fresh_restart_authorization_consumed") is True
assert watch.get("fresh_restart_result") == "SAFE_FAIL_PENDING_TERMINAL_INTENT_RECONCILIATION"

assert recon.get("status") == "AUTHORIZED_NOT_RUN"
assert recon.get("authorized") is True
assert recon.get("authorization_consumed") is False
assert recon.get("does_not_authorize_telegram_approve") is True
assert recon.get("does_not_authorize_executor_arm_or_invoke") is True
assert recon.get("does_not_authorize_order_submission") is True
assert recon.get("does_not_consume_second_canary_network_attempt") is True

print(recon["intent_id"])
print(recon["prediction_id"])
print(recon["paper_order_id"])
print(recon["helper_git_blob_sha"])
PY
) || fail "source_truth_reconciliation_authorization_invalid"

INTENT_ID="${AUTH[0]:-}"
PREDICTION_ID="${AUTH[1]:-}"
PAPER_ORDER_ID="${AUTH[2]:-}"
EXPECTED_HELPER_BLOB="${AUTH[3]:-}"

[[ "$INTENT_ID" =~ ^live-intent-[0-9a-f]{32}$ ]] || fail "intent_id_invalid"
[[ "$PREDICTION_ID" =~ ^[0-9a-f]{64}$ ]] || fail "prediction_id_invalid"
[[ "$PAPER_ORDER_ID" =~ ^[0-9a-f]{64}$ ]] || fail "paper_order_id_invalid"
[[ "$EXPECTED_HELPER_BLOB" =~ ^[0-9a-f]{40}$ ]] || fail "helper_blob_invalid"

ACTUAL_HELPER_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_terminal_telegram_intent_reconcile_cloudshell.sh")
[[ "$ACTUAL_HELPER_BLOB" == "$EXPECTED_HELPER_BLOB" ]] ||
  fail "terminal_intent_reconciliation_helper_binding_mismatch"

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

echo "=== PRE-MUTATION EXECUTOR SAFETY ==="
HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "executor_health_command_failed"

python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY' ||
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
expected_sha256 = sys.argv[2]
assert payload["status"] == "ok"
assert payload["executor_sha256"] == expected_sha256
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["account"]["open_order_count"] == 0
assert Decimal(str(payload["account"]["collateral_balance_usd"])) >= Decimal("5")
assert payload["account"]["clean_for_canary"] is True
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
PY
fail "executor_not_safe_for_terminal_intent_reconciliation"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command='sudo test ! -f /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json' ||
  fail "second_canary_attempt_marker_present"

echo "=== READ-ONLY DATABASE PRECHECK ==="
CANARY_SOURCE_B64=$(python3 - "$ROOT/src/bp_engine/execution/canary.py" <<'PY'
import base64
import sys
from pathlib import Path

print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii"), end="")
PY
)

HEALTH_B64=$(python3 - "$HEALTH" <<'PY'
import base64
import sys

print(base64.b64encode(sys.argv[1].encode("utf-8")).decode("ascii"), end="")
PY
)

PRECHECK=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_INTENT_ID='$INTENT_ID' CANARY_PREDICTION_ID='$PREDICTION_ID' CANARY_PAPER_ORDER_ID='$PAPER_ORDER_ID' /opt/bp/.venv/bin/python -" <<'PY'
import json
import os

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)
try:
    with engine.connect() as connection:
        assert connection.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        intent = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id == os.environ["CANARY_INTENT_ID"]
            )
        ).mappings().one()
        assert str(intent["prediction_id"]) == os.environ["CANARY_PREDICTION_ID"]
        evidence = dict(intent["evidence"] or {})
        assert str(evidence.get("paper_order_id") or "") == os.environ["CANARY_PAPER_ORDER_ID"]

        events = list(
            connection.execute(
                select(
                    schema.live_order_events.c.event_type,
                    schema.live_order_events.c.external_order_id,
                )
                .where(schema.live_order_events.c.intent_id == os.environ["CANARY_INTENT_ID"])
                .order_by(schema.live_order_events.c.id)
            ).mappings()
        )
        terminal = {
            "accepted",
            "rejected",
            "submission_unknown",
            "closed_before_submission",
        }
        submission_attempts = [
            row for row in events
            if row["event_type"] in {"accepted", "rejected", "submission_unknown"}
        ]
        closed = [row for row in events if row["event_type"] == "closed_before_submission"]
        assert not submission_attempts
        print(json.dumps({
            "intent_id": os.environ["CANARY_INTENT_ID"],
            "prediction_id": os.environ["CANARY_PREDICTION_ID"],
            "paper_order_id": os.environ["CANARY_PAPER_ORDER_ID"],
            "existing_events": events,
            "submission_attempt_event_count": len(submission_attempts),
            "closed_before_submission_count": len(closed),
            "has_any_terminal_event": any(row["event_type"] in terminal for row in events),
            "database_read_only": True,
        }, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
) || fail "database_precheck_failed"

printf '%s
' "$PRECHECK"

echo "=== RECONCILE TERMINAL TELEGRAM INTENT ==="
RECONCILED=$(gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_SOURCE_B64='$CANARY_SOURCE_B64' CANARY_HEALTH_B64='$HEALTH_B64' CANARY_INTENT_ID='$INTENT_ID' /opt/bp/.venv/bin/python -" <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime

from sqlalchemy import create_engine
from bp_engine.config import Settings

module = types.ModuleType("phase15_terminal_intent_canary_inline")
sys.modules[module.__name__] = module
source = base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(source, "<phase15_terminal_intent_canary_inline>", "exec"), module.__dict__)
health = json.loads(base64.b64decode(os.environ["CANARY_HEALTH_B64"]).decode("utf-8"))

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
try:
    report = module.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=os.environ["CANARY_INTENT_ID"],
        observed_at=datetime.now(UTC),
        reason="telegram_handoff_failed_no_retry_before_executor_submission",
        executor_health=health,
    )
    print(json.dumps(report, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
) || fail "terminal_intent_reconciliation_command_failed"

python3 - "$RECONCILED" "$INTENT_ID" <<'PY' ||
import json
import sys
payload = json.loads(sys.argv[1])
assert payload["status"] in {"reconciled", "already_reconciled"}
assert payload["intent_id"] == sys.argv[2]
assert payload["event_type"] == "closed_before_submission"
assert payload["submission_attempt_consumed"] is False
PY
fail "terminal_intent_reconciliation_result_invalid"

echo "$RECONCILED"

echo "=== POST-MUTATION SAFETY ==="
POST_HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "post_reconciliation_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' ||
import json
import sys
payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
assert payload["account"]["open_order_count"] == 0
PY
fail "post_reconciliation_executor_not_safe"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command='sudo test ! -f /var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json' ||
  fail "second_canary_attempt_marker_created_by_reconciliation"

echo "KILL_SWITCH_ENGAGED=true"
echo "LIVE_ORDER_SUBMITTED=false"
echo "SUBMISSION_ATTEMPT_CONSUMED=false"
echo "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false"
echo "PHASE15_V3_TERMINAL_INTENT_RECONCILIATION=PASS"
