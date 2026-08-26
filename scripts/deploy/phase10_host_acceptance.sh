#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
SOURCE_5M="phase9-300-c9f0e00eb7836af08008c66909f8f179"
SOURCE_15M="phase9-900-15c234f25588b23cce73a12f87a2e2ea"
SOURCE_SEMANTIC_5M="c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
SOURCE_SEMANTIC_15M="15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
HOST_PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase10-live-prediction
RUNTIME_ROOT=/var/lib/bp/phase10-runtime
VENV="$RUNTIME_ROOT/bp-phase10-venv-${EXPECTED_HEAD:0:12}-$$"
RUNTIME_UNIT=/run/systemd/system/bp-live-predictor.service
OBSERVE_SECONDS="${PHASE10_OBSERVE_SECONDS:-2100}"
POLL_SECONDS="${PHASE10_ACCEPTANCE_POLL_SECONDS:-10}"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if ! [[ "$OBSERVE_SECONDS" =~ ^[0-9]+$ ]] || (( OBSERVE_SECONDS < 60 )); then
  echo "PHASE10_OBSERVE_SECONDS must be an integer of at least 60" >&2
  exit 2
fi
if ! [[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] || (( POLL_SECONDS < 1 )); then
  echo "PHASE10_ACCEPTANCE_POLL_SECONDS must be a positive integer" >&2
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
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" || ! -f "$COMPOSE_FILE" ]]; then
  echo "missing production env, host Python, or compose file" >&2
  exit 2
fi
if [[ ! -f "$REPO/migrations/0010_live_predictions.sql" ]]; then
  echo "missing Phase 10 live prediction migration" >&2
  exit 2
fi
if [[ ! -f "$REPO/deploy/bp-live-predictor.service" ]]; then
  echo "missing bp-live-predictor service definition" >&2
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

TRADING_MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$TRADING_MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 10 acceptance requires research mode, live trading disabled, and zero limits" >&2
  exit 3
fi

RECORDER_BEFORE=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_BEFORE" != "active" ]]; then
  echo "bp-recorder is not active before Phase 10 acceptance" >&2
  exit 4
fi

if ! grep -qx 'User=bp' "$REPO/deploy/bp-live-predictor.service" || \
   ! grep -qx 'Group=bp' "$REPO/deploy/bp-live-predictor.service"; then
  echo "bp-live-predictor service is not unprivileged" >&2
  exit 4
fi

echo "PREDICTOR_SERVICE_CONTRACT=User=bp"
install -d -o bp -g bp "$EVIDENCE_ROOT" "$RUNTIME_ROOT"
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
  echo "disk status is $DISK_STATUS_BEFORE before Phase 10 acceptance" >&2
  exit 6
fi

PREDICTOR_WAS_ACTIVE=$(systemctl is-active bp-live-predictor || true)
cleanup() {
  set +e
  systemctl stop bp-live-predictor >/dev/null 2>&1 || true
  rm -f "$RUNTIME_UNIT" >/dev/null 2>&1 || true
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ "$PREDICTOR_WAS_ACTIVE" == "active" ]]; then
    systemctl start bp-live-predictor >/dev/null 2>&1 || true
  fi
  rm -rf "$VENV" >/dev/null 2>&1 || true
  set -e
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
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0010_live_predictions.sql" \
  > "$run_dir/migration.txt"

SOURCE_5M_COUNT=$(psql_scalar "SELECT count(*) FROM calibration_edge_runs WHERE run_id = '$SOURCE_5M' AND horizon_seconds = 300 AND semantic_sha256 = '$SOURCE_SEMANTIC_5M' AND calibration_version = 'platt-or-identity-v1' AND edge_policy_version = 'selected-ask-edge-v1';")
SOURCE_15M_COUNT=$(psql_scalar "SELECT count(*) FROM calibration_edge_runs WHERE run_id = '$SOURCE_15M' AND horizon_seconds = 900 AND semantic_sha256 = '$SOURCE_SEMANTIC_15M' AND calibration_version = 'platt-or-identity-v1' AND edge_policy_version = 'selected-ask-edge-v1';")
if [[ "$SOURCE_5M_COUNT" != "1" || "$SOURCE_15M_COUNT" != "1" ]]; then
  echo "frozen Phase 9 policy sources are missing or do not match immutable semantics" >&2
  exit 5
