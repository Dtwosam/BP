#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V3_LIVE_GATE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V3_LIVE_GATE_ZONE:-us-east1-c}"
VM="${PHASE14_V3_LIVE_GATE_VM:-bp-recorder}"

fail_local() {
  echo "PHASE14_V3_LIVE_GATE_REASSESSMENT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail_local "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || fail_local "gcloud_auth_missing"

"$ROOT/.venv/bin/python" - "$ROOT/PROJECT_STATE.json" <<'PY' \
  || fail_local "local_source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state.get("phase_14_v3_live_gate_reassessment") or {}
assert state.get("source_of_truth_version") == "0.14.176"
assert gate.get("explicit_user_live_authorization") == "pass"
assert gate.get("live_trading_enabled") is False
assert gate.get("max_trade_size_usd") == 0
assert gate.get("max_daily_loss_usd") == 0
assert gate.get("real_money_mutation_performed") is False
PY

REPORT_B64=$(base64 -w0 "$ROOT/src/bp_engine/v3_live_gate/report.py")

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EXPECTED_DEPLOYED_HEAD="52b4355d6f077373b873f7a6f42bc37a20ddbc7b"
EXPECTED_V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
EXPECTED_V4_RUNTIME="/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574"
V3_CURRENT=/var/lib/bp/runtime/v3-paper-current
V4_CURRENT=/var/lib/bp/runtime/v4-forward-current
GEOBLOCK_URL="https://polymarket.com/api/geoblock"

fail() {
  echo "PHASE14_V3_LIVE_GATE_REASSESSMENT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -r "$SAFETY_FILE" ]] || fail "safety_file_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$EXPECTED_DEPLOYED_HEAD" ]] \
  || fail "unexpected_deployed_head"
[[ "$(readlink -f "$V3_CURRENT")" == "$EXPECTED_V3_RUNTIME" ]] \
  || fail "unexpected_v3_runtime"
[[ "$(readlink -f "$V4_CURRENT")" == "$EXPECTED_V4_RUNTIME" ]] \
  || fail "unexpected_v4_runtime"

for unit in \
  bp-postgres.service \
  bp-recorder.service \
  bp-v3-frozen-predictor.service \
  bp-v3-paper-execution.service \
  bp-storage-maintenance.timer \
  bp-storage-disk-health.timer \
  bp-v4-forward-coverage.timer
do
  systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
done

MODE=$(read_env "$ENV_FILE" MODE)
LIVE=$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)
TRADE=$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)
LOSS=$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)
SAFE_MODE=$(read_env "$SAFETY_FILE" MODE)
SAFE_LIVE=$(read_env "$SAFETY_FILE" LIVE_TRADING_ENABLED)
SAFE_TRADE=$(read_env "$SAFETY_FILE" MAX_TRADE_SIZE_USD)
SAFE_LOSS=$(read_env "$SAFETY_FILE" MAX_DAILY_LOSS_USD)

[[ "$MODE" == "research" && "$SAFE_MODE" == "research" ]] || fail "mode_not_research"
[[ "$LIVE" == "false" && "$SAFE_LIVE" == "false" ]] || fail "live_trading_not_false"
[[ "$TRADE" == "0" && "$SAFE_TRADE" == "0" ]] || fail "max_trade_size_not_zero"
[[ "$LOSS" == "0" && "$SAFE_LOSS" == "0" ]] || fail "max_daily_loss_not_zero"

REPORT_SOURCE_B64="__REPORT_B64__"

sudo -u bp env \
  PYTHONPATH="$V3_CURRENT/src" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  REPORT_SOURCE_B64="$REPORT_SOURCE_B64" \
  GEOBLOCK_URL="$GEOBLOCK_URL" \
  "$REPO/.venv/bin/python" - <<'PY'
import base64
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import create_engine, func, or_, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)

report_source = base64.b64decode(os.environ["REPORT_SOURCE_B64"]).decode("utf-8")
namespace = {}
exec(compile(report_source, "<v3-live-gate-report>", "exec"), namespace)
build_report = namespace["build_v3_live_gate_report"]

settings = Settings(_env_file="/etc/bp/bp.env")
expected_url = "https://polymarket.com/api/geoblock"
if os.environ["GEOBLOCK_URL"] != expected_url:
    raise SystemExit("unexpected geoblock URL")
if settings.polymarket_geoblock_url != expected_url:
    raise SystemExit("configured geoblock URL is not the frozen direct endpoint")

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)

def latest(rows, key, timestamp):
    result = {}
    for row in rows:
        identity = str(row[key])
        existing = result.get(identity)
        if existing is None or row[timestamp] > existing[timestamp]:
            result[identity] = row
    return sorted(result.values(), key=lambda row: (row[timestamp], str(row[key])))

