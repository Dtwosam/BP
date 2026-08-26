#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="2026-08-24T00:00:00Z"
END="2026-08-25T00:00:00Z"
SOURCE_5M="phase8-300-efdf493067e9d56419afc4d88452bec6"
SOURCE_15M="phase8-900-64aaf2b1774ee7af37bd110b84b37ec1"
SOURCE_SEMANTIC_5M="efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93"
SOURCE_SEMANTIC_15M="64aaf2b1774ee7af37bd110b84b37ec19f85bdc875a283986d4dba16ae921828"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
HOST_PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase9-calibration-edge
ARTIFACT_ROOT=/var/lib/bp/artifacts/phase9-calibration-edge
VENV="/var/tmp/bp-phase9-venv-${EXPECTED_HEAD:0:12}-$$"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi

actual_head="${BP_VERIFIED_HEAD:-}"
if [[ -z "$actual_head" ]]; then
  echo "missing BP_VERIFIED_HEAD candidate provenance" >&2
  exit 2
fi
if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "expected HEAD $EXPECTED_HEAD but found $actual_head" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" ]]; then
  echo "missing production env or host Python" >&2
  exit 2
fi
if [[ ! -f "$REPO/migrations/0009_calibration_edge_runs.sql" ]]; then
  echo "missing Phase 9 calibration edge registry migration" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

psql_scalar() {
  local sql=$1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U bp -d bp -At -c "$sql" | tr -d '[:space:]'
}

live_trading=$(read_env LIVE_TRADING_ENABLED)
max_trade=$(read_env MAX_TRADE_SIZE_USD)
max_loss=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$live_trading" != "false" || "$max_trade" != "0" || "$max_loss" != "0" ]]; then
  echo "Phase 9 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

RECORDER_BEFORE=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_BEFORE" != "active" ]]; then
  echo "bp-recorder is not active before Phase 9 acceptance" >&2
  exit 4
fi

install -d -o bp -g bp "$EVIDENCE_ROOT" "$ARTIFACT_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

PRE_DISK=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" disk-health \
  --env-file "$ENV_FILE")