fi

OFFSET_5M=$(psql_scalar "SELECT report->'final_holdout'->>'selected_offset_seconds' FROM calibration_edge_runs WHERE run_id = '$SOURCE_5M';")
OFFSET_15M=$(psql_scalar "SELECT report->'final_holdout'->>'selected_offset_seconds' FROM calibration_edge_runs WHERE run_id = '$SOURCE_15M';")
if ! [[ "$OFFSET_5M" =~ ^[0-9]+$ ]] || ! [[ "$OFFSET_15M" =~ ^[0-9]+$ ]]; then
  echo "accepted policy selected offsets are not valid integers" >&2
  exit 5
fi

ORDER_SIDE_EFFECT_VIOLATIONS=$(candidate_python - "$REPO" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "src" / "bp_engine" / "live_prediction"
markers = (
    "create_order",
    "send_order",
    "execution_client",
    "signing_client",
    "allowance_client",
    "position_size",
)
violations = 0
for path in root.glob("*.py"):
    text = path.read_text(encoding="utf-8").lower()
    violations += sum(marker in text for marker in markers)
print(violations)
PY
)
if [[ "$ORDER_SIDE_EFFECT_VIOLATIONS" != "0" ]]; then
  echo "Phase 10 candidate contains execution-side-effect markers" >&2
  exit 5
fi

systemctl stop bp-live-predictor >/dev/null 2>&1 || true
cat > "$RUNTIME_UNIT" <<EOF
[Unit]
Description=BP prospective predictor acceptance candidate
After=network-online.target

[Service]
Type=simple
User=bp
Group=bp
WorkingDirectory=$REPO
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$REPO/src
ExecStart=$CANDIDATE_PY -m bp_engine.live_prediction run --source-calibration-run-id $SOURCE_5M --source-calibration-run-id $SOURCE_15M --env-file $ENV_FILE --poll-interval-seconds 1 --max-lateness-seconds 10
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
EOF
systemctl daemon-reload
systemctl start bp-live-predictor
sleep 2
if [[ "$(systemctl show bp-live-predictor -p User --value)" != "bp" ]]; then
  echo "bp-live-predictor runtime user is not bp" >&2
  exit 5
fi
if [[ "$(systemctl is-active bp-live-predictor || true)" != "active" ]]; then
  echo "bp-live-predictor failed to become active" >&2
  systemctl --no-pager --full status bp-live-predictor >&2 || true
  journalctl -u bp-live-predictor -n 80 --no-pager >&2 || true
  exit 5
fi

ACCEPTANCE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PREDICTION_COUNT_5M=0
PREDICTION_COUNT_15M=0
FUTURE_MARKET_COUNT_5M=0
FUTURE_MARKET_COUNT_15M=0
elapsed=0

while (( elapsed < OBSERVE_SECONDS )); do
  current_future_5m=$(psql_scalar "SELECT count(*) FROM polymarket_markets m WHERE m.horizon_seconds = 300 AND m.active IS TRUE AND m.closed IS FALSE AND m.resolved_outcome IS NULL AND m.start_at + ($OFFSET_5M * interval '1 second') > now() AND m.start_at + ($OFFSET_5M * interval '1 second') < m.end_at;")
  current_future_15m=$(psql_scalar "SELECT count(*) FROM polymarket_markets m WHERE m.horizon_seconds = 900 AND m.active IS TRUE AND m.closed IS FALSE AND m.resolved_outcome IS NULL AND m.start_at + ($OFFSET_15M * interval '1 second') > now() AND m.start_at + ($OFFSET_15M * interval '1 second') < m.end_at;")
  if (( current_future_5m > FUTURE_MARKET_COUNT_5M )); then FUTURE_MARKET_COUNT_5M=$current_future_5m; fi
  if (( current_future_15m > FUTURE_MARKET_COUNT_15M )); then FUTURE_MARKET_COUNT_15M=$current_future_15m; fi

  PREDICTION_COUNT_5M=$(psql_scalar "SELECT count(*) FROM live_predictions WHERE prediction_version = 'live-prediction-v1' AND horizon_seconds = 300 AND recorded_at >= '$ACCEPTANCE_STARTED_AT'::timestamptz;")
  PREDICTION_COUNT_15M=$(psql_scalar "SELECT count(*) FROM live_predictions WHERE prediction_version = 'live-prediction-v1' AND horizon_seconds = 900 AND recorded_at >= '$ACCEPTANCE_STARTED_AT'::timestamptz;")

  if (( FUTURE_MARKET_COUNT_5M > 0 && FUTURE_MARKET_COUNT_15M > 0 && \
        PREDICTION_COUNT_5M > 0 && PREDICTION_COUNT_15M > 0 )); then
    break
  fi
  sleep "$POLL_SECONDS"
  elapsed=$((elapsed + POLL_SECONDS))
