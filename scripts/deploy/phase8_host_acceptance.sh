#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="2026-08-24T00:00:00Z"
END="2026-08-25T00:00:00Z"
SOURCE_5M="phase7-300-0a822e17ceced11742bf6d3bc8214f44"
SOURCE_15M="phase7-900-e36d978aecc29816c5b9e2b67b30d6e2"
SOURCE_SEMANTIC_5M="0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0"
SOURCE_SEMANTIC_15M="e36d978aecc29816c5b9e2b67b30d6e218a0af6e08e6b7f31c10161ec1fc2a0b"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
HOST_PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase8-walk-forward-backtester
ARTIFACT_ROOT=/var/lib/bp/artifacts/phase8-backtests
VENV="/var/tmp/bp-phase8-venv-${EXPECTED_HEAD:0:12}-$$"

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
if [[ ! -f "$REPO/migrations/0008_backtest_runs.sql" ]]; then
  echo "missing Phase 8 backtest registry migration" >&2
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
  echo "Phase 8 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

RECORDER_BEFORE=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_BEFORE" != "active" ]]; then
  echo "bp-recorder is not active before Phase 8 acceptance" >&2
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
  echo "disk status is $DISK_STATUS_BEFORE before Phase 8 acceptance" >&2
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
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0008_backtest_runs.sql" \
  > "$run_dir/migration.txt"

SOURCE_5M_COUNT=$(psql_scalar "SELECT count(*) FROM model_training_runs WHERE run_id = '$SOURCE_5M' AND horizon_seconds = 300 AND semantic_sha256 = '$SOURCE_SEMANTIC_5M' AND validation_champion = 'market_price' AND dataset_version = 'supervised-core-v1' AND split_version = 'chronological-market-v1' AND feature_version = 'core-v1' AND label_version = 'official-outcome-v1';")
SOURCE_15M_COUNT=$(psql_scalar "SELECT count(*) FROM model_training_runs WHERE run_id = '$SOURCE_15M' AND horizon_seconds = 900 AND semantic_sha256 = '$SOURCE_SEMANTIC_15M' AND validation_champion = 'market_price' AND dataset_version = 'supervised-core-v1' AND split_version = 'chronological-market-v1' AND feature_version = 'core-v1' AND label_version = 'official-outcome-v1';")
if [[ "$SOURCE_5M_COUNT" != "1" || "$SOURCE_15M_COUNT" != "1" ]]; then
  echo "accepted Phase 7 source training runs are missing or do not match immutable semantics" >&2
  exit 5
fi

REGISTRY_BEFORE=$(psql_scalar "SELECT count(*) FROM backtest_runs;")
candidate_python "$REPO/scripts/run_walk_forward_backtest.py" \
  --start "$START" --end "$END" \
  --source-training-run-id "$SOURCE_5M" \
  --source-training-run-id "$SOURCE_15M" \
  --train-hours 8 --validation-hours 2 --test-hours 2 --step-hours 2 \
  --final-holdout-hours 2 --embargo-markets 1 \
  --min-train-markets 24 --min-validation-markets 6 --min-test-markets 6 \
  --min-market-price-coverage 0.80 --min-prediction-coverage 0.90 \
  --output-dir "$ARTIFACT_ROOT" --env-file "$ENV_FILE" \
  | tee "$run_dir/backtests-first.json"
REGISTRY_AFTER_FIRST=$(psql_scalar "SELECT count(*) FROM backtest_runs;")

candidate_python "$REPO/scripts/run_walk_forward_backtest.py" \
  --start "$START" --end "$END" \
  --source-training-run-id "$SOURCE_5M" \
  --source-training-run-id "$SOURCE_15M" \
  --train-hours 8 --validation-hours 2 --test-hours 2 --step-hours 2 \
  --final-holdout-hours 2 --embargo-markets 1 \
  --min-train-markets 24 --min-validation-markets 6 --min-test-markets 6 \
  --min-market-price-coverage 0.80 --min-prediction-coverage 0.90 \
  --output-dir "$ARTIFACT_ROOT" --env-file "$ENV_FILE" \
  | tee "$run_dir/backtests-second.json"
REGISTRY_AFTER_SECOND=$(psql_scalar "SELECT count(*) FROM backtest_runs;")

REGISTRY_SECOND_RUN_DELTA=$((REGISTRY_AFTER_SECOND - REGISTRY_AFTER_FIRST))
if [[ "$REGISTRY_SECOND_RUN_DELTA" -ne 0 ]]; then
  echo "second Phase 8 backtest pass created registry rows" >&2
  exit 5
fi

candidate_python - \
  "$ENV_FILE" "$REPO" "$START" "$END" \
  "$run_dir/backtests-first.json" "$run_dir/backtests-second.json" \
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
from bp_engine.storage.schema import backtest_runs

