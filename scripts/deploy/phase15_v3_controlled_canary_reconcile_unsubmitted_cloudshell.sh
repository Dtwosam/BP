#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
ACCEPT="${PHASE15_ACCEPT_CONTROLLED_CANARY_RECONCILIATION:-}"
EXPECTED_INTENT="${PHASE15_EXPECT_INTENT_ID:-}"

fail() {
  echo "PHASE15_CONTROLLED_CANARY_RECONCILIATION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] || fail "explicit_controlled_canary_reconciliation_authorization_required"
[[ "$EXPECTED_INTENT" =~ ^live-intent-[0-9a-f]{32}$ ]] || fail "expected_intent_id_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

HELPER_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_controlled_canary_reconcile_unsubmitted_cloudshell.sh")
CANARY_BLOB=$(git hash-object "$ROOT/src/bp_engine/execution/canary.py")

python3 - "$ROOT/PROJECT_STATE.json" "$HELPER_BLOB" "$CANARY_BLOB" <<'PY' ||
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
second = gate["second_live_canary_authorization"]
controlled = gate["controlled_auto_approved_canary_authorization"]
supervisor = gate["controlled_submission_supervisor"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
assert second["max_network_submission_attempts"] == 1
assert controlled["consumed"] is False
assert controlled["network_attempt_observed"] is False
assert controlled["real_order_submission_observed"] is False
assert supervisor["authorized"] is True
assert supervisor["completed"] is False
assert supervisor["max_network_submission_attempts"] == 1
assert supervisor["reconcile_only_after_market_end_grace_seconds"] == 20
assert supervisor["reconcile_helper_git_blob_sha"] == sys.argv[2]
assert supervisor["canary_git_blob_sha"] == sys.argv[3]
PY
fail "source_truth_supervisor_authorization_invalid"

PREPARED=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
ROOT=/var/lib/bp/phase15-canary-prepare-watch
CURRENT=$ROOT/current-run
[[ -r "$CURRENT" ]]
RUN_DIR=$(cat "$CURRENT")
[[ -r "$RUN_DIR/prepared.json" ]]
cat "$RUN_DIR/prepared.json"
REMOTE
) || fail "prepared_candidate_read_failed"

python3 - "$PREPARED" "$EXPECTED_INTENT" <<'PY' || fail "prepared_candidate_invalid"
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

payload = json.loads(sys.argv[1])
expected = sys.argv[2]
assert payload["action"] == "submit"
assert payload["intent_id"] == expected
assert str(payload["prediction_id"])
assert str(payload["paper_order_id"])
request = payload["request"]
assert Decimal(str(request["target_notional_usd"])) == Decimal("5")
market_end = datetime.fromisoformat(str(payload["market_end_at"])).astimezone(UTC)
assert datetime.now(UTC) >= market_end + timedelta(seconds=20)
PY

PREDICTION_ID=$(python3 - "$PREPARED" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["prediction_id"])
PY
)
PAPER_ORDER_ID=$(python3 - "$PREPARED" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["paper_order_id"])
PY
)

[[ "$PREDICTION_ID" =~ ^[0-9a-f]{64}$ ]] || fail "prediction_id_invalid"
[[ "$PAPER_ORDER_ID" =~ ^[0-9a-f]{64}$ ]] || fail "paper_order_id_invalid"

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

python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY' || fail "executor_not_safe_idle"
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["executor_sha256"] == sys.argv[2]
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

MARKER=/var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="sudo test ! -e '$MARKER'" ||
  fail "second_canary_network_attempt_marker_present"

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

PRECHECK=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' CANARY_INTENT_ID='$EXPECTED_INTENT' CANARY_PREDICTION_ID='$PREDICTION_ID' CANARY_PAPER_ORDER_ID='$PAPER_ORDER_ID' /opt/bp/.venv/bin/python -" <<'PY'
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
        submission = [
            row for row in events
            if row["event_type"] in {"accepted", "rejected", "submission_unknown"}
        ]
        closed = [row for row in events if row["event_type"] == "closed_before_submission"]
        assert not submission
        print(json.dumps({
            "events": events,
            "submission_attempt_event_count": len(submission),
            "closed_before_submission_count": len(closed),
            "database_read_only": True,
        }, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
) || fail "database_precheck_failed"

printf '%s\n' "$PRECHECK"

# Recheck the global attempt marker immediately before the database mutation.
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="sudo test ! -e '$MARKER'" ||
  fail "second_canary_network_attempt_marker_appeared_before_reconciliation"

RECONCILED=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_SOURCE_B64='$CANARY_SOURCE_B64' CANARY_HEALTH_B64='$HEALTH_B64' CANARY_INTENT_ID='$EXPECTED_INTENT' /opt/bp/.venv/bin/python -" <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime

from sqlalchemy import create_engine
from bp_engine.config import Settings

module = types.ModuleType("phase15_controlled_supervisor_canary_inline")
sys.modules[module.__name__] = module
source = base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(source, "<phase15_controlled_supervisor_canary_inline>", "exec"), module.__dict__)
health = json.loads(base64.b64decode(os.environ["CANARY_HEALTH_B64"]).decode("utf-8"))

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
try:
    report = module.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=os.environ["CANARY_INTENT_ID"],
        observed_at=datetime.now(UTC),
        reason="controlled_supervisor_candidate_expired_without_network_attempt",
        executor_health=health,
    )
    print(json.dumps(report, sort_keys=True, default=str))
finally:
    engine.dispose()
PY
) || fail "controlled_reconciliation_command_failed"

python3 - "$RECONCILED" "$EXPECTED_INTENT" <<'PY' || fail "controlled_reconciliation_result_invalid"
import json
import sys
payload = json.loads(sys.argv[1])
assert payload["status"] in {"reconciled", "already_reconciled"}
assert payload["intent_id"] == sys.argv[2]
assert payload["event_type"] == "closed_before_submission"
assert payload["submission_attempt_consumed"] is False
PY

printf '%s\n' "$RECONCILED"

POST_HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "post_reconciliation_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' || fail "post_reconciliation_executor_not_safe"
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

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="sudo test ! -e '$MARKER'" ||
  fail "second_canary_network_attempt_marker_created_during_reconciliation"

echo "INTENT_ID=$EXPECTED_INTENT"
echo "SUBMISSION_ATTEMPT_CONSUMED=false"
echo "REAL_ORDER_SUBMITTED=false"
echo "PHASE15_CONTROLLED_CANARY_RECONCILIATION=PASS"
