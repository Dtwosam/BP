#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="${PHASE7_ACCEPTANCE_START:-2026-08-24T00:00:00Z}"
END="${PHASE7_ACCEPTANCE_END:-2026-08-25T00:00:00Z}"
STEP_SECONDS="${PHASE7_STEP_SECONDS:-60}"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
HOST_PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase7-baseline-modeling
ARTIFACT_ROOT=/var/lib/bp/artifacts/phase7-baseline-modeling
VENV="/var/tmp/bp-phase7-venv-${EXPECTED_HEAD:0:12}-$$"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if ! [[ "$STEP_SECONDS" =~ ^[0-9]+$ ]] || [[ "$STEP_SECONDS" -le 0 ]]; then
  echo "PHASE7_STEP_SECONDS must be a positive integer" >&2
  exit 2
fi

actual_head=$(git -C "$REPO" rev-parse HEAD)
if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "expected HEAD $EXPECTED_HEAD but found $actual_head" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" ]]; then
  echo "missing production env or host Python" >&2
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
  echo "Phase 7 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

recorder_before=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_before" != "active" ]]; then
  echo "bp-recorder is not active before Phase 7 acceptance" >&2
  exit 4
fi

install -d -o bp -g bp "$EVIDENCE_ROOT" "$ARTIFACT_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

PRE_REPORT=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" report \
  --env-file "$ENV_FILE")
printf '%s\n' "$PRE_REPORT" > "$run_dir/storage-report-before.json"
read -r DISK_STATUS_BEFORE DISK_FREE_BYTES_BEFORE < <(
  printf '%s' "$PRE_REPORT" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin)["disk"]; print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS_BEFORE" != "ok" ]]; then
  echo "disk status is $DISK_STATUS_BEFORE before Phase 7 acceptance" >&2
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
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0007_model_training_runs.sql" \
  > "$run_dir/migration.txt"

candidate_python "$REPO/scripts/historical_backfill.py" standard \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/backfill.json"
candidate_python "$REPO/scripts/generate_labels.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/labels.json"
candidate_python "$REPO/scripts/generate_features.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  --step-seconds "$STEP_SECONDS" \
  | tee "$run_dir/features.json"

REGISTRY_BEFORE=$(psql_scalar "SELECT count(*) FROM model_training_runs;")
candidate_python "$REPO/scripts/train_baselines.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  --output-dir "$ARTIFACT_ROOT" \
  --horizon-seconds 300 --horizon-seconds 900 --min-markets 30 \
  | tee "$run_dir/models-first.json"
REGISTRY_AFTER_FIRST=$(psql_scalar "SELECT count(*) FROM model_training_runs;")
candidate_python "$REPO/scripts/train_baselines.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  --output-dir "$ARTIFACT_ROOT" \
  --horizon-seconds 300 --horizon-seconds 900 --min-markets 30 \
  | tee "$run_dir/models-second.json"
REGISTRY_AFTER_SECOND=$(psql_scalar "SELECT count(*) FROM model_training_runs;")

if [[ "$REGISTRY_AFTER_SECOND" != "$REGISTRY_AFTER_FIRST" ]]; then
  echo "second Phase 7 training pass created new registry rows" >&2
  exit 5
fi

candidate_python - \
  "$ENV_FILE" "$START" "$END" "$ARTIFACT_ROOT" \
  "$run_dir/models-first.json" "$run_dir/models-second.json" \
  "$REGISTRY_BEFORE" "$REGISTRY_AFTER_FIRST" "$REGISTRY_AFTER_SECOND" \
  <<'PY' | tee "$run_dir/research-summary.txt"
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.split import chronological_market_split
from bp_engine.storage.schema import market_features, market_labels, model_training_runs

env_file, start_text, end_text, artifact_root_text, first_path, second_path = sys.argv[1:7]
registry_before, registry_after_first, registry_after_second = map(int, sys.argv[7:10])
start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
artifact_root = Path(artifact_root_text)
first = sorted(json.loads(Path(first_path).read_text()), key=lambda row: row["horizon_seconds"])
second = sorted(json.loads(Path(second_path).read_text()), key=lambda row: row["horizon_seconds"])
if [row["horizon_seconds"] for row in first] != [300, 900]:
    raise SystemExit("first model pass did not contain exactly 300s and 900s horizons")
if [row["horizon_seconds"] for row in second] != [300, 900]:
    raise SystemExit("second model pass did not contain exactly 300s and 900s horizons")

semantic_keys = (
    "run_id", "dataset_sha256", "split_sha256", "predictor_names",
    "dropped_all_missing", "model_configs", "validation_champion",
    "best_test_result", "boosted_promotion_eligible", "evaluations",
    "offset_metrics", "gross_execution_diagnostic", "artifacts", "semantic_sha256",
)
artifact_hash_violations = 0
for left, right in zip(first, second, strict=True):
    for key in semantic_keys:
        if left[key] != right[key]:
            raise SystemExit(
                f"semantic rerun mismatch horizon={left['horizon_seconds']} key={key}"
            )
    for artifact in left["artifacts"]:
        expected = artifact["sha256"]
        if not any(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected
            for path in artifact_root.rglob(artifact["file_name"])
        ):
            artifact_hash_violations += 1
