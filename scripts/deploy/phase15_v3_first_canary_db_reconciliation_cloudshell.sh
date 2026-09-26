#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"

fail() {
  echo "PHASE15_V3_FIRST_CANARY_DB_RECONCILIATION=FAIL" >&2
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

[[ "${PHASE15_ACCEPT_FIRST_CANARY_DB_RECONCILIATION:-no}" == "yes" ]] ||
  fail "first_canary_db_reconciliation_not_explicitly_accepted"

SELF_BLOB=$(git hash-object "$ROOT/scripts/deploy/phase15_v3_first_canary_db_reconciliation_cloudshell.sh")

read -r INTENT_ID ORDER_ID REQUESTED_SHARES EXPECTED_BLOB < <(
python3 - "$ROOT/PROJECT_STATE.json" "$SELF_BLOB" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
actual_blob = sys.argv[2]
gate = state["phase_15_v3_live_canary"]
first = gate["first_live_canary"]
second = gate["second_live_canary_authorization"]
repair = gate["post_submission_db_reconciliation_repair"]

assert state["source_of_truth_version"] == "0.14.180"
assert state["status"] == "PHASE_15_SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
assert gate["status"] == "SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
assert gate["live_trading_enabled"] is False
assert gate["canary_order_submitted"] is True
assert gate["second_order_authorized"] is True
assert gate["pending_unsubmitted_intent"] is None
assert first["status"] == "RECONCILED_ZERO_FILL"
assert first["network_submission_attempt_consumed"] is True
assert first["official_order_fill_reconciliation_status"] == "PASS_ZERO_FILL"
assert first["official_fill_state"] == "zero_fill_observed"
assert first["official_reconciliation_complete"] is True
assert first["confirmed_filled_shares"] == 0
assert first["confirmed_filled_notional_usd"] == 0
assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
assert second["requires_official_reconciliation_before_any_third_order"] is True
assert repair["status"] == "AUTHORIZED_READY"
assert repair["authorized"] is True
assert repair["authorization_consumed"] is False
assert repair["helper_git_blob_sha"] == actual_blob
assert repair["does_not_authorize_telegram_approve"] is True
assert repair["does_not_authorize_executor_arm_or_invoke"] is True
assert repair["does_not_authorize_order_submission"] is True
print(
    first["intent_id"],
    first["external_order_id"],
    first["requested_shares"],
    repair["helper_git_blob_sha"],
)
PY
) || fail "source_truth_not_authorized_for_exact_repair_helper"

[[ "$SELF_BLOB" == "$EXPECTED_BLOB" ]] || fail "repair_helper_blob_mismatch"

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" \
    --zone="$EXEC_ZONE" \
    --quiet \
    --command='sudo /opt/bp-canary/executor.sh') ||
  fail "executor_health_command_failed"

if ! python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY'
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
then
  fail "executor_not_safe_for_db_reconciliation"
fi

SNAPSHOT=$(gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" \
  --zone="$EXEC_ZONE" \
  --quiet \
  --command="sudo env CANARY_ORDER_ID='$ORDER_ID' CANARY_INTENT_ID='$INTENT_ID' CANARY_REQUESTED_SHARES='$REQUESTED_SHARES' bash -s" <<'REMOTE'
set -Eeuo pipefail
[[ -e /etc/bp-canary/KILL ]] || {
  echo '{"error":"kill_switch_not_engaged"}'
  exit 1
}
set -a
source /etc/bp-canary/live.env
set +a
exec /opt/bp-canary/.venv/bin/python - <<'PY'
from __future__ import annotations

import importlib.metadata
import json
import os
import time
from decimal import Decimal

import polymarket

order_id = os.environ["CANARY_ORDER_ID"]
intent_id = os.environ["CANARY_INTENT_ID"]
requested_shares = Decimal(os.environ["CANARY_REQUESTED_SHARES"])

if importlib.metadata.version("polymarket-client") != "0.7.1":
    raise SystemExit("unexpected polymarket-client version")

private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
if not private_key:
    raise SystemExit("private key missing")
kwargs = {"private_key": private_key}
if wallet:
    kwargs["wallet"] = wallet
client = polymarket.SecureClient.create(**kwargs)

def status_text(value: object) -> str:
    raw = getattr(value, "value", value)
    text = str(raw).upper()
    if text.startswith("TRADE_STATUS_"):
        text = text[len("TRADE_STATUS_"):]
    return text

def sample() -> dict[str, object]:
    open_orders = tuple(client.list_open_orders().iter_items())
    trades = tuple(client.list_account_trades().iter_items())
    matches = []
    for trade in trades:
        hit = None
        if str(trade.taker_order_id) == order_id:
            hit = {
                "shares": Decimal(str(trade.size)),
                "price": Decimal(str(trade.price)),
                "status": status_text(trade.status),
            }
        for maker in trade.maker_orders:
            if str(maker.order_id) == order_id:
                if hit is not None:
                    raise RuntimeError("order appears more than once in a single trade")
                hit = {
                    "shares": Decimal(str(maker.matched_amount)),
                    "price": Decimal(str(maker.price)),
                    "status": status_text(trade.status),
                }
        if hit is None:
            continue
        matches.append({
            "trade_id": str(trade.id),
            "shares": format(hit["shares"], "f"),
            "price": format(hit["price"], "f"),
            "status": hit["status"],
        })
    matches.sort(key=lambda row: row["trade_id"])
    open_ids = sorted(str(order.id) for order in open_orders)
    return {
        "order_still_open": order_id in open_ids,
        "open_order_count": len(open_ids),
        "matching_trades": matches,
    }

first = sample()
time.sleep(3)
second = sample()
stable = first == second
confirmed = [row for row in second["matching_trades"] if row["status"] == "CONFIRMED"]
nonfinal = [row for row in second["matching_trades"] if row["status"] != "CONFIRMED"]
confirmed_shares = sum((Decimal(row["shares"]) for row in confirmed), Decimal("0"))
confirmed_notional = sum(
    (Decimal(row["shares"]) * Decimal(row["price"]) for row in confirmed),
    Decimal("0"),
)
if confirmed_shares > requested_shares:
    raise RuntimeError("confirmed fill exceeds requested shares")

payload = {
    "intent_id": intent_id,
    "external_order_id": order_id,
    "sdk_version": importlib.metadata.version("polymarket-client"),
    "snapshot_stable_across_3_seconds": stable,
    "order_still_open": second["order_still_open"],
    "open_order_count": second["open_order_count"],
    "matching_trade_count": len(second["matching_trades"]),
    "confirmed_filled_shares": format(confirmed_shares, "f"),
    "confirmed_filled_notional_usd": format(confirmed_notional, "f"),
    "fill_state": (
        "order_still_open"
        if second["order_still_open"]
        else "fill_observed_not_final"
        if nonfinal
        else "confirmed_fill"
        if confirmed
        else "zero_fill_observed"
    ),
    "official_reconciliation_complete": (
        stable
        and not second["order_still_open"]
        and not nonfinal
    ),
    "read_only": True,
}
print(json.dumps(payload, sort_keys=True))
PY
REMOTE
) || fail "fresh_official_fill_probe_failed"

if ! python3 - "$SNAPSHOT" "$INTENT_ID" "$ORDER_ID" <<'PY'
import json
import sys
from decimal import Decimal

payload = json.loads(sys.argv[1])
assert payload["intent_id"] == sys.argv[2]
assert payload["external_order_id"] == sys.argv[3]
assert payload["sdk_version"] == "0.7.1"
assert payload["read_only"] is True
assert payload["snapshot_stable_across_3_seconds"] is True
assert payload["order_still_open"] is False
assert payload["open_order_count"] == 0
assert payload["matching_trade_count"] == 0
assert Decimal(payload["confirmed_filled_shares"]) == 0
assert Decimal(payload["confirmed_filled_notional_usd"]) == 0
assert payload["fill_state"] == "zero_fill_observed"
assert payload["official_reconciliation_complete"] is True
PY
then
  fail "fresh_official_zero_fill_not_confirmed"
fi

HEALTH_B64=$(printf '%s' "$HEALTH" | base64 -w0)
SNAPSHOT_B64=$(printf '%s' "$SNAPSHOT" | base64 -w0)

RECONCILED=$(gcloud compute ssh "$US_VM" \
  --project="$PROJECT" \
  --zone="$US_ZONE" \
  --quiet \
  --command="sudo -u bp env PYTHONPATH='$V3_RUNTIME/src' MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 CANARY_HEALTH_B64='$HEALTH_B64' CANARY_SNAPSHOT_B64='$SNAPSHOT_B64' CANARY_INTENT_ID='$INTENT_ID' CANARY_ORDER_ID='$ORDER_ID' /opt/bp/.venv/bin/python -" <<'PY'
from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

health = json.loads(base64.b64decode(os.environ["CANARY_HEALTH_B64"]).decode("utf-8"))
snapshot = json.loads(base64.b64decode(os.environ["CANARY_SNAPSHOT_B64"]).decode("utf-8"))
intent_id = os.environ["CANARY_INTENT_ID"]
order_id = os.environ["CANARY_ORDER_ID"]

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(settings.database_url, pool_pre_ping=True)
repository = LiveReadinessRepository()

try:
    with engine.begin() as connection:
        intent = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id == intent_id,
                schema.live_order_intents.c.policy_version == "v3-live-canary-v1",
            )
        ).mappings().one()

        events = connection.execute(
            select(schema.live_order_events)
            .where(schema.live_order_events.c.intent_id == intent_id)
            .order_by(schema.live_order_events.c.id)
        ).mappings().all()
        event_types = [str(row["event_type"]) for row in events]
        accepted = [row for row in events if row["event_type"] == "accepted"]
        cancelled = [row for row in events if row["event_type"] == "cancelled"]
        if len(accepted) != 1 or len(cancelled) != 1:
            raise RuntimeError("expected exactly one accepted and one cancelled event")
        if str(accepted[0]["external_order_id"]) != order_id:
            raise RuntimeError("accepted event external order mismatch")
        if str(cancelled[0]["external_order_id"]) != order_id:
            raise RuntimeError("cancelled event external order mismatch")

        existing = None
        for row in connection.execute(
            select(schema.live_reconciliation_runs)
            .order_by(
                schema.live_reconciliation_runs.c.observed_at.desc(),
                schema.live_reconciliation_runs.c.id.desc(),
            )
        ).mappings():
            evidence = dict(row["evidence"] or {})
            if (
                evidence.get("reconciliation_kind")
                == "post_submission_official_zero_fill"
                and evidence.get("intent_id") == intent_id
                and evidence.get("external_order_id") == order_id
            ):
                existing = row
                break

        if existing is not None:
            result = {
                "status": "already_reconciled",
                "intent_id": intent_id,
                "external_order_id": order_id,
                "reconciliation_id": str(existing["reconciliation_id"]),
                "unresolved_count": int(existing["unresolved_count"]),
                "critical_count": int(existing["critical_count"]),
            }
        else:
            collateral = Decimal(str(health["account"]["collateral_balance_usd"]))
            if int(health["account"]["open_order_count"]) != 0:
                raise RuntimeError("official open orders present")
            if collateral < Decimal("5"):
                raise RuntimeError("insufficient official collateral")
            if snapshot["fill_state"] != "zero_fill_observed":
                raise RuntimeError("fresh snapshot is not zero fill")
            if snapshot["official_reconciliation_complete"] is not True:
                raise RuntimeError("fresh snapshot reconciliation incomplete")

            observed = datetime.now(UTC)
            stored = repository.store_reconciliation_run(
                connection,
                observed_at=observed,
                unresolved_count=0,
                critical_count=0,
                evidence={
                    "source": "phase15_v3_first_live_canary_official_zero_fill_ledger_repair",
                    "phase": "phase15_v3_live_canary_v1",
                    "reconciliation_kind": "post_submission_official_zero_fill",
                    "intent_id": intent_id,
                    "external_order_id": order_id,
                    "official_open_order_count": 0,
                    "collateral_balance_usd": str(collateral),
                    "confirmed_filled_shares": "0",
                    "confirmed_filled_notional_usd": "0",
                    "official_fill_state": "zero_fill_observed",
                    "network_submission_attempt_consumed": True,
                    "accepted_event_present": True,
                    "cancelled_event_present": True,
                    "account_snapshot": {
                        "total_exposure_usd": "0",
                        "realized_daily_pnl_usd": "0",
                        "consecutive_losses": 0,
                    },
                },
            )
            record = stored.record
            result = {
                "status": "reconciled",
                "intent_id": intent_id,
                "external_order_id": order_id,
                "reconciliation_id": str(record["reconciliation_id"]),
                "unresolved_count": int(record["unresolved_count"]),
                "critical_count": int(record["critical_count"]),
                "event_types": event_types,
            }

        print(json.dumps(result, sort_keys=True))
finally:
    engine.dispose()
PY
) || fail "db_reconciliation_write_failed"

if ! python3 - "$RECONCILED" "$INTENT_ID" "$ORDER_ID" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["status"] in ("reconciled", "already_reconciled")
assert payload["intent_id"] == sys.argv[2]
assert payload["external_order_id"] == sys.argv[3]
assert payload["unresolved_count"] == 0
assert payload["critical_count"] == 0
assert payload["reconciliation_id"].startswith("live-reconciliation-")
PY
then
  fail "db_reconciliation_result_invalid"
fi

echo "$HEALTH"
echo "$SNAPSHOT"
echo "$RECONCILED"
echo "TELEGRAM_APPROVAL_PERFORMED=false"
echo "EXECUTOR_ARMED=false"
echo "EXECUTOR_INVOKED=false"
echo "ORDER_SUBMISSION_PERFORMED=false"
echo "NETWORK_SUBMISSION_ATTEMPT_CONSUMED_BY_REPAIR=false"
echo "PHASE15_V3_FIRST_CANARY_DB_RECONCILIATION=PASS"