done

systemctl stop bp-live-predictor

if (( FUTURE_MARKET_COUNT_5M == 0 || FUTURE_MARKET_COUNT_15M == 0 )); then
  echo "PHASE10_HOST_ACCEPTANCE=PENDING"
  echo "REASON=future_verified_markets_unavailable"
  echo "FUTURE_MARKET_COUNT_5M=$FUTURE_MARKET_COUNT_5M"
  echo "FUTURE_MARKET_COUNT_15M=$FUTURE_MARKET_COUNT_15M"
  exit 7
fi
if (( PREDICTION_COUNT_5M == 0 || PREDICTION_COUNT_15M == 0 )); then
  echo "PHASE10_HOST_ACCEPTANCE=FAIL"
  echo "REASON=prospective_prediction_coverage_missing"
  echo "PREDICTION_COUNT_5M=$PREDICTION_COUNT_5M"
  echo "PREDICTION_COUNT_15M=$PREDICTION_COUNT_15M"
  exit 7
fi

candidate_python -m bp_engine.live_prediction report \
  --source-calibration-run-id "$SOURCE_5M" \
  --source-calibration-run-id "$SOURCE_15M" \
  --env-file "$ENV_FILE" --max-lateness-seconds 10 \
  | tee "$run_dir/prediction-report.json"

candidate_python - \
  "$ENV_FILE" "$run_dir/prediction-report.json" "$REPO" \
  "$PREDICTION_COUNT_5M" "$PREDICTION_COUNT_15M" \
  "$FUTURE_MARKET_COUNT_5M" "$FUTURE_MARKET_COUNT_15M" \
  "$ORDER_SIDE_EFFECT_VIOLATIONS" <<'PY' | tee "$run_dir/prospective-evidence.txt"
import json
import sys
from datetime import UTC
from pathlib import Path

from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.storage.schema import live_prediction_evaluations, live_predictions

(
    env_file,
    report_path,
    repo,
    prediction_5m,
    prediction_15m,
    future_5m,
    future_15m,
    order_side_effect_violations,
) = sys.argv[1:9]
report = json.loads(Path(report_path).read_text(encoding="utf-8"))
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)

with engine.connect() as connection:
    predictions = connection.execute(select(live_predictions)).mappings().all()
    evaluations = connection.execute(select(live_prediction_evaluations)).mappings().all()


def utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


source_cutoff_violations = 0
pre_outcome_violations = 0
max_lateness_ms = 0
predictions_by_id = {str(row["prediction_id"]): row for row in predictions}
for row in predictions:
    scheduled_at = utc(row["scheduled_at"])
    recorded_at = utc(row["recorded_at"])
    market_end_at = utc(row["market_end_at"])
    max_lateness_ms = max(max_lateness_ms, int(row["lateness_ms"]))
    if recorded_at >= market_end_at:
        pre_outcome_violations += 1
    for field in (
        "market_probability_observed_at",
        "up_book_cutoff_at",
        "down_book_cutoff_at",
    ):
        value = row[field]
        if value is not None and utc(value) > scheduled_at:
            source_cutoff_violations += 1
    params = row["market_probability_request_params"] or {}
    end_ts = params.get("endTs")
    if end_ts is not None:
        try:
            if int(end_ts) > int(scheduled_at.timestamp()):
                source_cutoff_violations += 1
        except (TypeError, ValueError):
            source_cutoff_violations += 1

