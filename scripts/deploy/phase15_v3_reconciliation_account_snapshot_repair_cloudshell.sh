#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
ACCEPT="${PHASE15_ACCEPT_RECONCILIATION_ACCOUNT_SNAPSHOT_REPAIR:-}"

fail() {
  echo "PHASE15_RECONCILIATION_ACCOUNT_SNAPSHOT_REPAIR=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] || fail "explicit_reconciliation_account_snapshot_repair_authorization_required"

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

HELPER_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_reconciliation_account_snapshot_repair_cloudshell.sh")
CANARY_BLOB=$(git hash-object "$ROOT/src/bp_engine/execution/canary.py")
LIVE_BLOB=$(git hash-object "$ROOT/src/bp_engine/execution/live.py")

CONFIG=$(
python3 - "$ROOT/PROJECT_STATE.json" "$HELPER_BLOB" "$CANARY_BLOB" "$LIVE_BLOB" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
repair = gate["reconciliation_account_snapshot_repair"]
second = gate["second_live_canary_authorization"]
controlled = gate["controlled_auto_approved_canary_authorization"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
assert second["max_network_submission_attempts"] == 1
assert controlled["consumed"] is False
assert controlled["network_attempt_observed"] is False
assert controlled["real_order_submission_observed"] is False
assert repair["status"] == "AUTHORIZED_NOT_EXECUTED"
assert repair["authorized"] is True
assert repair["production_mutation_performed"] is False
assert repair["additional_network_attempts_authorized"] is False
assert repair["order_submission_authorized"] is False
assert repair["helper_git_blob_sha"] == sys.argv[2]
assert repair["canary_git_blob_sha"] == sys.argv[3]
assert repair["live_git_blob_sha"] == sys.argv[4]

print("TARGET_INTENT_ID=" + repair["target_terminal_intent_id"])
print("EXPECTED_TERMINAL_RECONCILIATION_ID=" + repair["expected_terminal_reconciliation_id"])
print("EXPECTED_ACCOUNT_SNAPSHOT_SOURCE_RECONCILIATION_ID=" + repair["expected_account_snapshot_source_reconciliation_id"])
PY
) || fail "source_truth_repair_authorization_invalid"

eval "$CONFIG"

[[ "$TARGET_INTENT_ID" =~ ^live-intent-[0-9a-f]{32}$ ]] || fail "target_intent_invalid"
[[ "$EXPECTED_TERMINAL_RECONCILIATION_ID" =~ ^live-reconciliation-[0-9a-f]{32}$ ]] ||
  fail "terminal_reconciliation_id_invalid"
[[ "$EXPECTED_ACCOUNT_SNAPSHOT_SOURCE_RECONCILIATION_ID" =~ ^live-reconciliation-[0-9a-f]{32}$ ]] ||
  fail "snapshot_source_reconciliation_id_invalid"

MARKER=/var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json

HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "executor_health_command_failed"

python3 - "$HEALTH" <<'PY' || fail "executor_not_safe_idle"
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
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

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="sudo test ! -e '$MARKER'" ||
  fail "second_canary_network_attempt_marker_present"

HEALTH_B64=$(python3 - "$HEALTH" <<'PY'
import base64
import sys
print(base64.b64encode(sys.argv[1].encode("utf-8")).decode("ascii"), end="")
PY
)
LIVE_SOURCE_B64=$(python3 - "$ROOT/src/bp_engine/execution/live.py" <<'PY'
import base64
import sys
from pathlib import Path
print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii"), end="")
PY
)
CANARY_SOURCE_B64=$(python3 - "$ROOT/src/bp_engine/execution/canary.py" <<'PY'
import base64
import sys
from pathlib import Path
print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode("ascii"), end="")
PY
)

RESULT=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' TARGET_INTENT_ID='$TARGET_INTENT_ID' EXPECTED_TERMINAL_RECONCILIATION_ID='$EXPECTED_TERMINAL_RECONCILIATION_ID' EXPECTED_ACCOUNT_SNAPSHOT_SOURCE_RECONCILIATION_ID='$EXPECTED_ACCOUNT_SNAPSHOT_SOURCE_RECONCILIATION_ID' EXECUTOR_HEALTH_B64='$HEALTH_B64' LIVE_SOURCE_B64='$LIVE_SOURCE_B64' CANARY_SOURCE_B64='$CANARY_SOURCE_B64' /opt/bp/.venv/bin/python -" <<'PY'
from __future__ import annotations

import base64
import json
import os
import sys
import types
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select

import bp_engine.execution as execution_package
from bp_engine.config import Settings
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

live_module = types.ModuleType("bp_engine.execution.live")
live_module.__package__ = "bp_engine.execution"
sys.modules[live_module.__name__] = live_module
execution_package.live = live_module
live_source = base64.b64decode(os.environ["LIVE_SOURCE_B64"]).decode("utf-8")
exec(compile(live_source, "<phase15_repair_live_inline>", "exec"), live_module.__dict__)

canary = types.ModuleType("phase15_repair_canary_inline")
sys.modules[canary.__name__] = canary
canary_source = base64.b64decode(os.environ["CANARY_SOURCE_B64"]).decode("utf-8")
exec(compile(canary_source, "<phase15_repair_canary_inline>", "exec"), canary.__dict__)
_account_snapshot = live_module._account_snapshot

target_intent_id = os.environ["TARGET_INTENT_ID"]
expected_terminal_reconciliation_id = os.environ["EXPECTED_TERMINAL_RECONCILIATION_ID"]
expected_snapshot_source_id = os.environ["EXPECTED_ACCOUNT_SNAPSHOT_SOURCE_RECONCILIATION_ID"]
health = json.loads(base64.b64decode(os.environ["EXECUTOR_HEALTH_B64"]).decode("utf-8"))

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
repository = LiveReadinessRepository()

try:
    with engine.begin() as connection:
        intent = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id == target_intent_id,
                schema.live_order_intents.c.policy_version == canary.CANARY_POLICY_VERSION,
            )
        ).mappings().one()

        terminal_event = connection.execute(
            select(schema.live_order_events.c.event_type)
            .where(
                schema.live_order_events.c.intent_id == target_intent_id,
                schema.live_order_events.c.event_type
                == canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT,
            )
            .order_by(schema.live_order_events.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        assert terminal_event == canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT

        target_attempt = connection.execute(
            select(schema.live_order_events.c.event_type)
            .where(
                schema.live_order_events.c.intent_id == target_intent_id,
                schema.live_order_events.c.event_type.in_(
                    canary.CANARY_SUBMISSION_ATTEMPT_EVENTS
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
        assert target_attempt is None
        assert canary._submission_attempt_count(connection) == 1

        reconciliations = list(
            connection.execute(
                select(schema.live_reconciliation_runs)
                .order_by(
                    schema.live_reconciliation_runs.c.observed_at.desc(),
                    schema.live_reconciliation_runs.c.id.desc(),
                )
            ).mappings()
        )
        assert reconciliations
        latest = reconciliations[0]
        assert str(latest["reconciliation_id"]) == expected_terminal_reconciliation_id
        assert int(latest["unresolved_count"]) == 0
        assert int(latest["critical_count"]) == 0
        latest_evidence = dict(latest["evidence"] or {})
        assert latest_evidence.get("reconciliation_kind") == "pre_submission_intent_close"
        assert latest_evidence.get("intent_id") == target_intent_id
        assert latest_evidence.get("submission_attempt_consumed") is False
        assert "account_snapshot" not in latest_evidence

        source = next(
            row
            for row in reconciliations
            if str(row["reconciliation_id"]) == expected_snapshot_source_id
        )
        assert int(source["unresolved_count"]) == 0
        assert int(source["critical_count"]) == 0
        source_evidence = dict(source["evidence"] or {})
        raw_account = source_evidence.get("account_snapshot")
        assert isinstance(raw_account, dict)
        account_snapshot = {
            "total_exposure_usd": str(
                Decimal(str(raw_account["total_exposure_usd"]))
            ),
            "realized_daily_pnl_usd": str(
                Decimal(str(raw_account["realized_daily_pnl_usd"]))
            ),
            "consecutive_losses": int(raw_account["consecutive_losses"]),
        }
        assert Decimal(account_snapshot["total_exposure_usd"]) >= 0
        assert account_snapshot["consecutive_losses"] >= 0

        before = _account_snapshot(connection, observed_at=datetime.now(UTC))
        assert before.unresolved_critical_reconciliation == 1

        unresolved_intents = []
        for candidate in connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.policy_version
                == canary.CANARY_POLICY_VERSION
            )
        ).mappings():
            terminal = connection.execute(
                select(schema.live_order_events.c.event_type)
                .where(
                    schema.live_order_events.c.intent_id == candidate["intent_id"],
                    schema.live_order_events.c.event_type.in_(
                        canary.CANARY_INTENT_TERMINAL_EVENTS
                    ),
                )
                .order_by(schema.live_order_events.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if terminal is None:
                unresolved_intents.append(str(candidate["intent_id"]))
        assert unresolved_intents == []

        observed = datetime.now(UTC)
        stored = repository.store_reconciliation_run(
            connection,
            observed_at=observed,
            unresolved_count=0,
            critical_count=0,
            evidence={
                "source": "phase15_reconciliation_account_snapshot_carry_forward_repair",
                "phase": "phase15_v3_live_canary_v1",
                "reconciliation_kind": "account_snapshot_carry_forward_repair",
                "target_terminal_intent_id": target_intent_id,
                "repaired_from_reconciliation_id": expected_terminal_reconciliation_id,
                "account_snapshot_carried_from_reconciliation_id": (
                    expected_snapshot_source_id
                ),
                "submission_attempt_consumed_by_repair": False,
                "network_submission_attempt_consumed_by_repair": False,
                "official_open_order_count": 0,
                "collateral_balance_usd": str(
                    Decimal(str(health["account"]["collateral_balance_usd"]))
                ),
                "account_snapshot": account_snapshot,
            },
        )

        after = _account_snapshot(connection, observed_at=observed)
        assert after.unresolved_critical_reconciliation == 0

        print(
            json.dumps(
                {
                    "status": "repaired",
                    "target_intent_id": target_intent_id,
                    "new_reconciliation_id": str(stored.record["reconciliation_id"]),
                    "carried_from_reconciliation_id": expected_snapshot_source_id,
                    "before_unresolved_critical_reconciliation": (
                        before.unresolved_critical_reconciliation
                    ),
                    "after_unresolved_critical_reconciliation": (
                        after.unresolved_critical_reconciliation
                    ),
                    "submission_attempt_consumed_by_repair": False,
                    "network_submission_attempt_consumed_by_repair": False,
                },
                sort_keys=True,
            )
        )
finally:
    engine.dispose()
PY
) || fail "database_repair_failed"

python3 - "$RESULT" "$TARGET_INTENT_ID" <<'PY' || fail "database_repair_result_invalid"
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["status"] == "repaired"
assert payload["target_intent_id"] == sys.argv[2]
assert payload["before_unresolved_critical_reconciliation"] == 1
assert payload["after_unresolved_critical_reconciliation"] == 0
assert payload["submission_attempt_consumed_by_repair"] is False
assert payload["network_submission_attempt_consumed_by_repair"] is False
PY

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="sudo test ! -e '$MARKER'" ||
  fail "second_canary_network_attempt_marker_created_during_repair"

POST_HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null) ||
  fail "post_repair_executor_health_failed"

python3 - "$POST_HEALTH" <<'PY' || fail "post_repair_executor_not_safe"
import json
import sys
payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
assert payload["account"]["open_order_count"] == 0
assert payload["account"]["clean_for_canary"] is True
PY

echo "$RESULT"
echo "ORDER_SUBMISSION_PERFORMED=false"
echo "NETWORK_SUBMISSION_ATTEMPT_CONSUMED_BY_REPAIR=false"
echo "PHASE15_RECONCILIATION_ACCOUNT_SNAPSHOT_REPAIR=PASS"
