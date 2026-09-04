#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_PARTITIONED_STORAGE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_PARTITIONED_STORAGE_ZONE:-us-east1-c}"
VM="${PHASE14_PARTITIONED_STORAGE_VM:-bp-recorder}"
BRANCH="${PHASE14_PARTITIONED_STORAGE_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_PARTITIONED_STORAGE_HEAD:-}"
EXPECTED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_FROM_HEAD:-}"
EXPECTED_ARCHIVE_EVIDENCE="${PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE:-}"
ENV_FILE="${PHASE14_PARTITIONED_STORAGE_ENV_FILE:-/etc/bp/bp.env}"
MIN_FREE_GIB="${PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB:-40}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_FROM_HEAD must be the exact 40-character expected deployed SHA" >&2
  exit 2
fi
if ! [[ "$EXPECTED_ARCHIVE_EVIDENCE" =~ ^/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-[0-9]{8}T[0-9]{6}Z\.json$ ]]; then
  echo "archive_evidence_binding_invalid" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_ENV_FILE must be absolute" >&2
  exit 2
fi
if ! [[ "$MIN_FREE_GIB" =~ ^[0-9]+$ ]] || (( MIN_FREE_GIB < 25 )); then
  echo "PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB must be an integer >= 25" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run this helper from Google Cloud Shell" >&2
  exit 2
fi
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

printf -v HEAD_Q '%q' "$EXPECTED_HEAD"
printf -v FROM_Q '%q' "$EXPECTED_FROM_HEAD"
printf -v ARCHIVE_EVIDENCE_Q '%q' "$EXPECTED_ARCHIVE_EVIDENCE"
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_Q '%q' "$ENV_FILE"
printf -v FREE_Q '%q' "$MIN_FREE_GIB"

read -r -d '' WORKER <<'WORKER_EOF' || true
set -Eeuo pipefail

SHA="${PHASE14_PARTITIONED_STORAGE_HEAD:?}"
EXPECTED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_FROM_HEAD:?}"
EXPECTED_ARCHIVE_EVIDENCE="${PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE:?}"
BRANCH="${PHASE14_PARTITIONED_STORAGE_BRANCH:?}"
ENV_FILE="${PHASE14_PARTITIONED_STORAGE_ENV_FILE:?}"
MIN_FREE_GIB="${PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB:?}"

REPO=/opt/bp
RECORDER_UNIT=bp-recorder.service
STORAGE_HEALTH_PATH=/mnt/bp-data
STORAGE_ARCHIVE_DIR=/mnt/bp-data/archive/raw
EVIDENCE_DIR=/mnt/bp-data/evidence
POSTGRES_DATA_SOURCE=""
ARCHIVE_EVIDENCE=""
ARCHIVE_EVIDENCE_SHA256=""
ARCHIVE_WINDOW_END=""
OLD_HEAD=""
OLD_BRANCH=""
REMOTE_HEAD=""
FREE_BYTES=""
ROOT_FREE_BYTES=""
RAW_TOTAL_BYTES=""
RAW_TOTAL_PRETTY=""
RAW_ESTIMATED_ROWS=""
RAW_PARTITIONED=""
LEGACY_TABLE_PRESENT=""
DEDUPE_TABLE_PRESENT=""
MAINTENANCE_TIMER_STATE=""
DISK_HEALTH_TIMER_STATE=""