printf '%s\n' "$PRE_DISK" > "$run_dir/storage-disk-health-before.json"
read -r DISK_STATUS_BEFORE DISK_FREE_BYTES_BEFORE < <(
  printf '%s' "$PRE_DISK" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS_BEFORE" != "ok" ]]; then
  echo "disk status is $DISK_STATUS_BEFORE before Phase 9 acceptance" >&2
  exit 6
fi

cleanup() {
  rm -rf "$VENV" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo -u bp "$HOST_PY" -m venv "$VENV"
sudo -u bp "$VENV/bin/python" -m pip install --disable-pip-version-check "$REPO" \
  | tee "$run_dir/candidate-install.txt"
CANDIDATE_PY="$VENV/bin/python"

candidate_python() {
  sudo -u bp env PYTHONPATH="$REPO/src" "$CANDIDATE_PY" "$@"
}

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0009_calibration_edge_runs.sql" \
  > "$run_dir/migration.txt"

SOURCE_5M_COUNT=$(psql_scalar "SELECT count(*) FROM backtest_runs WHERE run_id = '$SOURCE_5M' AND horizon_seconds = 300 AND semantic_sha256 = '$SOURCE_SEMANTIC_5M' AND backtest_version = 'walk-forward-v1' AND dataset_version = 'supervised-core-v1' AND feature_version = 'core-v1' AND label_version = 'official-outcome-v1';")
SOURCE_15M_COUNT=$(psql_scalar "SELECT count(*) FROM backtest_runs WHERE run_id = '$SOURCE_15M' AND horizon_seconds = 900 AND semantic_sha256 = '$SOURCE_SEMANTIC_15M' AND backtest_version = 'walk-forward-v1' AND dataset_version = 'supervised-core-v1' AND feature_version = 'core-v1' AND label_version = 'official-outcome-v1';")
if [[ "$SOURCE_5M_COUNT" != "1" || "$SOURCE_15M_COUNT" != "1" ]]; then
  echo "accepted Phase 8 source backtests are missing or do not match immutable semantics" >&2
  exit 5
fi

candidate_python - "$REPO" <<'PY' | tee "$run_dir/edge-source-contract.txt"
import sys
from pathlib import Path

repo = Path(sys.argv[1])
text = (repo / "src/bp_engine/calibration/edge.py").read_text(encoding="utf-8")
required = (
    'side = "up" if probability_up >= 0.5 else "down"',
    'prefix = f"pm_{side}"',
    'row.predictors.get(f"{prefix}_best_ask")',
    "missing__{prefix}_book_missing",
    "missing__{prefix}_book_stale",
    "fee = config.fee_rate * ask * (1.0 - ask)",
    "cost_adjusted_edge = raw_edge - fee - config.slippage_buffer",
)
missing = [pattern for pattern in required if pattern not in text]
if missing:
    raise SystemExit(f"candidate edge source contract mismatch: {missing[0]}")
print("EDGE_SOURCE_CONTRACT=PASS")
PY

REGISTRY_BEFORE=$(psql_scalar "SELECT count(*) FROM calibration_edge_runs;")
candidate_python "$REPO/scripts/run_calibration_edge.py" \
  --start "$START" --end "$END" \
  --source-backtest-run-id "$SOURCE_5M" \
  --source-backtest-run-id "$SOURCE_15M" \
  --fee-rate 0.07 --slippage-buffer 0.01 \
  --output-dir "$ARTIFACT_ROOT" --env-file "$ENV_FILE" \
  | tee "$run_dir/calibration-first.json"
REGISTRY_AFTER_FIRST=$(psql_scalar "SELECT count(*) FROM calibration_edge_runs;")

candidate_python "$REPO/scripts/run_calibration_edge.py" \
  --start "$START" --end "$END" \
  --source-backtest-run-id "$SOURCE_5M" \
  --source-backtest-run-id "$SOURCE_15M" \
  --fee-rate 0.07 --slippage-buffer 0.01 \
  --output-dir "$ARTIFACT_ROOT" --env-file "$ENV_FILE" \
  | tee "$run_dir/calibration-second.json"
REGISTRY_AFTER_SECOND=$(psql_scalar "SELECT count(*) FROM calibration_edge_runs;")

REGISTRY_SECOND_RUN_DELTA=$((REGISTRY_AFTER_SECOND - REGISTRY_AFTER_FIRST))
if [[ "$REGISTRY_SECOND_RUN_DELTA" -ne 0 ]]; then
  echo "second Phase 9 analysis pass created registry rows" >&2
  exit 5
fi
echo "REGISTRY_SECOND_RUN_DELTA=0"

candidate_python - \
  "$ENV_FILE" "$START" "$END" \
  "$run_dir/calibration-first.json" "$run_dir/calibration-second.json" \
  "$REGISTRY_BEFORE" "$REGISTRY_AFTER_FIRST" "$REGISTRY_AFTER_SECOND" \
  "$SOURCE_5M" "$SOURCE_15M" "$SOURCE_SEMANTIC_5M" "$SOURCE_SEMANTIC_15M" \
  <<'PY' | tee "$run_dir/research-summary.txt"
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.modeling.dataset import load_dataset
from bp_engine.storage.schema import backtest_runs, calibration_edge_runs

(
    env_file,
    start_text,
    end_text,
    first_path,
    second_path,
    registry_before_text,
    registry_after_first_text,
    registry_after_second_text,
    source_5m,
    source_15m,
    source_semantic_5m,
    source_semantic_15m,
) = sys.argv[1:13]
start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
registry_before = int(registry_before_text)
registry_after_first = int(registry_after_first_text)
registry_after_second = int(registry_after_second_text)
first = sorted(json.loads(Path(first_path).read_text()), key=lambda row: row["horizon_seconds"])
second = sorted(json.loads(Path(second_path).read_text()), key=lambda row: row["horizon_seconds"])

if [row["horizon_seconds"] for row in first] != [300, 900]:
    raise SystemExit("first Phase 9 pass did not contain exactly 300s and 900s horizons")
if [row["horizon_seconds"] for row in second] != [300, 900]:
    raise SystemExit("second Phase 9 pass did not contain exactly 300s and 900s horizons")

def without_created_at(report):
    return {key: value for key, value in report.items() if key != "created_at"}

for left, right in zip(first, second, strict=True):
    if without_created_at(left) != without_created_at(right):
        raise SystemExit(f"semantic rerun mismatch for horizon={left['horizon_seconds']}")
print("SEMANTIC_RERUN_MATCH=1")

expected_sources = {
    300: (source_5m, source_semantic_5m),
    900: (source_15m, source_semantic_15m),
}
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
source_reports = {}
datasets = {}
with engine.connect() as connection:
    for horizon, (run_id, semantic) in expected_sources.items():
        stored = connection.execute(
            select(backtest_runs).where(backtest_runs.c.run_id == run_id)
        ).mappings().one()
        if stored["semantic_sha256"] != semantic:
            raise SystemExit(f"source semantic mismatch for horizon={horizon}")
        source_reports[horizon] = stored["report"]
        datasets[horizon] = load_dataset(
            connection,
            start=start,
            end=end,
            horizon_seconds=horizon,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
    for report in first:
        count = connection.execute(
            select(func.count()).select_from(calibration_edge_runs).where(
                calibration_edge_runs.c.run_id == report["run_id"]
            )
        ).scalar_one()
        if count != 1:
            raise SystemExit(f"expected one immutable Phase 9 row for {report['run_id']}")

source_offset_mismatch_violations = 0
oos_holdout_overlap_violations = 0
executable_contract_violations = 0
cost_assumption_violations = 0
selection_boundary_violations = 0

for report in first:
    horizon = report["horizon_seconds"]
    expected_run, expected_semantic = expected_sources[horizon]
    if report["source_backtest_run_id"] != expected_run:
        raise SystemExit(f"source run mismatch for horizon={horizon}")
    if report["source_backtest_semantic_sha256"] != expected_semantic:
        raise SystemExit(f"source semantic mismatch in Phase 9 report for horizon={horizon}")
    if report["config"].get("fee_rate") != 0.07:
        cost_assumption_violations += 1
    if report["config"].get("slippage_buffer") != 0.01:
        cost_assumption_violations += 1

    source = source_reports[horizon]
    source_folds = source["folds"]
    phase9_folds = report["folds"]
    if len(source_folds) != len(phase9_folds):
        source_offset_mismatch_violations += 1
    for source_fold, fold in zip(source_folds, phase9_folds, strict=False):
        if source_fold["selected_offset_seconds"] != fold["selected_offset_seconds"]:
            source_offset_mismatch_violations += 1
        if fold["calibration_selection_fit_partition"] != "train":
            selection_boundary_violations += 1
        if fold["calibration_selection_partition"] != "validation":
            selection_boundary_violations += 1
        if fold["edge_selection_partition"] != "validation":
            selection_boundary_violations += 1
        if fold["evaluation_partition"] != "test":
            selection_boundary_violations += 1

    source_final = source["final_holdout"]
    final = report["final_holdout"]
    if source_final["selected_offset_seconds"] != final["selected_offset_seconds"]:
        source_offset_mismatch_violations += 1
    if final["calibration_selection_fit_partition"] != "train":
        selection_boundary_violations += 1
    if final["calibration_selection_partition"] != "validation":
        selection_boundary_violations += 1
    if final["edge_selection_partition"] != "validation":
        selection_boundary_violations += 1
    if final["evaluation_partition"] != "holdout":
        selection_boundary_violations += 1

    ordinary_ids = report["aggregate_oos"]["condition_ids"]
    if len(ordinary_ids) != len(set(ordinary_ids)):
        oos_holdout_overlap_violations += 1
    if set(ordinary_ids).intersection(final["holdout_condition_ids"]):
        oos_holdout_overlap_violations += 1

    row_map = {
        (row.condition_id, row.feature_offset_seconds): row
        for row in datasets[horizon].rows
    }

    def check_predictions(predictions, offset):
        nonlocal_placeholder = None
        del nonlocal_placeholder
        violations = [0, 0]
        for prediction in predictions:
            decision = prediction["edge_decision"]
            if not decision["trade"]:
                continue
            row = row_map.get((prediction["condition_id"], offset))
            if row is None:
                violations[0] += 1
                continue
            side = decision["side"]
            prefix = "pm_up" if side == "up" else "pm_down"
            market_probability = row.predictors.get("pm_up_price")
            book_missing = row.predictors.get(f"missing__{prefix}_book_missing")
            book_stale = row.predictors.get(f"missing__{prefix}_book_stale")
            observed_ask = row.predictors.get(f"{prefix}_best_ask")
            if market_probability is None or not math.isfinite(float(market_probability)):
                violations[0] += 1
            if book_missing != 0.0 or book_stale != 0.0:
                violations[0] += 1
            if observed_ask is None or not 0.0 < float(observed_ask) <= 1.0:
                violations[0] += 1
            elif decision["ask"] is None or not math.isclose(
                float(decision["ask"]), float(observed_ask), rel_tol=0.0, abs_tol=1e-12
            ):
                violations[0] += 1
            if not decision["executable"] or not prediction["market_probability_observed"]:
                violations[0] += 1
            if decision["reason"] != "trade":
                violations[0] += 1
            if decision["slippage_buffer"] != 0.01:
                violations[1] += 1
            if decision["ask"] is not None:
                ask = float(decision["ask"])
                expected_fee = 0.07 * ask * (1.0 - ask)
                if not math.isclose(
                    float(decision["fee"]), expected_fee, rel_tol=0.0, abs_tol=1e-12
                ):
                    violations[1] += 1
                expected_adjusted = (
                    float(decision["raw_edge"]) - expected_fee - 0.01
                )
                if not math.isclose(
                    float(decision["cost_adjusted_edge"]),
                    expected_adjusted,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    violations[1] += 1
        return violations

    for fold in phase9_folds:
        execution_violations, cost_violations = check_predictions(
            fold["predictions"], fold["selected_offset_seconds"]
        )
        executable_contract_violations += execution_violations
        cost_assumption_violations += cost_violations
    execution_violations, cost_violations = check_predictions(
        final["predictions"], final["selected_offset_seconds"]
    )
    executable_contract_violations += execution_violations
    cost_assumption_violations += cost_violations

if source_offset_mismatch_violations:
    raise SystemExit(f"source offset mismatches={source_offset_mismatch_violations}")
if oos_holdout_overlap_violations:
    raise SystemExit(f"OOS/holdout overlap violations={oos_holdout_overlap_violations}")
if executable_contract_violations:
    raise SystemExit(f"executable contract violations={executable_contract_violations}")
if cost_assumption_violations:
    raise SystemExit(f"cost assumption violations={cost_assumption_violations}")
if selection_boundary_violations:
    raise SystemExit(f"selection boundary violations={selection_boundary_violations}")

print("SOURCE_OFFSET_MISMATCH_VIOLATIONS=0")
print("OOS_HOLDOUT_OVERLAP_VIOLATIONS=0")
print("EXECUTABLE_CONTRACT_VIOLATIONS=0")
print("COST_ASSUMPTION_VIOLATIONS=0")
print("SELECTION_BOUNDARY_VIOLATIONS=0")
print(f"REGISTRY_BEFORE={registry_before}")
print(f"REGISTRY_AFTER_FIRST={registry_after_first}")
print(f"REGISTRY_AFTER_SECOND={registry_after_second}")
print("REGISTRY_SECOND_RUN_DELTA=0")

for report in first:
    suffix = "5M" if report["horizon_seconds"] == 300 else "15M"
    policies = [fold["edge_policy_selection"]["policy"] for fold in report["folds"]]
    offsets = [fold["selected_offset_seconds"] for fold in report["folds"]]
    aggregate = report["aggregate_oos"]["edge_metrics"]
    final = report["final_holdout"]
    print(f"RUN_ID_{suffix}={report['run_id']}")
    print(f"SEMANTIC_SHA_{suffix}={report['semantic_sha256']}")
    print(f"SOURCE_RUN_ID_{suffix}={report['source_backtest_run_id']}")
    print(f"SELECTED_OFFSETS_{suffix}={','.join(str(value) for value in offsets)}")
    print(f"FOLD_POLICIES_{suffix}={','.join(policies)}")
    print(f"OOS_TRADES_{suffix}={aggregate['trade_count']}")
    print(f"OOS_ASSUMED_COST_PNL_{suffix}={aggregate['realized_pnl_after_assumed_costs']}")
    print(f"FINAL_POLICY_{suffix}={final['edge_policy_selection']['policy']}")
    print(f"FINAL_TRADES_{suffix}={final['edge_metrics']['trade_count']}")
    print(
        f"FINAL_ASSUMED_COST_PNL_{suffix}="
        f"{final['edge_metrics']['realized_pnl_after_assumed_costs']}"
    )
PY

POST_DISK=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" disk-health \
  --env-file "$ENV_FILE")
printf '%s\n' "$POST_DISK" > "$run_dir/storage-disk-health-after.json"
read -r DISK_STATUS DISK_FREE_BYTES < <(
  printf '%s' "$POST_DISK" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS" != "ok" ]]; then
  echo "disk status is $DISK_STATUS after Phase 9 acceptance" >&2
  exit 6
fi

RECORDER_AFTER=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_AFTER" != "active" ]]; then
  echo "bp-recorder is not active after Phase 9 acceptance" >&2
  exit 4
fi

{
  cat "$run_dir/research-summary.txt"
  echo "HEAD=$EXPECTED_HEAD"
  echo "DISK_STATUS_BEFORE=$DISK_STATUS_BEFORE"
  echo "DISK_FREE_BYTES_BEFORE=$DISK_FREE_BYTES_BEFORE"
  echo "DISK_STATUS=ok"
  echo "DISK_FREE_BYTES=$DISK_FREE_BYTES"
  echo "RECORDER_BEFORE=$RECORDER_BEFORE"
  echo "RECORDER_AFTER=$RECORDER_AFTER"
  echo "LIVE_TRADING_ENABLED=false"
  echo "MAX_TRADE_SIZE_USD=0"
  echo "MAX_DAILY_LOSS_USD=0"
  echo "VERDICT=PASS"
  echo "PHASE9_HOST_ACCEPTANCE=PASS"
} | tee "$run_dir/final-summary.txt"