for evaluation in evaluations:
    prediction = predictions_by_id.get(str(evaluation["prediction_id"]))
    if prediction is None:
        pre_outcome_violations += 1
        continue
    if utc(prediction["recorded_at"]) >= utc(evaluation["label_source_observed_at"]):
        pre_outcome_violations += 1

evaluation_count = len(evaluations)
evaluation_status = "appended" if evaluation_count else "pending"
late_or_missed = int(report["late_or_missed_coverage"])
semantic_hash_violations = int(report["semantic_hash_violations"])
duplicate_natural_keys = int(report["duplicate_natural_keys"])
evaluation_mutation_violations = int(report["prediction_mutation_violations"])
order_violations = int(order_side_effect_violations)

print(f"PREDICTION_COUNT_5M={prediction_5m}")
print(f"PREDICTION_COUNT_15M={prediction_15m}")
print(f"FUTURE_MARKET_COUNT_5M={future_5m}")
print(f"FUTURE_MARKET_COUNT_15M={future_15m}")
print(f"LATE_OR_MISSED_COVERAGE={late_or_missed}")
print(f"MAX_LATENESS_MS={max_lateness_ms}")
print(f"PRE_OUTCOME_VIOLATIONS={pre_outcome_violations}")
print(f"SOURCE_CUTOFF_VIOLATIONS={source_cutoff_violations}")
print(f"SEMANTIC_HASH_VIOLATIONS={semantic_hash_violations}")
print(f"DUPLICATE_NATURAL_KEYS={duplicate_natural_keys}")
print(f"EVALUATION_COUNT={evaluation_count}")
print(f"EVALUATION_STATUS={evaluation_status}")
print(f"EVALUATION_MUTATION_VIOLATIONS={evaluation_mutation_violations}")
print(f"ORDER_SIDE_EFFECT_VIOLATIONS={order_violations}")

violations = (
    pre_outcome_violations
    + source_cutoff_violations
    + semantic_hash_violations
    + duplicate_natural_keys
    + evaluation_mutation_violations
    + order_violations
)
if max_lateness_ms > 10_000:
    violations += 1
if violations:
    raise SystemExit(f"prospective evidence violations={violations}")
print("VERDICT=PASS")
PY

RECORDER_AFTER=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_AFTER" != "active" ]]; then
  echo "bp-recorder is not active after Phase 10 acceptance" >&2
  exit 4
fi
POST_DISK=$(sudo -u bp "$HOST_PY" "$HOST_REPO/scripts/storage_maintenance.py" disk-health \
  --env-file "$ENV_FILE")
printf '%s\n' "$POST_DISK" > "$run_dir/storage-disk-health-after.json"
read -r DISK_STATUS_AFTER DISK_FREE_BYTES_AFTER < <(
  printf '%s' "$POST_DISK" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS_AFTER" != "ok" ]]; then
  echo "disk status is $DISK_STATUS_AFTER after Phase 10 acceptance" >&2
  exit 6
fi

printf 'RECORDER_BEFORE=%s\n' "$RECORDER_BEFORE"
printf 'RECORDER_AFTER=%s\n' "$RECORDER_AFTER"
printf 'DISK_STATUS_BEFORE=%s\n' "$DISK_STATUS_BEFORE"
printf 'DISK_STATUS_AFTER=%s\n' "$DISK_STATUS_AFTER"
printf 'DISK_FREE_BYTES_BEFORE=%s\n' "$DISK_FREE_BYTES_BEFORE"
printf 'DISK_FREE_BYTES_AFTER=%s\n' "$DISK_FREE_BYTES_AFTER"
echo "PHASE10_HOST_ACCEPTANCE=PASS"
echo "EVIDENCE_DIR=$run_dir"