if artifact_hash_violations:
    raise SystemExit(f"{artifact_hash_violations} model artifact hashes failed verification")

settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
labels = {}
features = {}
partition_violations = 0
single_class_partitions = 0
with engine.connect() as connection:
    for horizon in (300, 900):
        labels[horizon] = connection.execute(
            select(func.count()).select_from(market_labels).where(
                market_labels.c.market_start_at >= start,
                market_labels.c.market_start_at < end,
                market_labels.c.label_version == "official-outcome-v1",
                market_labels.c.horizon_seconds == horizon,
            )
        ).scalar_one()
        features[horizon] = connection.execute(
            select(func.count()).select_from(market_features).where(
                market_features.c.market_start_at >= start,
                market_features.c.market_start_at < end,
                market_features.c.feature_version == "core-v1",
                market_features.c.horizon_seconds == horizon,
            )
        ).scalar_one()
        dataset = load_dataset(
            connection,
            start=start,
            end=end,
            horizon_seconds=horizon,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        split = chronological_market_split(dataset, min_markets=30)
        sets = [
            set(split.train.condition_ids),
            set(split.validation.condition_ids),
            set(split.test.condition_ids),
        ]
        for index, left_set in enumerate(sets):
            for right_set in sets[index + 1:]:
                partition_violations += len(left_set & right_set)
        for partition in (split.train, split.validation, split.test):
            if {row.target for row in partition.rows} != {0, 1}:
                single_class_partitions += 1
    for row in first:
        count = connection.execute(
            select(func.count()).select_from(model_training_runs).where(
                model_training_runs.c.run_id == row["run_id"]
            )
        ).scalar_one()
        if count != 1:
            raise SystemExit(f"expected one registry row for {row['run_id']}")

if labels[300] < 100 or labels[900] < 30:
    raise SystemExit(f"insufficient labeled markets: 5m={labels[300]} 15m={labels[900]}")
if features[300] <= 0 or features[900] <= 0:
    raise SystemExit("both horizons must have non-empty core-v1 feature rows")
if partition_violations:
    raise SystemExit(f"partition violations={partition_violations}")
if single_class_partitions:
    raise SystemExit(f"single-class non-embargo partitions={single_class_partitions}")
if registry_after_second != registry_after_first:
    raise SystemExit("second training pass changed registry row count")

by_horizon = {row["horizon_seconds"]: row for row in first}
print(f"LABELS_5M={labels[300]}")
print(f"LABELS_15M={labels[900]}")
print(f"FEATURE_ROWS_5M={features[300]}")
print(f"FEATURE_ROWS_15M={features[900]}")
for horizon, suffix in ((300, "5M"), (900, "15M")):
    row = by_horizon[horizon]
    print(f"RUN_ID_{suffix}={row['run_id']}")
    print(f"DATASET_SHA_{suffix}={row['dataset_sha256']}")
    print(f"SPLIT_SHA_{suffix}={row['split_sha256']}")
    print(f"SEMANTIC_SHA_{suffix}={row['semantic_sha256']}")
    print(f"VALIDATION_CHAMPION_{suffix}={row['validation_champion']}")
    print(f"BOOSTED_PROMOTION_ELIGIBLE_{suffix}={str(row['boosted_promotion_eligible']).lower()}")
print("SEMANTIC_RERUN_MATCH=1")
print(f"REGISTRY_BEFORE={registry_before}")
print(f"REGISTRY_AFTER_FIRST={registry_after_first}")
print(f"REGISTRY_AFTER_SECOND={registry_after_second}")
print("REGISTRY_SECOND_RUN_DELTA=0")
print("PARTITION_VIOLATIONS=0")
print("SINGLE_CLASS_PARTITIONS=0")
print("ARTIFACT_HASH_VIOLATIONS=0")
PY

POST_REPORT=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" report \
  --env-file "$ENV_FILE")
printf '%s\n' "$POST_REPORT" > "$run_dir/storage-report-after.json"
read -r DISK_STATUS DISK_FREE_BYTES < <(
  printf '%s' "$POST_REPORT" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin)["disk"]; print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS" != "ok" ]]; then
  echo "disk status is $DISK_STATUS after Phase 7 acceptance" >&2
  exit 6
fi

recorder_after=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_after" != "active" ]]; then
  echo "bp-recorder is not active after Phase 7 acceptance" >&2
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
  echo "DISK_STATUS=$DISK_STATUS"
  echo "DISK_FREE_BYTES=$DISK_FREE_BYTES"
  echo "RECORDER_BEFORE=$recorder_before"
  echo "RECORDER_AFTER=$recorder_after"
  echo "LIVE_TRADING_ENABLED=$live_trading"
  echo "MAX_TRADE_SIZE_USD=$max_trade"
  echo "MAX_DAILY_LOSS_USD=$max_loss"
} > "$run_dir/final-summary.txt"

cat "$run_dir/final-summary.txt"
