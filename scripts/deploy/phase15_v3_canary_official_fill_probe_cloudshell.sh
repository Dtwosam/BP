#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"

fail() {
  echo "PHASE15_V3_CANARY_OFFICIAL_FILL_PROBE=FAIL" >&2
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

read -r INTENT_ID ORDER_ID REQUESTED_SHARES < <(
python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate=state["phase_15_v3_live_canary"]
first=gate["first_live_canary"]

assert state["source_of_truth_version"] == "0.14.180"
assert state["status"] == "PHASE_15_FIRST_LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED"
assert gate["status"] == "LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED"
assert gate["canary_order_submitted"] is True
assert gate["max_submission_attempts"] == 1
assert gate["second_order_authorized"] is False
assert first["status"] == "SUBMITTED_AND_RECORDED_RECONCILIATION_PENDING"
assert first["network_submission_attempt_consumed"] is True
assert first["retry_authorized"] is False
assert first["second_order_authorized"] is False
assert first["official_order_fill_reconciliation_required"] is True
assert first["official_order_fill_reconciliation_status"] == "PENDING"
print(first["intent_id"], first["external_order_id"], first["requested_shares"])
PY
) || fail "source_truth_not_pending_first_canary_reconciliation"

EXECUTOR_SHA256=$(python3 - "$ROOT/scripts/deploy/phase15_v3_canary_executor.py" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

HEALTH=$(printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh') ||
  fail "executor_health_command_failed"

if ! python3 - "$HEALTH" "$EXECUTOR_SHA256" <<'PY'
import json
import sys

payload=json.loads(sys.argv[1])
expected_executor_sha256=sys.argv[2]
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["executor_sha256"] == expected_executor_sha256
assert payload["account"]["open_order_count"] == 0
assert payload["account"]["clean_for_canary"] is True
assert payload["kill_switch_engaged"] is True
assert payload["submission_ready"] is False
PY
then
  fail "executor_not_safe_for_readonly_reconciliation"
fi

SNAPSHOT=$(gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="sudo env CANARY_ORDER_ID='$ORDER_ID' CANARY_INTENT_ID='$INTENT_ID' CANARY_REQUESTED_SHARES='$REQUESTED_SHARES' bash -s" <<'REMOTE'
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

order_id=os.environ["CANARY_ORDER_ID"]
intent_id=os.environ["CANARY_INTENT_ID"]
requested_shares=Decimal(os.environ["CANARY_REQUESTED_SHARES"])

if importlib.metadata.version("polymarket-client") != "0.7.1":
    raise SystemExit("unexpected polymarket-client version")

private_key=os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
wallet=os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
if not private_key:
    raise SystemExit("private key missing")
kwargs={"private_key": private_key}
if wallet:
    kwargs["wallet"]=wallet
client=polymarket.SecureClient.create(**kwargs)

def status_text(value: object) -> str:
    raw=getattr(value, "value", value)
    text=str(raw).upper()
    if text.startswith("TRADE_STATUS_"):
        text=text[len("TRADE_STATUS_"):]
    return text

def side_text(value: object) -> str:
    return str(getattr(value, "value", value)).upper()

def sample() -> dict[str, object]:
    open_orders=tuple(client.list_open_orders().iter_items())
    trades=tuple(client.list_account_trades().iter_items())
    matches=[]

    for trade in trades:
        hits=[]
        if str(trade.taker_order_id) == order_id:
            hits.append({
                "role":"TAKER",
                "shares":Decimal(str(trade.size)),
                "price":Decimal(str(trade.price)),
                "side":side_text(trade.side),
            })
        for maker in trade.maker_orders:
            if str(maker.order_id) == order_id:
                hits.append({
                    "role":"MAKER",
                    "shares":Decimal(str(maker.matched_amount)),
                    "price":Decimal(str(maker.price)),
                    "side":side_text(maker.side),
                })
        if len(hits) > 1:
            raise RuntimeError("order appears more than once in a single trade")
        if not hits:
            continue
        hit=hits[0]
        matches.append({
            "trade_id":str(trade.id),
            "role":hit["role"],
            "side":hit["side"],
            "shares":format(hit["shares"], "f"),
            "price":format(hit["price"], "f"),
            "status":status_text(trade.status),
            "matched_at":trade.matched_at.isoformat(),
            "updated_at":trade.updated_at.isoformat(),
            "transaction_hash":str(trade.transaction_hash),
        })

    matches.sort(key=lambda row:(row["matched_at"], row["trade_id"]))
    open_ids=sorted(str(order.id) for order in open_orders)
    return {
        "order_still_open": order_id in open_ids,
        "open_order_count": len(open_ids),
        "matching_trades": matches,
    }

first=sample()
time.sleep(3)
second=sample()
stable=(first == second)

confirmed=[row for row in second["matching_trades"] if row["status"] == "CONFIRMED"]
nonfinal=[row for row in second["matching_trades"] if row["status"] != "CONFIRMED"]

confirmed_shares=sum((Decimal(row["shares"]) for row in confirmed), Decimal("0"))
confirmed_notional=sum(
    (Decimal(row["shares"])*Decimal(row["price"]) for row in confirmed),
    Decimal("0"),
)
observed_shares=sum(
    (Decimal(row["shares"]) for row in second["matching_trades"]),
    Decimal("0"),
)
observed_notional=sum(
    (Decimal(row["shares"])*Decimal(row["price"]) for row in second["matching_trades"]),
    Decimal("0"),
)

if confirmed_shares > requested_shares:
    raise RuntimeError("confirmed fill exceeds requested shares")

if second["order_still_open"]:
    fill_state="order_still_open"
    complete=False
elif nonfinal:
    fill_state="fill_observed_not_final"
    complete=False
elif confirmed:
    fill_state="confirmed_fill"
    complete=stable
else:
    fill_state="zero_fill_observed"
    complete=stable

payload={
    "intent_id":intent_id,
    "external_order_id":order_id,
    "sdk_version":importlib.metadata.version("polymarket-client"),
    "snapshot_stable_across_3_seconds":stable,
    "order_still_open":second["order_still_open"],
    "open_order_count":second["open_order_count"],
    "matching_trade_count":len(second["matching_trades"]),
    "matching_trades":second["matching_trades"],
    "observed_matched_shares":format(observed_shares, "f"),
    "observed_matched_notional_usd":format(observed_notional, "f"),
    "confirmed_filled_shares":format(confirmed_shares, "f"),
    "confirmed_filled_notional_usd":format(confirmed_notional, "f"),
    "confirmed_fill_fraction_of_requested":(
        format(confirmed_shares/requested_shares, "f") if requested_shares else None
    ),
    "fill_state":fill_state,
    "official_reconciliation_complete":complete,
    "read_only":True,
}
print(json.dumps(payload,sort_keys=True))
PY
REMOTE
) || fail "official_fill_probe_command_failed"

if ! python3 - "$SNAPSHOT" "$INTENT_ID" "$ORDER_ID" <<'PY'
import json
import sys

payload=json.loads(sys.argv[1])
assert payload["intent_id"] == sys.argv[2]
assert payload["external_order_id"] == sys.argv[3]
assert payload["sdk_version"] == "0.7.1"
assert payload["read_only"] is True
assert isinstance(payload["official_reconciliation_complete"], bool)
assert payload["fill_state"] in {
    "zero_fill_observed",
    "confirmed_fill",
    "fill_observed_not_final",
    "order_still_open",
}
PY
then
  fail "official_fill_probe_result_invalid"
fi

echo "$HEALTH"
echo "$SNAPSHOT"
echo "NETWORK_SUBMISSION_ATTEMPT_CONSUMED=true"
echo "SECOND_ORDER_AUTHORIZED=false"
echo "NO_ORDER_MUTATION_PERFORMED=true"

COMPLETE=$(python3 - "$SNAPSHOT" <<'PY'
import json,sys
print("true" if json.loads(sys.argv[1])["official_reconciliation_complete"] else "false")
PY
)

if [[ "$COMPLETE" == "true" ]]; then
  echo "PHASE15_V3_CANARY_OFFICIAL_FILL_PROBE=PASS"
else
  echo "PHASE15_V3_CANARY_OFFICIAL_FILL_PROBE=PENDING"
fi
