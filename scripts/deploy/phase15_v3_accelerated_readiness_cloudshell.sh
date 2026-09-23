#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_V3_READINESS_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_V3_READINESS_ZONE:-us-east1-c}"
VM="${PHASE15_V3_READINESS_VM:-bp-recorder}"

fail_local() {
  echo "PHASE15_V3_ACCELERATED_READINESS=FAIL" >&2
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
command -v python3 >/dev/null 2>&1 || fail_local "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || fail_local "gcloud_auth_missing"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' \
  || fail_local "local_source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state.get("phase_15_v3_canary_readiness") or {}
assert state.get("source_of_truth_version") == "0.14.178"
assert gate.get("status") == "ACCELERATED_READINESS_ENGINEERING_NOT_RUN"
assert gate.get("statistical_rules_frozen_before_new_reliability_audit") is True
assert gate.get("accelerated_audit_run_performed") is False
assert gate.get("live_trading_enabled") is False
assert gate.get("max_trade_size_usd") == 0
assert gate.get("max_daily_loss_usd") == 0
assert gate.get("real_money_mutation_performed") is False
PY

REPORT_B64=$(base64 -w0 "$ROOT/src/bp_engine/v3_live_gate/report.py")
CALIBRATION_B64=$(base64 -w0 "$ROOT/src/bp_engine/v3_live_gate/calibration_audit.py")
ACCELERATED_B64=$(base64 -w0 "$ROOT/src/bp_engine/v3_live_gate/accelerated.py")

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

fail() {
  echo "PHASE15_V3_ACCELERATED_READINESS=FAIL" >&2
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
CALIBRATION_SOURCE_B64="__CALIBRATION_B64__"
ACCELERATED_SOURCE_B64="__ACCELERATED_B64__"

sudo -u bp env \
  PYTHONPATH="$V3_CURRENT/src" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  REPORT_SOURCE_B64="$REPORT_SOURCE_B64" \
  CALIBRATION_SOURCE_B64="$CALIBRATION_SOURCE_B64" \
  ACCELERATED_SOURCE_B64="$ACCELERATED_SOURCE_B64" \
  "$REPO/.venv/bin/python" - <<'PY'
import base64
import json
import os
import sys
import types
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, or_, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)

if V3_PAPER_EXECUTION_VERSION != "paper-execution-v3-frozen-v1":
    raise SystemExit("unexpected V3 execution identity")
if V3_PAPER_PREDICTION_VERSION != "v3-frozen-paper-v1":
    raise SystemExit("unexpected V3 prediction identity")

def load_inline(name, env_key):
    module = types.ModuleType(name)
    sys.modules[name] = module
    source = base64.b64decode(os.environ[env_key]).decode("utf-8")
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module

report_module = load_inline("v3_live_report_inline", "REPORT_SOURCE_B64")
calibration_module = load_inline(
    "v3_calibration_audit_inline",
    "CALIBRATION_SOURCE_B64",
)
accelerated_module = load_inline(
    "v3_accelerated_readiness_inline",
    "ACCELERATED_SOURCE_B64",
)

settings = Settings(_env_file="/etc/bp/bp.env")
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
        if connection.execute(
            text("SHOW default_transaction_read_only")
        ).scalar_one() != "on":
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

        settlements = []
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

        evaluation_rows = [
            dict(row)
            for row in connection.execute(
                select(
                    schema.live_prediction_evaluations,
                    schema.live_predictions.c.calibrated_probability.label(
                        "prospective_probability"
                    ),
                )
                .select_from(
                    schema.live_predictions.join(
                        schema.live_prediction_evaluations,
                        schema.live_predictions.c.prediction_id
                        == schema.live_prediction_evaluations.c.prediction_id,
                    )
                )
                .where(
                    schema.live_predictions.c.prediction_version
                    == V3_PAPER_PREDICTION_VERSION,
                    schema.live_prediction_evaluations.c.label_version
                    == "official-outcome-v1",
                )
                .order_by(
                    schema.live_prediction_evaluations.c.evaluated_at,
                    schema.live_prediction_evaluations.c.id,
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
finally:
    engine.dispose()

latest_settlements = latest(settlements, "paper_order_id", "settled_at")
latest_evaluations = latest(evaluation_rows, "prediction_id", "evaluated_at")
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
v3_report = report_module.build_v3_live_gate_report(
    settlements=latest_settlements,
    evaluations=latest_evaluations,
    reconciliation=reconciliation,
    user_authorized=True,
    bootstrap_seed=14,
    bootstrap_resamples=10_000,
)

points = [
    calibration_module.CalibrationPoint(
        probability=float(row["prospective_probability"]),
        target=int(row["official_target"]),
    )
    for row in latest_evaluations
]
calibration_audit = calibration_module.build_calibration_audit(
    points,
    bootstrap_seed=15,
    bootstrap_resamples=2_000,
)

frozen_selection = {
    "ordinary_validation_economics_passed": True,
    "ordinary_fold_count": 5,
    "ordinary_validation_gate": (
        "min 8 validation trades/fold; >=4/5 nonnegative validation folds; "
        "positive aggregate validation P&L"
    ),
    "selection_sha256": (
        "a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508"
    ),
    "holdout_after_cost_pnl": 1.654224,
    "holdout_evidence_sha256": (
        "a093de346cde6a98cf056bdbc3f7c901c3570aff611363cda996a04fa8e4d66a"
    ),
}
result = accelerated_module.build_accelerated_v3_readiness(
    v3_report=v3_report,
    calibration_audit=calibration_audit,
    frozen_selection=frozen_selection,
)
output = {
    "generated_at": datetime.now(UTC).isoformat(),
    "mode": "phase15_v3_accelerated_readiness_read_only",
    "result": result,
    "safety": {
        "database_session_read_only": True,
        "production_checkout_mutation": False,
        "production_files_created": False,
        "service_or_timer_mutation": False,
        "wallet_or_signing_material_read": False,
        "authenticated_trading_client_constructed": False,
        "real_order_submission_attempted": False,
        "real_money_mutation_performed": False,
        "live_trading_enabled": False,
        "phase15_activation_performed": False,
    },
}
print(json.dumps(output, indent=2, sort_keys=True, default=str))
PY

echo "PHASE15_V3_ACCELERATED_READINESS=PASS" >&2
REMOTE

REMOTE_SCRIPT=${REMOTE_SCRIPT/__REPORT_B64__/$REPORT_B64}
REMOTE_SCRIPT=${REMOTE_SCRIPT/__CALIBRATION_B64__/$CALIBRATION_B64}
REMOTE_SCRIPT=${REMOTE_SCRIPT/__ACCELERATED_B64__/$ACCELERATED_B64}
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "LOCAL_MAIN=$LOCAL_HEAD"
echo "This helper is read-only. It cannot enable trading or access wallet/signing material."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
