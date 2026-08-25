#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
START="${PHASE5_ACCEPTANCE_START:-2026-08-24T18:00:00Z}"
END="${PHASE5_ACCEPTANCE_END:-2026-08-24T19:00:00Z}"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
COMPOSE_FILE="$HOST_REPO/docker-compose.prod.yml"
PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase5-official-labels

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
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

live_trading=$(read_env LIVE_TRADING_ENABLED)
max_trade=$(read_env MAX_TRADE_SIZE_USD)
max_loss=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$live_trading" != "false" || "$max_trade" != "0" || "$max_loss" != "0" ]]; then
  echo "Phase 5 acceptance requires live trading disabled and zero trade/loss limits" >&2
  exit 3
fi

recorder_before=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_before" != "active" ]]; then
  echo "bp-recorder is not active before Phase 5 acceptance" >&2
  exit 4
fi

install -d -o bp -g bp "$EVIDENCE_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

# Phase 5 migration is additive and idempotent. It does not alter recorder/raw tables.
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U bp -d bp < "$REPO/migrations/0005_market_labels.sql" \
  > "$run_dir/migration.txt"

candidate_python "$REPO/scripts/generate_labels.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/labels-first.json"

candidate_python "$REPO/scripts/generate_labels.py" \
  --start "$START" --end "$END" --env-file "$ENV_FILE" \
  | tee "$run_dir/labels-second.json"

read -r first_nonempty second_idempotent considered_match < <(
  "$PY" - "$run_dir/labels-first.json" "$run_dir/labels-second.json" <<'PY'
import json
import sys
from pathlib import Path

first = json.loads(Path(sys.argv[1]).read_text())
second = json.loads(Path(sys.argv[2]).read_text())
first_nonempty = int(first["inserted"]) + int(first["existing"]) > 0
second_idempotent = int(second["inserted"]) == 0 and int(second["existing"]) > 0
considered_match = (
    int(first["conditions_considered"]) == int(second["conditions_considered"])
    and int(first["skipped"]) == int(second["skipped"])
)
print(int(first_nonempty), int(second_idempotent), int(considered_match))
PY
)
if [[ "$first_nonempty" != "1" ]]; then
  echo "first Phase 5 label pass produced no resolved labels" >&2
  exit 5
fi
if [[ "$second_idempotent" != "1" ]]; then
  echo "second Phase 5 label pass was not an idempotent existing-only rerun" >&2
  exit 5
fi
if [[ "$considered_match" != "1" ]]; then
  echo "Phase 5 generation inputs changed between immediate reruns" >&2
  exit 5
fi

psql_scalar() {
  local sql=$1
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
    psql -U bp -d bp -At -c "$sql" | tr -d '[:space:]'
}

target_labels=$(psql_scalar "
SELECT count(*)
FROM market_labels
WHERE market_start_at >= '$START'::timestamptz
  AND market_start_at < '$END'::timestamptz
  AND label_version = 'official-outcome-v1';")

invalid_leakage=$(psql_scalar "
SELECT count(*)
FROM market_labels
WHERE market_start_at >= '$START'::timestamptz
  AND market_start_at < '$END'::timestamptz
  AND source_observed_at < market_end_at;")

invalid_reference_prices=$(psql_scalar "
SELECT count(*)
FROM market_labels
WHERE market_start_at >= '$START'::timestamptz
  AND market_start_at < '$END'::timestamptz
  AND label_version = 'official-outcome-v1'
  AND (start_reference IS NOT NULL OR end_reference IS NOT NULL);")

invalid_contract=$(psql_scalar "
SELECT count(*)
FROM market_labels
WHERE market_start_at >= '$START'::timestamptz
  AND market_start_at < '$END'::timestamptz
  AND (
    label_version <> 'official-outcome-v1'
    OR label_source <> 'polymarket_gamma_snapshot'
    OR official_outcome NOT IN ('Up', 'Down')
  );")

missing_provenance=$(psql_scalar "
SELECT count(*)
FROM market_labels AS l
LEFT JOIN polymarket_market_snapshots AS s
  ON s.condition_id = l.condition_id
 AND s.payload_sha256 = l.source_snapshot_sha256
 AND s.downloaded_at = l.source_observed_at
WHERE l.market_start_at >= '$START'::timestamptz
  AND l.market_start_at < '$END'::timestamptz
  AND l.label_version = 'official-outcome-v1'
  AND s.id IS NULL;")

duplicate_keys=$(psql_scalar "
SELECT count(*)
FROM (
  SELECT condition_id, label_version
  FROM market_labels
  GROUP BY condition_id, label_version
  HAVING count(*) > 1
) AS duplicates;")

if [[ "$target_labels" -le 0 ]]; then
  echo "no Phase 5 labels persisted for acceptance window" >&2
  exit 5
fi
if [[ "$invalid_leakage" != "0" ]]; then
  echo "found $invalid_leakage labels whose source evidence predates market end" >&2
  exit 5
fi
if [[ "$invalid_reference_prices" != "0" ]]; then
  echo "V1 contains non-NULL official reference prices without first-party verification" >&2
  exit 5
fi
if [[ "$invalid_contract" != "0" ]]; then
  echo "found $invalid_contract labels outside the Phase 5 label contract" >&2
  exit 5
fi
if [[ "$missing_provenance" != "0" ]]; then
  echo "found $missing_provenance labels without an exact source snapshot provenance join" >&2
  exit 5
fi
if [[ "$duplicate_keys" != "0" ]]; then
  echo "found $duplicate_keys duplicate immutable label natural keys" >&2
  exit 5
fi

recorder_after=$(systemctl is-active bp-recorder || true)
if [[ "$recorder_after" != "active" ]]; then
  echo "bp-recorder is not active after Phase 5 acceptance" >&2
  exit 6
fi

cat > "$run_dir/final-summary.txt" <<EOF
VERDICT=PASS
HEAD=$actual_head
CANDIDATE_REPO=$REPO
DEPLOYED_RECORDER_REPO=$HOST_REPO
ACCEPTANCE_START=$START
ACCEPTANCE_END=$END
LIVE_TRADING_ENABLED=$live_trading
MAX_TRADE_SIZE_USD=$max_trade
MAX_DAILY_LOSS_USD=$max_loss
RECORDER_BEFORE=$recorder_before
RECORDER_AFTER=$recorder_after
FIRST_RUN_NONEMPTY=$first_nonempty
SECOND_RUN_IDEMPOTENT=$second_idempotent
CONSIDERED_MATCH=$considered_match
TARGET_LABELS=$target_labels
INVALID_LEAKAGE=$invalid_leakage
INVALID_REFERENCE_PRICES=$invalid_reference_prices
INVALID_CONTRACT=$invalid_contract
MISSING_PROVENANCE=$missing_provenance
DUPLICATE_KEYS=$duplicate_keys
EOF

cat "$run_dir/final-summary.txt"
