#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_PARTITIONED_STORAGE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_PARTITIONED_STORAGE_ZONE:-us-east1-c}"
VM="${PHASE14_PARTITIONED_STORAGE_VM:-bp-recorder}"
BRANCH="${PHASE14_PARTITIONED_STORAGE_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_PARTITIONED_STORAGE_HEAD:-}"
EXPECTED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_FROM_HEAD:-}"
APPROVED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_APPROVED_FROM_HEAD:-}"
APPROVED_HEAD="${PHASE14_PARTITIONED_STORAGE_APPROVED_HEAD:-}"
ENV_FILE="${PHASE14_PARTITIONED_STORAGE_ENV_FILE:-/etc/bp/bp.env}"
MIN_FREE_GIB="${PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB:-40}"
PREFLIGHT_VERIFIED="${PHASE14_PARTITIONED_STORAGE_PREFLIGHT_VERIFIED:-}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_FROM_HEAD must be the exact 40-character expected deployed SHA" >&2
  exit 2
fi
if [[ ! "$APPROVED_FROM_HEAD" =~ ^[0-9a-f]{40}$ || ! "$APPROVED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "migration_approval_missing_or_invalid" >&2
  exit 2
fi
[[ "$APPROVED_FROM_HEAD" == "$EXPECTED_FROM_HEAD" ]] || {
  echo "migration_approval_missing_or_invalid" >&2
  exit 2
}
[[ "$APPROVED_HEAD" == "$EXPECTED_HEAD" ]] || {
  echo "migration_approval_missing_or_invalid" >&2
  exit 2
}
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
if [[ -z "$PREFLIGHT_VERIFIED" || ! -r "$PREFLIGHT_VERIFIED" ]]; then
  echo "verified_preflight_missing" >&2
  exit 2
fi
if [[ "$PREFLIGHT_VERIFIED" != /* ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_PREFLIGHT_VERIFIED must be an absolute path" >&2
  exit 2
fi

PREFLIGHT_VERIFIED_SHA256=$(sha256sum "$PREFLIGHT_VERIFIED" | awk '{print $1}')
python - "$PREFLIGHT_VERIFIED" "$EXPECTED_FROM_HEAD" "$EXPECTED_HEAD" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

path, expected_from_head, expected_head = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
if payload.get("verdict") != "PASS":
    raise SystemExit("verified preflight verdict is not PASS")
if payload.get("from_head") != expected_from_head:
    raise SystemExit("verified preflight FROM_HEAD mismatch")
if payload.get("head") != expected_head:
    raise SystemExit("verified preflight HEAD mismatch")
if payload.get("remote_head") != expected_head:
    raise SystemExit("verified preflight REMOTE_HEAD mismatch")
if payload.get("mutations_performed") is not False:
    raise SystemExit("verified preflight reported mutations")
if payload.get("recorder_state") != "stopped":
    raise SystemExit("verified preflight recorder state is not stopped")
if payload.get("storage_shape") != "legacy_unmigrated":
    raise SystemExit("verified preflight storage shape is not legacy_unmigrated")
PY

gcloud config set project "$PROJECT" >/dev/null

printf -v HEAD_Q '%q' "$EXPECTED_HEAD"
printf -v FROM_Q '%q' "$EXPECTED_FROM_HEAD"
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_Q '%q' "$ENV_FILE"
printf -v FREE_Q '%q' "$MIN_FREE_GIB"
printf -v PREFLIGHT_SHA_Q '%q' "$PREFLIGHT_VERIFIED_SHA256"

read -r -d '' WORKER <<'WORKER_EOF' || true
set -Eeuo pipefail

SHA="${PHASE14_PARTITIONED_STORAGE_HEAD:?}"
EXPECTED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_FROM_HEAD:?}"
BRANCH="${PHASE14_PARTITIONED_STORAGE_BRANCH:?}"
ENV_FILE="${PHASE14_PARTITIONED_STORAGE_ENV_FILE:?}"
MIN_FREE_GIB="${PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB:?}"
PREFLIGHT_VERIFIED_SHA256="${PHASE14_PARTITIONED_STORAGE_PREFLIGHT_SHA256:?}"

REPO=/opt/bp
PYTHON="$REPO/.venv/bin/python"
RECORDER_UNIT=bp-recorder.service
STORAGE_HEALTH_PATH=/mnt/bp-data
STORAGE_ARCHIVE_DIR=/mnt/bp-data/archive/raw
EVIDENCE_DIR=/mnt/bp-data/evidence
ROLLBACK_ARMED=0
RECORDER_RESTARTED=false
ROLLBACK_MATERIAL_RETAINED=true
OLD_HEAD=""
OLD_BRANCH=""
ENV_BACKUP=""
APPLY_JSON=""
VERIFY_JSON=""
MAINTENANCE_JSON=""
DISK_JSON=""
ARCHIVE_EVIDENCE=""
POSTGRES_DATA_SOURCE=""
MIGRATION_FREE_BYTES=""
MIGRATION_RAW_TOTAL_BYTES=""
MIGRATION_REQUIRED_FREE_BYTES=""
PARTITION_BYTES_BEFORE_MAINTENANCE=""
PARTITION_BYTES_AFTER_MAINTENANCE=""
PARTITION_BYTES_RELEASED=""

MANAGED_UNITS=(
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
  bp-v2-forward-coverage.service
  bp-v2-forward-coverage.timer
  bp-storage-maintenance.service
  bp-storage-maintenance.timer
  bp-storage-disk-health.service
  bp-storage-disk-health.timer
)
declare -A WAS_ACTIVE
declare -A WAS_ENABLED

fail() {
  echo "PHASE14_PARTITIONED_STORAGE_ROLLOUT=FAIL" >&2
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

validate_automatic_promotion_false() {
  git -C "$REPO" show "$SHA:PROJECT_STATE.json" | "$PYTHON" -c '
import json
import sys

payload = json.load(sys.stdin)
values = []

def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "automatic_promotion":
                values.append(item)
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)

walk(payload)
if not values or any(item is not False for item in values):
    raise SystemExit("candidate automatic_promotion state is not uniformly false")
'
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

validate_rollout_scope() {
  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      PROJECT_STATE.json|docs/*|tests/*|.github/workflows/ci.yml|deploy/bp.env.example|.env.example)
        ;;
      src/bp_engine/storage/*|src/bp_engine/config.py|src/bp_engine/recorder/service.py|src/bp_engine/recorder/writer.py)
        ;;
      src/bp_engine/collectors/reliability.py|src/bp_engine/collectors/websocket_runner.py)
        ;;
      scripts/storage_maintenance.py|scripts/deploy/bootstrap_ubuntu.sh|scripts/deploy/ensure_storage_indexes.py|scripts/deploy/ensure_phase12_replay_indexes.py)
        ;;
      scripts/deploy/migrate_partitioned_raw_storage.py|scripts/deploy/phase14_partitioned_storage_rollout_cloudshell.sh)
        ;;
      *)
        fail "unexpected_rollout_path:$path"
        ;;
    esac
  done < <(git -C "$REPO" diff --name-only "$OLD_HEAD" "$SHA")
}

require_recorder_stopped() {
  if systemctl is-active --quiet "$RECORDER_UNIT"; then
    fail "recorder_must_be_stopped"
  fi
}

capture_unit_state() {
  local unit
  for unit in "${MANAGED_UNITS[@]}"; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      WAS_ACTIVE["$unit"]=1
    else
      WAS_ACTIVE["$unit"]=0
    fi
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
      WAS_ENABLED["$unit"]=1
    else
      WAS_ENABLED["$unit"]=0
    fi
  done
}

stop_managed_units() {
  local unit
  for unit in "${MANAGED_UNITS[@]}"; do
    systemctl stop "$unit" >/dev/null 2>&1 || true
  done
}

restore_managed_units() {
  local unit
  for unit in "${MANAGED_UNITS[@]}"; do
    if [[ "${WAS_ENABLED[$unit]:-0}" == 1 ]]; then
      systemctl enable "$unit" >/dev/null 2>&1 || true
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if [[ "${WAS_ACTIVE[$unit]:-0}" == 1 ]]; then
      systemctl start "$unit" >/dev/null 2>&1 || true
    fi
  done
}

set_env_value() {
  local key=$1
  local value=$2
  "$PYTHON" - "$ENV_FILE" "$key" "$value" <<'PY'
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
seen = False
for line in lines:
    if line.startswith(f"{key}="):
        updated.append(f"{key}={value}")
        seen = True
    else:
        updated.append(line)
if not seen:
    updated.append(f"{key}={value}")

fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(updated) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_name, path.stat().st_mode)
    os.chown(temp_name, path.stat().st_uid, path.stat().st_gid)
    os.replace(temp_name, path)
finally:
    Path(temp_name).unlink(missing_ok=True)
PY
}

verify_dedicated_data_filesystem() {
  mountpoint -q /mnt/bp-data || fail "dedicated_data_mount_missing"
  [[ -d "$STORAGE_ARCHIVE_DIR" ]] || fail "canonical_archive_directory_missing"
  [[ -d "$EVIDENCE_DIR" ]] || fail "storage_evidence_directory_missing"

  local free_bytes required_bytes
  free_bytes=$(df --output=avail -B1 /mnt/bp-data | tail -n1 | tr -d ' ')
  required_bytes=$((MIN_FREE_GIB * 1024 * 1024 * 1024))
  (( free_bytes > required_bytes )) || fail "dedicated_data_free_space_below_required_headroom"

  local container_id
  container_id=$(docker compose     --env-file "$ENV_FILE"     -f "$REPO/docker-compose.prod.yml"     ps -q postgres)
  [[ -n "$container_id" ]] || fail "postgres_container_missing"
  POSTGRES_DATA_SOURCE=$(docker inspect "$container_id"     --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}')
  [[ -n "$POSTGRES_DATA_SOURCE" && -e "$POSTGRES_DATA_SOURCE" ]]     || fail "postgres_data_source_missing"

  local data_device archive_device protected_device
  data_device=$(stat -c %d "$POSTGRES_DATA_SOURCE")
  archive_device=$(stat -c %d "$STORAGE_ARCHIVE_DIR")
  protected_device=$(stat -c %d /mnt/bp-data)

  [[ "$data_device" == "$protected_device" ]]     || fail "postgres_data_not_on_dedicated_filesystem"
  [[ "$archive_device" == "$protected_device" ]]     || fail "archive_not_on_dedicated_filesystem"
  [[ "$data_device" == "$archive_device" ]]     || fail "postgres_and_archive_filesystems_differ"
}

verify_unmigrated_storage_shape() {
  local postgres_user postgres_db row
  local raw_partitioned legacy_table_present dedupe_table_present

  postgres_user=$(read_env POSTGRES_USER)
  postgres_db=$(read_env POSTGRES_DB)
  postgres_user=${postgres_user:-bp}
  postgres_db=${postgres_db:-bp}

  row=$(docker compose \
    --env-file "$ENV_FILE" \
    -f "$REPO/docker-compose.prod.yml" \
    exec -T postgres \
    psql -U "$postgres_user" -d "$postgres_db" -At -F '|' -c "
      SELECT
        EXISTS (
          SELECT 1
          FROM pg_partitioned_table p
          JOIN pg_class c ON c.oid = p.partrelid
          WHERE c.oid = 'public.raw_market_events'::regclass
        ),
        to_regclass('public.raw_market_events_legacy') IS NOT NULL,
        to_regclass('public.raw_event_dedupe') IS NOT NULL;
    ") || fail "postgres_storage_shape_query_failed"

  IFS='|' read -r raw_partitioned legacy_table_present dedupe_table_present <<< "$row"
  [[ "$raw_partitioned" == "f" ]] || fail "raw_storage_already_partitioned"
  [[ "$legacy_table_present" == "f" ]] || fail "rollback_legacy_table_already_present"
  [[ "$dedupe_table_present" == "f" ]] || fail "dedupe_ledger_already_present"
}

verify_migration_headroom() {
  local postgres_user postgres_db raw_total_bytes free_bytes
  local critical_reserve_bytes configured_minimum_bytes required_free_bytes

  postgres_user=$(read_env POSTGRES_USER)
  postgres_db=$(read_env POSTGRES_DB)
  postgres_user=${postgres_user:-bp}
  postgres_db=${postgres_db:-bp}

  raw_total_bytes=$(docker compose \
    --env-file "$ENV_FILE" \
    -f "$REPO/docker-compose.prod.yml" \
    exec -T postgres \
    psql -U "$postgres_user" -d "$postgres_db" -At -c \
    "SELECT pg_total_relation_size('public.raw_market_events')::bigint;") \
    || fail "postgres_raw_relation_size_query_failed"
  raw_total_bytes=$(printf '%s' "$raw_total_bytes" | tr -d '[:space:]')
  [[ "$raw_total_bytes" =~ ^[0-9]+$ ]] || fail "invalid_raw_relation_size"

  free_bytes=$(df --output=avail -B1 /mnt/bp-data | tail -n1 | tr -d ' ')
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || fail "invalid_dedicated_data_free_bytes"

  critical_reserve_bytes=$((15 * 1024 * 1024 * 1024))
  configured_minimum_bytes=$((MIN_FREE_GIB * 1024 * 1024 * 1024))
  required_free_bytes=$((raw_total_bytes + critical_reserve_bytes))
  if (( required_free_bytes < configured_minimum_bytes )); then
    required_free_bytes=$configured_minimum_bytes
  fi

  MIGRATION_FREE_BYTES=$free_bytes
  MIGRATION_RAW_TOTAL_BYTES=$raw_total_bytes
  MIGRATION_REQUIRED_FREE_BYTES=$required_free_bytes
  (( free_bytes >= required_free_bytes )) || fail "insufficient_migration_headroom"
}

partition_relation_bytes() {
  local postgres_user postgres_db value
  postgres_user=$(read_env POSTGRES_USER)
  postgres_db=$(read_env POSTGRES_DB)
  postgres_user=${postgres_user:-bp}
  postgres_db=${postgres_db:-bp}

  value=$(docker compose \
    --env-file "$ENV_FILE" \
    -f "$REPO/docker-compose.prod.yml" \
    exec -T postgres \
    psql -U "$postgres_user" -d "$postgres_db" -At -c "
      SELECT COALESCE(sum(pg_total_relation_size(child.oid)), 0)::bigint
      FROM pg_inherits
      JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
      JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
      JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
      WHERE parent_ns.nspname = current_schema()
        AND parent.relname = 'raw_market_events';
    ") || fail "postgres_partition_relation_size_query_failed"
  value=$(printf '%s' "$value" | tr -d '[:space:]')
  [[ "$value" =~ ^[0-9]+$ ]] || fail "invalid_partition_relation_bytes"
  printf '%s\n' "$value"
}

verify_archive_evidence() {
  ARCHIVE_EVIDENCE=$(ls -1t "$EVIDENCE_DIR"/phase14-storage-recovery-24-48h-*.json 2>/dev/null | head -n1 || true)
  [[ -n "$ARCHIVE_EVIDENCE" ]] || fail "verified_24_48h_archive_evidence_missing"

  "$PYTHON" - "$ARCHIVE_EVIDENCE" <<'PY'
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
PY
}

rollback_partitioned_storage() {
  set +e
  echo "PHASE14_PARTITIONED_STORAGE_ROLLBACK=START" >&2
  stop_managed_units

  if [[ -x "$PYTHON" && -f "$REPO/scripts/deploy/migrate_partitioned_raw_storage.py" ]]; then
    "$PYTHON" $REPO/scripts/deploy/migrate_partitioned_raw_storage.py rollback       --env-file "$ENV_FILE" >&2 || true
  fi

  if [[ -n "$OLD_BRANCH" ]]; then
    git -C "$REPO" checkout --force "$OLD_BRANCH" >/dev/null 2>&1 || true
    git -C "$REPO" reset --hard "$OLD_HEAD" >/dev/null 2>&1 || true
  elif [[ -n "$OLD_HEAD" ]]; then
    git -C "$REPO" checkout --detach --force "$OLD_HEAD" >/dev/null 2>&1 || true
  fi

  if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
    cp -a "$ENV_BACKUP" "$ENV_FILE"
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  restore_managed_units
  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  echo "RECORDER_RESTARTED=false" >&2
  echo "PHASE14_PARTITIONED_STORAGE_ROLLBACK=COMPLETE" >&2
  set -e
}

on_exit() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 && ROLLBACK_ARMED == 1 )); then
    rollback_partitioned_storage
  fi
  rm -f "${ENV_BACKUP:-}" "${APPLY_JSON:-}" "${VERIFY_JSON:-}"     "${MAINTENANCE_JSON:-}" "${DISK_JSON:-}"
  exit "$rc"
}
trap on_exit EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -x "$PYTHON" ]] || fail "python_runtime_missing"
validate_deployed_checkout
require_research_zero_money
require_recorder_stopped
verify_dedicated_data_filesystem
verify_archive_evidence
verify_unmigrated_storage_shape
verify_migration_headroom

OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)
OLD_BRANCH=$(git -C "$REPO" symbolic-ref --quiet --short HEAD || true)
[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]] || fail "unexpected_deployed_head:$OLD_HEAD"

git -C "$REPO" fetch --quiet origin   "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
REMOTE_HEAD=$(git -C "$REPO" rev-parse "refs/remotes/origin/$BRANCH")
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "remote_candidate_head_changed"
git -C "$REPO" cat-file -e "$SHA^{commit}" || fail "candidate_commit_missing"
git -C "$REPO" merge-base --is-ancestor "$OLD_HEAD" "$SHA" || fail "candidate_not_descendant"
validate_rollout_scope
validate_automatic_promotion_false

capture_unit_state
ENV_BACKUP=$(mktemp /var/tmp/bp-partitioned-storage-env.XXXXXX)
cp -a "$ENV_FILE" "$ENV_BACKUP"
ROLLBACK_ARMED=1
stop_managed_units
require_recorder_stopped
verify_unmigrated_storage_shape
verify_migration_headroom

git -C "$REPO" checkout --detach --force "$SHA" >/dev/null
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "candidate_checkout_failed"

set_env_value STORAGE_HEALTH_PATH "$STORAGE_HEALTH_PATH"
set_env_value STORAGE_ARCHIVE_DIR "$STORAGE_ARCHIVE_DIR"
set_env_value STORAGE_MAINTENANCE_MAX_AGE_HOURS 2
require_research_zero_money

APPLY_JSON=$(mktemp /var/tmp/bp-partitioned-storage-apply.XXXXXX.json)
if ! sudo -u bp "$PYTHON" $REPO/scripts/deploy/migrate_partitioned_raw_storage.py apply     --env-file "$ENV_FILE" > "$APPLY_JSON"; then
  cat "$APPLY_JSON" >&2 || true
  fail "partitioned_storage_migration_apply_failed"
fi

VERIFY_JSON=$(mktemp /var/tmp/bp-partitioned-storage-verify.XXXXXX.json)
if ! sudo -u bp "$PYTHON" $REPO/scripts/deploy/migrate_partitioned_raw_storage.py verify     --env-file "$ENV_FILE" > "$VERIFY_JSON"; then
  cat "$VERIFY_JSON" >&2 || true
  fail "partitioned_storage_migration_verify_failed"
fi

"$PYTHON" - "$APPLY_JSON" "$VERIFY_JSON" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

apply = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
verify = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if apply.get("rollback_material_retained") is not True:
    raise SystemExit("migration did not retain rollback material")
if apply.get("rollback_table") != "raw_market_events_legacy":
    raise SystemExit("migration did not preserve raw_market_events_legacy")
if apply.get("non_raw_table_counts_before") != apply.get("non_raw_table_counts_after"):
    raise SystemExit("non-raw table counts changed during migration")
checks = verify.get("verification") or {}
if checks.get("synthetic_duplicate_suppressed") is not True:
    raise SystemExit("synthetic duplicate verification failed")
if checks.get("synthetic_partition_routing_verified") is not True:
    raise SystemExit("synthetic partition routing verification failed")
if int(checks.get("synthetic_rows_committed", -1)) != 0:
    raise SystemExit("synthetic verification committed rows")
if checks.get("current_plus_two_future_present") is not True:
    raise SystemExit("current + two future partitions are missing")
PY

PARTITION_BYTES_BEFORE_MAINTENANCE=$(partition_relation_bytes)
(( PARTITION_BYTES_BEFORE_MAINTENANCE > 0 )) || fail "partition_relation_bytes_before_maintenance_missing"

MAINTENANCE_JSON=$(mktemp /var/tmp/bp-partitioned-storage-maintenance.XXXXXX.json)
if ! sudo -u bp "$PYTHON" "$REPO/scripts/storage_maintenance.py" run     --env-file "$ENV_FILE" > "$MAINTENANCE_JSON"; then
  cat "$MAINTENANCE_JSON" >&2 || true
  fail "partitioned_storage_maintenance_cycle_failed"
fi

if ! "$PYTHON" - "$MAINTENANCE_JSON" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("maintenance did not run in partitioned mode")
if int(payload.get("partitions_retired", 0)) < 1:
    raise SystemExit("nonempty_partition_retirement_not_observed")

nonempty = []
for item in payload.get("raw_intervals") or []:
    if not isinstance(item, dict) or not item.get("partition"):
        continue
    archived_rows = int(item.get("archived_rows", 0))
    if archived_rows <= 0:
        continue
    dedupe_rows_removed = int(item.get("dedupe_rows_removed", -1))
    if dedupe_rows_removed != archived_rows:
        raise SystemExit("partition_dedupe_cleanup_mismatch")
    nonempty.append(item)

if not nonempty:
    raise SystemExit("nonempty_partition_retirement_not_observed")
PY
then
  cat "$MAINTENANCE_JSON" >&2 || true
  fail "partition_retirement_acceptance_failed"
fi

PARTITION_BYTES_AFTER_MAINTENANCE=$(partition_relation_bytes)
(( PARTITION_BYTES_AFTER_MAINTENANCE < PARTITION_BYTES_BEFORE_MAINTENANCE )) \
  || fail "partition_relation_bytes_not_released"
PARTITION_BYTES_RELEASED=$((PARTITION_BYTES_BEFORE_MAINTENANCE - PARTITION_BYTES_AFTER_MAINTENANCE))

DISK_JSON=$(mktemp /var/tmp/bp-partitioned-storage-health.XXXXXX.json)
if ! sudo -u bp "$PYTHON" "$REPO/scripts/storage_maintenance.py" disk-health     --env-file "$ENV_FILE" > "$DISK_JSON"; then
  cat "$DISK_JSON" >&2 || true
  fail "partitioned_storage_composite_health_failed"
fi
"$PYTHON" - "$DISK_JSON" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") == "critical":
    raise SystemExit("composite storage health is critical")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("composite storage health is not partitioned")
guards = payload.get("guards") or {}
if not all(guards.get(key) is True for key in (
    "maintenance_fresh",
    "current_partition_present",
    "retention_current",
)):
    raise SystemExit("composite storage guard is not fully healthy")
PY

systemctl enable --now bp-storage-maintenance.timer
systemctl enable --now bp-storage-disk-health.timer

# Restore only the services/timers that existed before the migration.
restore_managed_units
# Storage protection timers are intentionally enabled after a successful migration.
systemctl enable --now bp-storage-maintenance.timer
systemctl enable --now bp-storage-disk-health.timer
require_recorder_stopped
require_research_zero_money
validate_automatic_promotion_false
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_changed"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-partitioned-storage-rollout-$STAMP.json"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-partitioned-storage-evidence.XXXXXX.json)
"$PYTHON" -   "$APPLY_JSON" "$VERIFY_JSON" "$MAINTENANCE_JSON" "$DISK_JSON"   "$EVIDENCE_TMP" "$OLD_HEAD" "$SHA" "$ARCHIVE_EVIDENCE" "$POSTGRES_DATA_SOURCE"   "$PREFLIGHT_VERIFIED_SHA256" "$MIGRATION_FREE_BYTES" "$MIGRATION_RAW_TOTAL_BYTES" "$MIGRATION_REQUIRED_FREE_BYTES"   "$PARTITION_BYTES_BEFORE_MAINTENANCE" "$PARTITION_BYTES_AFTER_MAINTENANCE" "$PARTITION_BYTES_RELEASED" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    apply_path,
    verify_path,
    maintenance_path,
    disk_path,
    output_path,
    old_head,
    new_head,
    archive_evidence,
    postgres_data_source,
    preflight_verified_sha256,
    migration_free_bytes,
    migration_raw_total_bytes,
    migration_required_free_bytes,
    partition_bytes_before_maintenance,
    partition_bytes_after_maintenance,
    partition_bytes_released,
) = sys.argv[1:]
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "deployed_from_sha": old_head,
    "candidate_sha": new_head,
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
    },
    "storage_health_path": "/mnt/bp-data",
    "archive_dir": "/mnt/bp-data/archive/raw",
    "postgres_data_source": postgres_data_source,
    "source_archive_evidence": archive_evidence,
    "verified_preflight_sha256": preflight_verified_sha256,
    "pre_migration_headroom": {
        "free_bytes": int(migration_free_bytes),
        "raw_total_bytes": int(migration_raw_total_bytes),
        "required_free_bytes": int(migration_required_free_bytes),
        "critical_reserve_gib": 15,
    },
    "partition_retirement_acceptance": {
        "partition_bytes_before_maintenance": int(partition_bytes_before_maintenance),
        "partition_bytes_after_maintenance": int(partition_bytes_after_maintenance),
        "partition_bytes_released": int(partition_bytes_released),
        "nonempty_partition_retirement_verified": True,
    },
    "migration": json.loads(Path(apply_path).read_text(encoding="utf-8")),
    "verification": json.loads(Path(verify_path).read_text(encoding="utf-8")),
    "maintenance": json.loads(Path(maintenance_path).read_text(encoding="utf-8")),
    "storage_health": json.loads(Path(disk_path).read_text(encoding="utf-8")),
    "recorder_restarted": False,
    "rollback_material_retained": True,
}
Path(output_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
rm -f "$EVIDENCE_TMP"

ROLLBACK_ARMED=0
echo "PHASE14_PARTITIONED_STORAGE_ROLLOUT=PASS"
echo "FROM_HEAD=$OLD_HEAD"
echo "HEAD=$SHA"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
echo "PARTITION_BYTES_RELEASED=$PARTITION_BYTES_RELEASED"
echo "RECORDER_RESTARTED=false"
echo "ROLLBACK_MATERIAL_RETAINED=true"
WORKER_EOF

WORKER_B64=$(printf '%s' "$WORKER" | base64 -w0)
printf -v WORKER_B64_Q '%q' "$WORKER_B64"

read -r -d '' LAUNCHER <<LAUNCHER_EOF || true
set -Eeuo pipefail
SHORT=${PHASE14_PARTITIONED_STORAGE_HEAD:0:12}
UNIT="bp-phase14-partitioned-storage-$SHORT"
WORKER_PATH="/var/tmp/$UNIT.sh"
printf '%s' $WORKER_B64_Q | base64 -d > "$WORKER_PATH"
chmod 0700 "$WORKER_PATH"
systemd-run   --unit="$UNIT"   --description="BP Phase 14 partitioned storage rollout"   --property=Type=oneshot   --property=StandardOutput=journal   --property=StandardError=journal   --setenv=PHASE14_PARTITIONED_STORAGE_HEAD="$HEAD_Q"   --setenv=PHASE14_PARTITIONED_STORAGE_FROM_HEAD="$FROM_Q"   --setenv=PHASE14_PARTITIONED_STORAGE_BRANCH="$BRANCH_Q"   --setenv=PHASE14_PARTITIONED_STORAGE_ENV_FILE="$ENV_Q"   --setenv=PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB="$FREE_Q"   --setenv=PHASE14_PARTITIONED_STORAGE_PREFLIGHT_SHA256="$PREFLIGHT_SHA_Q"   /bin/bash "$WORKER_PATH"
echo "PHASE14_PARTITIONED_STORAGE_STARTED=PASS"
echo "UNIT=$UNIT.service"
echo "STATUS_COMMAND=sudo systemctl status $UNIT.service --no-pager -l"
echo "LOG_COMMAND=sudo journalctl -u $UNIT.service -f"
LAUNCHER_EOF

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "FROM_HEAD=$EXPECTED_FROM_HEAD"
echo "HEAD=$EXPECTED_HEAD"
echo "MIN_FREE_GIB=$MIN_FREE_GIB"
echo "PREFLIGHT_VERIFIED=$PREFLIGHT_VERIFIED"
echo "PREFLIGHT_VERIFIED_SHA256=$PREFLIGHT_VERIFIED_SHA256"
echo "Launching detached partitioned-storage rollout job; production recorder is not started."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="sudo env PHASE14_PARTITIONED_STORAGE_HEAD=$HEAD_Q PHASE14_PARTITIONED_STORAGE_FROM_HEAD=$FROM_Q PHASE14_PARTITIONED_STORAGE_BRANCH=$BRANCH_Q PHASE14_PARTITIONED_STORAGE_ENV_FILE=$ENV_Q PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB=$FREE_Q bash -s"   <<< "$LAUNCHER"