try:
    with engine.connect() as connection:
        mode = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
        if mode != "on":
            raise SystemExit("database session is not read-only")

        orders = [
            dict(row)
            for row in connection.execute(
                select(schema.paper_orders).where(
                    schema.paper_orders.c.execution_version
                    == V3_PAPER_EXECUTION_VERSION
                )
            ).mappings()
        ]
        order_ids = tuple(str(row["paper_order_id"]) for row in orders)
        prediction_ids = tuple(str(row["prediction_id"]) for row in orders)

        settlements = []
        evaluations = []
        fills = []
        if order_ids:
            settlements = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_settlements).where(
                        schema.paper_settlements.c.paper_order_id.in_(order_ids)
                    )
                ).mappings()
            ]
            fills = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_fills).where(
                        schema.paper_fills.c.paper_order_id.in_(order_ids)
                    )
                ).mappings()
            ]
        if prediction_ids:
            evaluations = [
                dict(row)
                for row in connection.execute(
                    select(schema.live_prediction_evaluations).where(
                        schema.live_prediction_evaluations.c.prediction_id.in_(
                            prediction_ids
                        )
                    )
                ).mappings()
            ]

        joined = schema.paper_orders.outerjoin(
            schema.live_predictions,
            schema.paper_orders.c.prediction_id
            == schema.live_predictions.c.prediction_id,
        )
        invalid_sources = connection.scalar(
            select(func.count())
            .select_from(joined)
            .where(
                schema.paper_orders.c.execution_version
                == V3_PAPER_EXECUTION_VERSION,
                or_(
                    schema.live_predictions.c.prediction_id.is_(None),
                    schema.live_predictions.c.prediction_version
                    != V3_PAPER_PREDICTION_VERSION,
                ),
            )
        ) or 0

        live_ledger = {}
        for name in (
            "live_order_intents",
            "live_order_events",
            "live_risk_decisions",
            "live_reconciliation_runs",
        ):
            table = getattr(schema, name, None)
            if table is None:
                live_ledger[name] = None
            else:
                live_ledger[name] = int(
                    connection.scalar(select(func.count()).select_from(table)) or 0
                )
finally:
    engine.dispose()

latest_settlements = latest(settlements, "paper_order_id", "settled_at")
latest_evaluations = latest(evaluations, "prediction_id", "evaluated_at")
fills_by_order = {}
for fill in fills:
    fills_by_order.setdefault(str(fill["paper_order_id"]), []).append(fill)

violations = int(invalid_sources)
for settlement in latest_settlements:
    rows = fills_by_order.get(str(settlement["paper_order_id"]), [])
    shares = sum((row["shares"] for row in rows), Decimal("0"))
    cost = sum((row["total_cost"] for row in rows), Decimal("0"))
    if shares != settlement["filled_shares"] or cost != settlement["total_fill_cost"]:
        violations += 1

reconciliation = {
    "status": "OK" if violations == 0 else "VIOLATION",
    "violation_count": violations,
    "invalid_order_source_count": int(invalid_sources),
}
report = build_report(
    settlements=latest_settlements,
    evaluations=latest_evaluations,
    reconciliation=reconciliation,
    user_authorized=True,
)

try:
    response = httpx.get(expected_url, timeout=5.0, follow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code}")
    payload = response.json()
    if (
        not isinstance(payload, dict)
        or type(payload.get("blocked")) is not bool
        or not isinstance(payload.get("country"), str)
        or not isinstance(payload.get("region"), str)
    ):
        raise RuntimeError("invalid_schema")
    geoblock = {
        "status": "ok",
        "blocked": payload["blocked"],
        "country": payload["country"],
        "region": payload["region"],
        "checked_at": datetime.now(UTC).isoformat(),
        "direct_url": expected_url,
    }
except Exception as exc:
    geoblock = {
        "status": "error",
        "blocked": None,
        "error": type(exc).__name__,
        "checked_at": datetime.now(UTC).isoformat(),
        "direct_url": expected_url,
    }

try:
    import polymarket_client  # noqa: F401
    sdk_import_ok = True
except Exception:
    sdk_import_ok = False

runtime = {
    "mode": str(settings.mode.value),
    "live_trading_enabled": bool(settings.live_trading_enabled),
    "max_trade_size_usd": str(settings.max_trade_size_usd),
    "max_daily_loss_usd": str(settings.max_daily_loss_usd),
    "max_total_exposure_usd": str(settings.max_total_exposure_usd),
    "max_consecutive_losses": int(settings.max_consecutive_losses),
    "activation_manifest_present": Path(settings.live_activation_manifest_path).exists(),
    "kill_switch_engaged": Path(settings.live_kill_switch_path).exists(),
    "sdk_import_ok": sdk_import_ok,
    "live_order_ledger_counts": live_ledger,
}

output = {
    "generated_at": datetime.now(UTC).isoformat(),
    "mode": "phase14_v3_live_gate_reassessment_read_only",
    "runtime": runtime,
    "geographic_eligibility": geoblock,
    "v3": report,
    "frozen_holdout_reference": {
        "market_count": 144,
        "accuracy": 0.8402777777777778,
        "log_loss": 0.35419212970900277,
        "brier_score": 0.10943703117284813,
        "trade_count": 20,
        "realized_pnl_after_assumed_costs": 1.654224,
        "profit_factor": 1.5272848919547029,
        "evidence_sha256": "a093de346cde6a98cf056bdbc3f7c901c3570aff611363cda996a04fa8e4d66a",
    },
    "safety": {
        "database_session_read_only": True,
        "production_checkout_mutation": False,
        "production_files_created": False,
        "service_or_timer_mutation": False,
        "wallet_or_signing_material_read": False,
        "real_order_submission_attempted": False,
        "real_money_mutation_performed": False,
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "phase15_permitted": False,
    },
    "gate_boundary": {
        "master_live_gate_mutated": False,
        "automatic_promotion": False,
        "live_gate_eligible": False,
        "reason": (
            "Evidence collection only. A separate source-truth decision must assess "
            "every Master live-gate row before any live or nonzero-money change."
        ),
    },
}
print(json.dumps(output, indent=2, sort_keys=True, default=str))
PY

echo "PHASE14_V3_LIVE_GATE_REASSESSMENT=PASS" >&2
REMOTE

REMOTE_SCRIPT=${REMOTE_SCRIPT/__REPORT_B64__/$REPORT_B64}
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "LOCAL_MAIN=$LOCAL_HEAD"
echo "This helper is read-only and fail-closed. It never reads wallet/signing material or submits real orders."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