(
    env_file,
    repo_text,
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
) = sys.argv[1:14]
repo = Path(repo_text)
start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
registry_before = int(registry_before_text)
registry_after_first = int(registry_after_first_text)
registry_after_second = int(registry_after_second_text)
first = sorted(json.loads(Path(first_path).read_text()), key=lambda row: row["horizon_seconds"])
second = sorted(json.loads(Path(second_path).read_text()), key=lambda row: row["horizon_seconds"])

if [row["horizon_seconds"] for row in first] != [300, 900]:
    raise SystemExit("first backtest pass did not contain exactly 300s and 900s horizons")
if [row["horizon_seconds"] for row in second] != [300, 900]:
    raise SystemExit("second backtest pass did not contain exactly 300s and 900s horizons")

expected_sources = {
    300: (source_5m, source_semantic_5m),
    900: (source_15m, source_semantic_15m),
}

def without_created_at(report):
    return {key: value for key, value in report.items() if key != "created_at"}

for left, right in zip(first, second, strict=True):
    if without_created_at(left) != without_created_at(right):
        raise SystemExit(f"semantic rerun mismatch for horizon={left['horizon_seconds']}")
    expected_run, expected_semantic = expected_sources[left["horizon_seconds"]]
    if left["source_training_run_id"] != expected_run:
        raise SystemExit("source training run id mismatch")
    if left["source_training_semantic_sha256"] != expected_semantic:
        raise SystemExit("source training semantic mismatch")

settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
targets = {}
with engine.connect() as connection:
    for horizon in (300, 900):
        dataset = load_dataset(
            connection,
            start=start,
            end=end,
            horizon_seconds=horizon,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        targets[horizon] = {}
        for row in dataset.rows:
            existing = targets[horizon].setdefault(row.condition_id, row.target)
            if existing != row.target:
                raise SystemExit("condition target mismatch across feature rows")
    for report in first:
        count = connection.execute(
            select(func.count()).select_from(backtest_runs).where(
                backtest_runs.c.run_id == report["run_id"]
            )
        ).scalar_one()
        if count != 1:
            raise SystemExit(f"expected exactly one immutable registry row for {report['run_id']}")

partition_overlap_violations = 0
ordinary_test_reuse_violations = 0
single_class_partitions = 0
prediction_coverage_violations = 0
nonfinite_metric_violations = 0
execution_semantic_violations = 0

def classes_for(horizon, condition_ids):
    try:
        return {targets[horizon][condition_id] for condition_id in condition_ids}
    except KeyError as exc:
        raise SystemExit(f"report references unknown condition {exc.args[0]}") from exc


def finite_walk(value):
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return
    if isinstance(value, list):
        for item in value:
            finite_walk(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            finite_walk(item)
        return
    raise ValueError(f"unsupported report value type {type(value)!r}")


def check_execution(execution, prediction_markets):
    global execution_semantic_violations
    required = {
        "prediction_markets",
        "executable_markets",
        "unavailable_no_fill_markets",
        "execution_coverage",
        "average_observed_ask",
        "correct_executed_trades",
        "gross_execution_pnl_before_costs",
        "mean_gross_pnl_per_executed_share",
    }
    if not required <= set(execution):
        execution_semantic_violations += 1
        return
    predicted = execution["prediction_markets"]
    executable = execution["executable_markets"]
    unavailable = execution["unavailable_no_fill_markets"]
    if predicted != prediction_markets or executable + unavailable != predicted:
        execution_semantic_violations += 1
    expected_coverage = executable / predicted if predicted else 0.0
    if abs(execution["execution_coverage"] - expected_coverage) > 1e-12:
        execution_semantic_violations += 1
    ask = execution["average_observed_ask"]
    if executable and (ask is None or not 0.0 < ask <= 1.0):
        execution_semantic_violations += 1
    if not executable and ask is not None:
        execution_semantic_violations += 1


def check_regimes(regimes, expected_markets):
    global execution_semantic_violations
    required = {"utc_session", "volatility", "execution_availability"}
    if set(regimes) != required:
        execution_semantic_violations += 1
        return
    for groups in regimes.values():
        if sum(group["market_count"] for group in groups.values()) != expected_markets:
            execution_semantic_violations += 1

execution_source = (repo / "src/bp_engine/backtesting/execution.py").read_text(encoding="utf-8")
for token in (
    "pm_up_best_ask",
    "pm_down_best_ask",
    "missing__{prefix}_book_missing",
    "missing__{prefix}_book_stale",
    "gross_execution_pnl_before_costs",
):
    if token not in execution_source:
        execution_semantic_violations += 1
for forbidden in ("midpoint", "price_history", "synthetic_fill"):
    if forbidden in execution_source:
        execution_semantic_violations += 1

by_horizon = {}
for report in first:
    horizon = report["horizon_seconds"]
    by_horizon[horizon] = report
    try:
        finite_walk(report)
    except ValueError:
        nonfinite_metric_violations += 1
    folds = report["folds"]
    if len(folds) < 3:
        raise SystemExit(f"horizon {horizon} has fewer than three ordinary walk-forward folds")

    seen_tests = set()
    flattened_tests = []
    for fold in folds:
        train_ids = set(fold["train_condition_ids"])
        validation_ids = set(fold["validation_condition_ids"])
        test_ids = set(fold["test_condition_ids"])
        partition_overlap_violations += len(train_ids & validation_ids)
        partition_overlap_violations += len(train_ids & test_ids)
        partition_overlap_violations += len(validation_ids & test_ids)
        ordinary_test_reuse_violations += len(seen_tests & test_ids)
        seen_tests |= test_ids
        flattened_tests.extend(fold["test_condition_ids"])
        for ids in (train_ids, validation_ids, test_ids):
            if classes_for(horizon, ids) != {0, 1}:
                single_class_partitions += 1
        coverage = fold["prediction_coverage"]
        if coverage < 0.90:
            prediction_coverage_violations += 1
        if fold["expected_test_markets"] != (
            fold["predicted_test_markets"] + len(fold["missing_offset_condition_ids"])
        ):
            prediction_coverage_violations += 1
        if fold["selected_offset_seconds"] <= 0 or fold["selected_offset_seconds"] >= horizon:
            prediction_coverage_violations += 1
        candidate_offsets = {
            candidate["offset_seconds"] for candidate in fold["validation_candidates"]
        }
        if fold["selected_offset_seconds"] not in candidate_offsets:
            prediction_coverage_violations += 1
        check_execution(fold["execution"], fold["predicted_test_markets"])
        check_regimes(fold["regimes"], fold["metrics"]["market_count"])

    if flattened_tests != report["aggregate_oos_condition_ids"]:
        ordinary_test_reuse_violations += 1
    if len(flattened_tests) != len(set(flattened_tests)):
        ordinary_test_reuse_violations += 1
    check_execution(report["aggregate_oos_execution"], report["aggregate_oos_metrics"]["market_count"])
    check_regimes(report["aggregate_oos_regimes"], report["aggregate_oos_metrics"]["market_count"])

    final = report["final_holdout"]
    final_train = set(final["train_condition_ids"])
    final_validation = set(final["validation_condition_ids"])
    final_holdout = set(final["holdout_condition_ids"])
    partition_overlap_violations += len(final_train & final_validation)
    partition_overlap_violations += len(final_train & final_holdout)
    partition_overlap_violations += len(final_validation & final_holdout)
    partition_overlap_violations += len(set(flattened_tests) & final_holdout)
    if not final_holdout or final["predicted_holdout_markets"] <= 0:
        raise SystemExit(f"horizon {horizon} final holdout is empty")
    for ids in (final_train, final_validation, final_holdout):
        if classes_for(horizon, ids) != {0, 1}:
            single_class_partitions += 1
    if final["prediction_coverage"] < 0.90:
        prediction_coverage_violations += 1
    if final["expected_holdout_markets"] != (
        final["predicted_holdout_markets"] + len(final["missing_offset_condition_ids"])
    ):
        prediction_coverage_violations += 1
    candidate_offsets = {
        candidate["offset_seconds"] for candidate in final["validation_candidates"]
    }
    if final["selected_offset_seconds"] not in candidate_offsets:
        prediction_coverage_violations += 1
    check_execution(final["execution"], final["predicted_holdout_markets"])
    check_regimes(final["regimes"], final["metrics"]["market_count"])

if partition_overlap_violations:
    raise SystemExit(f"partition overlap violations={partition_overlap_violations}")
if ordinary_test_reuse_violations:
    raise SystemExit(f"ordinary test reuse violations={ordinary_test_reuse_violations}")
if single_class_partitions:
    raise SystemExit(f"single-class evaluated partitions={single_class_partitions}")
if prediction_coverage_violations:
    raise SystemExit(f"prediction coverage violations={prediction_coverage_violations}")
if nonfinite_metric_violations:
    raise SystemExit(f"non-finite metric violations={nonfinite_metric_violations}")
if execution_semantic_violations:
    raise SystemExit(f"execution semantic violations={execution_semantic_violations}")
if registry_after_second != registry_after_first:
    raise SystemExit("second backtest pass changed registry row count")

for horizon, suffix in ((300, "5M"), (900, "15M")):
    report = by_horizon[horizon]
    final = report["final_holdout"]
    print(f"RUN_ID_{suffix}={report['run_id']}")
    print(f"DATASET_SHA_{suffix}={report['dataset_sha256']}")
    print(f"CONFIG_SHA_{suffix}={report['config_sha256']}")
    print(f"PLAN_SHA_{suffix}={report['plan_sha256']}")
    print(f"SEMANTIC_SHA_{suffix}={report['semantic_sha256']}")
    print(f"FOLDS_{suffix}={len(report['folds'])}")
    print(f"FINAL_HOLDOUT_{suffix}=present")
    print(f"ORDINARY_OOS_MARKETS_{suffix}={report['aggregate_oos_metrics']['market_count']}")
    print(f"ORDINARY_OOS_ACCURACY_{suffix}={report['aggregate_oos_metrics']['accuracy']}")
    print(f"ORDINARY_OOS_LOG_LOSS_{suffix}={report['aggregate_oos_metrics']['log_loss']}")
    print(f"ORDINARY_OOS_BRIER_{suffix}={report['aggregate_oos_metrics']['brier_score']}")
    print(f"ORDINARY_EXECUTION_COVERAGE_{suffix}={report['aggregate_oos_execution']['execution_coverage']}")
    print(f"ORDINARY_GROSS_PNL_BEFORE_COSTS_{suffix}={report['aggregate_oos_execution']['gross_execution_pnl_before_costs']}")
    print(f"FINAL_HOLDOUT_ACCURACY_{suffix}={final['metrics']['accuracy']}")
    print(f"FINAL_EXECUTION_COVERAGE_{suffix}={final['execution']['execution_coverage']}")
    print(f"FINAL_GROSS_PNL_BEFORE_COSTS_{suffix}={final['execution']['gross_execution_pnl_before_costs']}")
    print(
        f"SELECTED_OFFSETS_{suffix}="
        + ",".join(str(fold["selected_offset_seconds"]) for fold in report["folds"])
    )

print("SEMANTIC_RERUN_MATCH=1")
print(f"REGISTRY_BEFORE={registry_before}")
print(f"REGISTRY_AFTER_FIRST={registry_after_first}")
print(f"REGISTRY_AFTER_SECOND={registry_after_second}")
print("REGISTRY_SECOND_RUN_DELTA=0")
print("PARTITION_OVERLAP_VIOLATIONS=0")
print("ORDINARY_TEST_REUSE_VIOLATIONS=0")
print("SINGLE_CLASS_PARTITIONS=0")
print("PREDICTION_COVERAGE_VIOLATIONS=0")
print("NONFINITE_METRIC_VIOLATIONS=0")
print("EXECUTION_SEMANTIC_VIOLATIONS=0")
PY

POST_REPORT=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" report \
  --env-file "$ENV_FILE")
printf '%s\n' "$POST_REPORT" > "$run_dir/storage-report-after.json"
read -r DISK_STATUS DISK_FREE_BYTES < <(
  printf '%s' "$POST_REPORT" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin)["disk"]; print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS" != "ok" ]]; then
  echo "disk status is $DISK_STATUS after Phase 8 acceptance" >&2
  exit 6
fi

RECORDER_AFTER=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_AFTER" != "active" ]]; then
  echo "bp-recorder is not active after Phase 8 acceptance" >&2
  exit 6
fi

live_trading_after=$(read_env LIVE_TRADING_ENABLED)
max_trade_after=$(read_env MAX_TRADE_SIZE_USD)
max_loss_after=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$live_trading_after" != "false" || "$max_trade_after" != "0" || "$max_loss_after" != "0" ]]; then
  echo "trading safety changed during Phase 8 acceptance" >&2
  exit 6
fi

{
  echo "VERDICT=PASS"
  echo "HEAD=$actual_head"
  echo "CANDIDATE_REPO=$REPO"
  echo "DEPLOYED_RECORDER_REPO=$HOST_REPO"
  echo "ACCEPTANCE_START=$START"
  echo "ACCEPTANCE_END=$END"
  cat "$run_dir/research-summary.txt"
  echo "DISK_STATUS_BEFORE=$DISK_STATUS_BEFORE"
  echo "DISK_FREE_BYTES_BEFORE=$DISK_FREE_BYTES_BEFORE"
  echo "DISK_STATUS=ok"
  echo "DISK_FREE_BYTES=$DISK_FREE_BYTES"
  echo "RECORDER_BEFORE=$RECORDER_BEFORE"
  echo "RECORDER_AFTER=$RECORDER_AFTER"
  echo "LIVE_TRADING_ENABLED=$live_trading_after"
  echo "MAX_TRADE_SIZE_USD=$max_trade_after"
  echo "MAX_DAILY_LOSS_USD=$max_loss_after"
  echo "EVIDENCE_DIR=$run_dir"
  echo "ARTIFACT_DIR=$ARTIFACT_ROOT"
} > "$run_dir/final-summary.txt"

cat "$run_dir/final-summary.txt"
