#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="${PHASE6_ACCEPTANCE_START:-2026-08-24T18:00:00Z}"
END="${PHASE6_ACCEPTANCE_END:-2026-08-24T19:00:00Z}"
STEP_SECONDS="${PHASE6_STEP_SECONDS:-60}"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase6-feature-engine

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if ! [[ "$STEP_SECONDS" =~ ^[0-9]+$ ]] || [[ "$STEP_SECONDS" -le 0 ]]; then
  echo "PHASE6_STEP_SECONDS must be a positive integer" >&2
  exit 2
fi

actual_head=$(git -C "$REPO" rev-parse HEAD)
if [[ "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "expected HEAD $EXPECTED_HEAD but found $actual_head" >&2
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 2
fi
if [[ ! -x "$PY" ]]; then
  echo "missing host virtualenv Python at $PY" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

candidate_python() {
  sudo -u bp env PYTHONPATH="$REPO/src" "$PY" "$@"
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
  echo "Phase 6 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

recorder_before=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_before" != "active" ]]; then
  echo "bp-recorder is not active before Phase 6 acceptance" >&2
  exit 4
fi

install -d -o bp -g bp "$EVIDENCE_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

# Phase 6 migration is additive and idempotent; it does not alter recorder/raw/history tables.
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0006_market_features.sql" \
  > "$run_dir/migration.txt"

candidate_python "$REPO/scripts/generate_features.py" \
  --start "$START" \
  --end "$END" \
  --env-file "$ENV_FILE" \
  --step-seconds "$STEP_SECONDS" \
  | tee "$run_dir/features-first.json"

candidate_python "$REPO/scripts/generate_features.py" \
  --start "$START" \
  --end "$END" \
  --env-file "$ENV_FILE" \
  --step-seconds "$STEP_SECONDS" \
  | tee "$run_dir/features-second.json"

read -r first_targets first_planned first_inserted first_existing \
  second_targets second_planned second_inserted second_existing < <(
  "$PY" - "$run_dir/features-first.json" "$run_dir/features-second.json" <<'PY'
import json
import sys
from pathlib import Path

first = json.loads(Path(sys.argv[1]).read_text())
second = json.loads(Path(sys.argv[2]).read_text())
print(
    int(first["targets_considered"]),
    int(first["planned_rows"]),
    int(first["inserted"]),
    int(first["existing"]),
    int(second["targets_considered"]),
    int(second["planned_rows"]),
    int(second["inserted"]),
    int(second["existing"]),
)
PY
)

if [[ "$first_targets" -le 0 || "$first_planned" -le 0 ]]; then
  echo "Phase 6 acceptance window produced no feature targets/rows" >&2
  exit 5
fi
if [[ "$first_targets" != "$second_targets" || "$first_planned" != "$second_planned" ]]; then
  echo "Phase 6 target set changed between immediate reruns" >&2
  exit 5
fi
if [[ $((first_inserted + first_existing)) -ne "$first_planned" ]]; then
  echo "first Phase 6 run did not account for every planned row" >&2
  exit 5
fi
if [[ "$second_inserted" != "0" || "$second_existing" != "$second_planned" ]]; then
  echo "second Phase 6 run was not an existing-only idempotent rerun" >&2
  exit 5
fi

TARGET_MARKETS=$(psql_scalar "
SELECT count(*)
FROM market_labels
WHERE market_start_at >= '$START'::timestamptz
  AND market_start_at < '$END'::timestamptz
  AND label_version = 'official-outcome-v1';")

FEATURE_ROWS=$(psql_scalar "
SELECT count(*)
FROM market_features AS f
JOIN market_labels AS l
  ON l.condition_id = f.condition_id
WHERE l.market_start_at >= '$START'::timestamptz
  AND l.market_start_at < '$END'::timestamptz
  AND l.label_version = 'official-outcome-v1'
  AND f.feature_version = 'core-v1';")

INVALID_FUTURE_CUTOFFS=$(psql_scalar "
SELECT count(*)
FROM market_features AS f
JOIN market_labels AS l
  ON l.condition_id = f.condition_id
CROSS JOIN LATERAL json_each_text(f.source_cutoffs) AS cutoff(key, value)
WHERE l.market_start_at >= '$START'::timestamptz
  AND l.market_start_at < '$END'::timestamptz
  AND l.label_version = 'official-outcome-v1'
  AND f.feature_version = 'core-v1'
  AND cutoff.value::timestamptz > f.feature_at;")

DUPLICATE_KEYS=$(psql_scalar "
SELECT count(*)
FROM (
  SELECT condition_id, feature_at, feature_version
  FROM market_features
  GROUP BY condition_id, feature_at, feature_version
  HAVING count(*) > 1
) AS duplicates;")

LABEL_KEY_VIOLATIONS=$(psql_scalar "
SELECT count(*)
FROM market_features AS f
JOIN market_labels AS l
  ON l.condition_id = f.condition_id
WHERE l.market_start_at >= '$START'::timestamptz
  AND l.market_start_at < '$END'::timestamptz
  AND l.label_version = 'official-outcome-v1'
  AND f.feature_version = 'core-v1'
  AND (
    f.features::jsonb ?| ARRAY[
      'official_outcome', 'resolved_outcome', 'start_reference', 'end_reference',
      'resolution_source', 'label_source', 'label_version',
      'source_snapshot_sha256', 'source_observed_at'
    ]
    OR f.missing_flags::jsonb ?| ARRAY[
      'official_outcome', 'resolved_outcome', 'start_reference', 'end_reference',
      'resolution_source', 'label_source', 'label_version',
      'source_snapshot_sha256', 'source_observed_at'
    ]
    OR f.source_cutoffs::jsonb ?| ARRAY[
      'official_outcome', 'resolved_outcome', 'start_reference', 'end_reference',
      'resolution_source', 'label_source', 'label_version',
      'source_snapshot_sha256', 'source_observed_at'
    ]
  );")

OFFICIAL_REFERENCE_VIOLATIONS=$(psql_scalar "
SELECT count(*)
FROM market_features AS f
JOIN market_labels AS l
  ON l.condition_id = f.condition_id
WHERE l.market_start_at >= '$START'::timestamptz
  AND l.market_start_at < '$END'::timestamptz
  AND l.label_version = 'official-outcome-v1'
  AND f.feature_version = 'core-v1'
  AND (
    NOT (f.features::jsonb ? 'official_reference_distance')
    OR f.features::jsonb -> 'official_reference_distance' <> 'null'::jsonb
    OR COALESCE((f.missing_flags->>'official_reference_missing')::boolean, false) IS NOT TRUE
  );")

if [[ "$TARGET_MARKETS" != "$second_targets" ]]; then
  echo "target market count does not match feature-generator target count" >&2
  exit 5
fi
if [[ "$FEATURE_ROWS" != "$second_planned" ]]; then
  echo "persisted feature row count does not match planned rows" >&2
  exit 5
fi
if [[ "$INVALID_FUTURE_CUTOFFS" != "0" ]]; then
  echo "found $INVALID_FUTURE_CUTOFFS feature source cutoffs after feature time" >&2
  exit 5
fi
if [[ "$DUPLICATE_KEYS" != "0" ]]; then
  echo "found $DUPLICATE_KEYS duplicate immutable feature natural keys" >&2
  exit 5
fi
if [[ "$LABEL_KEY_VIOLATIONS" != "0" ]]; then
  echo "found $LABEL_KEY_VIOLATIONS feature rows containing label/outcome keys" >&2
  exit 5
fi
if [[ "$OFFICIAL_REFERENCE_VIOLATIONS" != "0" ]]; then
  echo "found $OFFICIAL_REFERENCE_VIOLATIONS invalid V1 official-reference feature rows" >&2
  exit 5
fi

# storage_maintenance.py report is a fail-closed capacity gate.
REPORT=$(sudo -u bp "$PY" "$HOST_REPO/scripts/storage_maintenance.py" report --env-file "$ENV_FILE")
printf '%s\n' "$REPORT" > "$run_dir/storage-report.json"
read -r DISK_STATUS DISK_FREE_BYTES < <(
  printf '%s' "$REPORT" |
    "$PY" -c 'import json,sys; d=json.load(sys.stdin)["disk"]; print(d["status"], d["free_bytes"])'
)
if [[ "$DISK_STATUS" != "ok" ]]; then
  echo "disk status is $DISK_STATUS after Phase 6 acceptance" >&2
  exit 6
fi

recorder_after=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_after" != "active" ]]; then
  echo "bp-recorder is not active after Phase 6 acceptance" >&2
  exit 6
fi

cat > "$run_dir/final-summary.txt" <<EOF
VERDICT=PASS
HEAD=$actual_head
CANDIDATE_REPO=$REPO
DEPLOYED_RECORDER_REPO=$HOST_REPO
ACCEPTANCE_START=$START
ACCEPTANCE_END=$END
STEP_SECONDS=$STEP_SECONDS
TARGET_MARKETS=$TARGET_MARKETS
FEATURE_ROWS=$FEATURE_ROWS
FIRST_RUN_INSERTED=$first_inserted
FIRST_RUN_EXISTING=$first_existing
SECOND_RUN_INSERTED=$second_inserted
SECOND_RUN_EXISTING=$second_existing
INVALID_FUTURE_CUTOFFS=$INVALID_FUTURE_CUTOFFS
DUPLICATE_KEYS=$DUPLICATE_KEYS
LABEL_KEY_VIOLATIONS=$LABEL_KEY_VIOLATIONS
OFFICIAL_REFERENCE_VIOLATIONS=$OFFICIAL_REFERENCE_VIOLATIONS
DISK_STATUS=$DISK_STATUS
DISK_FREE_BYTES=$DISK_FREE_BYTES
RECORDER_BEFORE=$recorder_before
RECORDER_AFTER=$recorder_after
LIVE_TRADING_ENABLED=$live_trading
MAX_TRADE_SIZE_USD=$max_trade
MAX_DAILY_LOSS_USD=$max_loss
EOF

cat "$run_dir/final-summary.txt"