fail() {
  echo "PHASE14_PARTITIONED_STORAGE_PREFLIGHT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

require_research_zero_money() {
  local mode
  local live_trading_enabled
  local max_trade_size_usd
  local max_daily_loss_usd

  mode=$(read_env MODE)
  live_trading_enabled=$(read_env LIVE_TRADING_ENABLED)
  max_trade_size_usd=$(read_env MAX_TRADE_SIZE_USD)
  max_daily_loss_usd=$(read_env MAX_DAILY_LOSS_USD)
  if [[ "$mode" != "research" ||         "$live_trading_enabled" != "false" ||         "$max_trade_size_usd" != "0" ||         "$max_daily_loss_usd" != "0" ]]; then
    fail "research_zero_money_boundary_not_satisfied"
  fi
}

validate_deployed_checkout() {
  local entry code path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

require_recorder_stopped() {
  if systemctl is-active --quiet "$RECORDER_UNIT"; then
    fail "recorder_must_be_stopped"
  fi
}

verify_dedicated_data_filesystem() {
  mountpoint -q /mnt/bp-data || fail "dedicated_data_mount_missing"
  [[ -d "$STORAGE_ARCHIVE_DIR" ]] || fail "canonical_archive_directory_missing"
  [[ -d "$EVIDENCE_DIR" ]] || fail "storage_evidence_directory_missing"

  FREE_BYTES=$(df --output=avail -B1 /mnt/bp-data | tail -n1 | tr -d ' ')
  ROOT_FREE_BYTES=$(df --output=avail -B1 / | tail -n1 | tr -d ' ')
  local required_bytes
  required_bytes=$((MIN_FREE_GIB * 1024 * 1024 * 1024))
  (( FREE_BYTES > required_bytes )) || fail "dedicated_data_free_space_below_required_headroom"

  local container_id
  container_id=$(docker compose     --env-file "$ENV_FILE"     -f "$REPO/docker-compose.prod.yml"     ps -q postgres)
  [[ -n "$container_id" ]] || fail "postgres_container_missing"

  POSTGRES_DATA_SOURCE=$(docker inspect "$container_id"     --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}')
  [[ -n "$POSTGRES_DATA_SOURCE" && -e "$POSTGRES_DATA_SOURCE" ]] || fail "postgres_data_source_missing"

  local data_device archive_device protected_device
  data_device=$(stat -c %d "$POSTGRES_DATA_SOURCE")
  archive_device=$(stat -c %d "$STORAGE_ARCHIVE_DIR")
  protected_device=$(stat -c %d "$STORAGE_HEALTH_PATH")

  [[ "$data_device" == "$protected_device" ]] || fail "postgres_data_not_on_dedicated_filesystem"
  [[ "$archive_device" == "$protected_device" ]] || fail "archive_not_on_dedicated_filesystem"
  [[ "$data_device" == "$archive_device" ]] || fail "postgres_and_archive_filesystems_differ"
}

verify_archive_evidence() {
  ARCHIVE_EVIDENCE="$EXPECTED_ARCHIVE_EVIDENCE"
  [[ "$ARCHIVE_EVIDENCE" =~ ^/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-[0-9]{8}T[0-9]{6}Z\.json$ ]] \
    || fail "archive_evidence_binding_invalid"
  [[ -f "$ARCHIVE_EVIDENCE" ]] || fail "verified_24_48h_archive_evidence_missing"

  ARCHIVE_WINDOW_END=$("$REPO/.venv/bin/python" - "$ARCHIVE_EVIDENCE" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
intervals = payload.get("intervals")
if int(payload.get("hours", -1)) != 24:
    raise SystemExit("archive evidence hours must equal 24")
if not isinstance(intervals, list) or len(intervals) != 24:
    raise SystemExit("archive evidence intervals must contain exactly 24 hours")

previous_end = None
for item in intervals:
    start = datetime.fromisoformat(str(item["start_at"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(item["end_at"]).replace("Z", "+00:00"))
    if end - start != timedelta(hours=1):
        raise SystemExit("archive evidence contains a non-hour interval")
    if previous_end is not None and start != previous_end:
        raise SystemExit("archive evidence intervals are not contiguous")
    previous_end = end

print(str(payload["window_end"]))
PY
  ) || fail "verified_24_48h_archive_evidence_invalid"

  ARCHIVE_EVIDENCE_SHA256=$(sha256sum "$ARCHIVE_EVIDENCE" | awk '{print $1}')
  [[ "$ARCHIVE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] \
    || fail "verified_24_48h_archive_evidence_digest_invalid"
}

capture_database_shape() {
  local row postgres_user postgres_db
  postgres_user=$(read_env POSTGRES_USER)
  postgres_db=$(read_env POSTGRES_DB)
  postgres_user=${postgres_user:-bp}
  postgres_db=${postgres_db:-bp}
  row=$(docker compose     --env-file "$ENV_FILE"     -f "$REPO/docker-compose.prod.yml"     exec -T postgres     psql -U "$postgres_user" -d "$postgres_db" -AtF $'\t' -c "
      SELECT
        pg_total_relation_size('public.raw_market_events')::bigint,
        pg_size_pretty(pg_total_relation_size('public.raw_market_events')),
        COALESCE((SELECT GREATEST(reltuples::bigint, 0) FROM pg_class WHERE oid = 'public.raw_market_events'::regclass), 0),
        EXISTS (
          SELECT 1
          FROM pg_partitioned_table p
          JOIN pg_class c ON c.oid = p.partrelid
          WHERE c.oid = 'public.raw_market_events'::regclass
        ),
        to_regclass('public.raw_market_events_legacy') IS NOT NULL,
        to_regclass('public.raw_event_dedupe') IS NOT NULL;
    ") || fail "postgres_read_only_shape_query_failed"

  IFS=$'\t' read -r     RAW_TOTAL_BYTES     RAW_TOTAL_PRETTY     RAW_ESTIMATED_ROWS     RAW_PARTITIONED     LEGACY_TABLE_PRESENT     DEDUPE_TABLE_PRESENT <<< "$row"
}


verify_migration_headroom() {
  local raw_total_bytes critical_reserve_bytes configured_minimum_bytes required_free_bytes
  raw_total_bytes=$RAW_TOTAL_BYTES
  critical_reserve_bytes=$((15 * 1024 * 1024 * 1024))
  configured_minimum_bytes=$((MIN_FREE_GIB * 1024 * 1024 * 1024))
  required_free_bytes=$((raw_total_bytes + critical_reserve_bytes))
  if (( required_free_bytes < configured_minimum_bytes )); then
    required_free_bytes=$configured_minimum_bytes
  fi
  (( FREE_BYTES >= required_free_bytes )) || fail "insufficient_migration_headroom"
}

verify_unmigrated_storage_shape() {
  [[ "$RAW_PARTITIONED" == "f" ]] || fail "raw_storage_already_partitioned"
  [[ "$LEGACY_TABLE_PRESENT" == "f" ]] || fail "rollback_legacy_table_already_present"
  [[ "$DEDUPE_TABLE_PRESENT" == "f" ]] || fail "dedupe_ledger_already_present"
}

capture_timer_state() {
  MAINTENANCE_TIMER_STATE=$(systemctl is-active bp-storage-maintenance.timer 2>/dev/null || true)
  DISK_HEALTH_TIMER_STATE=$(systemctl is-active bp-storage-disk-health.timer 2>/dev/null || true)
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"

validate_deployed_checkout
require_research_zero_money
require_recorder_stopped

OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)
OLD_BRANCH=$(git -C "$REPO" symbolic-ref --quiet --short HEAD || true)
[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]] || fail "unexpected_deployed_head:$OLD_HEAD"

REMOTE_HEAD=$(git -C "$REPO" ls-remote --exit-code origin "refs/heads/$BRANCH" | awk 'NR == 1 {print $1}')
[[ -n "$REMOTE_HEAD" ]] || fail "remote_candidate_head_missing"
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "remote_candidate_head_changed"

verify_dedicated_data_filesystem
verify_archive_evidence
capture_database_shape
verify_migration_headroom
verify_unmigrated_storage_shape
capture_timer_state

echo "PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS"
echo "FROM_HEAD=$OLD_HEAD"
echo "FROM_BRANCH=${OLD_BRANCH:-detached}"
echo "HEAD=$SHA"
echo "REMOTE_HEAD=$REMOTE_HEAD"
echo "RECORDER_STATE=stopped"
echo "POSTGRES_DATA_SOURCE=$POSTGRES_DATA_SOURCE"
echo "DEDICATED_DATA_FREE_BYTES=$FREE_BYTES"
echo "ROOT_FREE_BYTES=$ROOT_FREE_BYTES"
echo "ARCHIVE_EVIDENCE=$ARCHIVE_EVIDENCE"
echo "ARCHIVE_EVIDENCE_SHA256=$ARCHIVE_EVIDENCE_SHA256"
echo "ARCHIVE_WINDOW_END=$ARCHIVE_WINDOW_END"
echo "RAW_TOTAL_BYTES=$RAW_TOTAL_BYTES"
echo "RAW_TOTAL_PRETTY=$RAW_TOTAL_PRETTY"
echo "RAW_ESTIMATED_ROWS=$RAW_ESTIMATED_ROWS"
echo "RAW_PARTITIONED=$RAW_PARTITIONED"
echo "LEGACY_TABLE_PRESENT=$LEGACY_TABLE_PRESENT"
echo "DEDUPE_TABLE_PRESENT=$DEDUPE_TABLE_PRESENT"
echo "MAINTENANCE_TIMER_STATE=$MAINTENANCE_TIMER_STATE"
echo "DISK_HEALTH_TIMER_STATE=$DISK_HEALTH_TIMER_STATE"
echo "MUTATIONS_PERFORMED=false"
WORKER_EOF

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "FROM_HEAD=$EXPECTED_FROM_HEAD"
echo "HEAD=$EXPECTED_HEAD"
echo "MIN_FREE_GIB=$MIN_FREE_GIB"
echo "ENV_FILE=$ENV_FILE"
echo "Running read-only Phase 14 partitioned-storage production preflight."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="sudo env PHASE14_PARTITIONED_STORAGE_HEAD=$HEAD_Q PHASE14_PARTITIONED_STORAGE_FROM_HEAD=$FROM_Q PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE=$ARCHIVE_EVIDENCE_Q PHASE14_PARTITIONED_STORAGE_BRANCH=$BRANCH_Q PHASE14_PARTITIONED_STORAGE_ENV_FILE=$ENV_Q PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB=$FREE_Q bash -s"   <<< "$WORKER"
