#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only production identity and safety snapshot. Never runs the A/B.
PROJECT="${PHASE14_V4_CACHE_READINESS_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_CACHE_READINESS_ZONE:-us-east1-c}"
VM="${PHASE14_V4_CACHE_READINESS_VM:-bp-recorder}"

fail() {
  printf 'PHASE14_V4_CACHE_READINESS_STATUS=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repo_missing"
cd "$ROOT"
[[ "$(git branch --show-current)" == "main" ]] || fail "not_on_main"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_worktree_not_clean"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail

printf 'PRODUCTION_VM_CHECK=read_only\n'
printf 'DEPLOYED_CHECKOUT_HEAD='
git -C /opt/bp rev-parse HEAD

echo 'CRITICAL_SERVICE_STATES'
for unit in bp-postgres.service bp-recorder.service \
  bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  active="$(systemctl is-active "$unit" || true)"
  printf '%s=%s\n' "$unit" "$active"
  [[ "$active" == active ]] || {
    echo "SERVICE_NOT_ACTIVE=$unit" >&2
    exit 1
  }
done

echo 'REQUIRED_TIMER_STATES'
for timer in bp-storage-maintenance.timer bp-storage-disk-health.timer \
  bp-v2-forward-coverage.timer bp-v4-forward-coverage.timer; do
  active="$(systemctl is-active "$timer" || true)"
  enabled="$(systemctl is-enabled "$timer" || true)"
  printf '%s=active:%s,enabled:%s\n' "$timer" "$active" "$enabled"
  [[ "$active" == active && "$enabled" == enabled ]] || exit 1
done

echo 'HOST_MEMORY_KIB'
grep -E '^(MemTotal|MemAvailable|SwapTotal):' /proc/meminfo
echo 'HOST_IO_PRESSURE'
cat /proc/pressure/io

available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
total_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
[[ "$total_kib" -ge $((7 * 1024 * 1024)) ]] || {
  echo 'INSUFFICIENT_HOST_TOTAL_RAM' >&2; exit 1;
}
[[ "$available_kib" -ge $((4 * 1024 * 1024)) ]] || {
  echo 'INSUFFICIENT_AVAILABLE_RAM_FOR_BASELINE_GATE' >&2; exit 1;
}

mapfile -t postgres_ids < <(
  sudo -n docker ps \
    --filter label=com.docker.compose.service=postgres \
    --format '{{.ID}}'
)
[[ "${#postgres_ids[@]}" == 1 ]] || {
  echo "POSTGRES_CONTAINER_COUNT=${#postgres_ids[@]}" >&2; exit 1;
}
postgres_id="${postgres_ids[0]}"

echo 'POSTGRES_CONTAINER'
sudo -n docker inspect --format \
  'compose_project={{index .Config.Labels "com.docker.compose.project"}} memory_limit_bytes={{.HostConfig.Memory}}' \
  "$postgres_id"
echo 'POSTGRES_CGROUP_MEMORY'
sudo -n docker exec "$postgres_id" sh -c '
  for name in memory.max memory.current memory.swap.max; do
    path="/sys/fs/cgroup/$name"
    if [ -r "$path" ]; then
      printf "%s=" "$name"
      cat "$path"
    fi
  done
'

ORIGINAL_HEAD=dd067b3820eb40e03049b047a9eb072b43fbcd8b
RELEASE="/var/lib/bp/runtime/v4-source-time-fresh-book-shadow-$ORIGINAL_HEAD"
VENV="/var/lib/bp/runtime/v4-paper-venv-$ORIGINAL_HEAD"

sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$RELEASE/src" "$VENV/bin/python" - <<'PY'
import json
from sqlalchemy import create_engine, text
from bp_engine.config import Settings, TradingMode

s = Settings(_env_file="/etc/bp/bp.env")
config = {
    "mode": str(s.mode),
    "live_trading_enabled": s.live_trading_enabled,
    "max_trade_size_usd": s.max_trade_size_usd,
    "max_daily_loss_usd": s.max_daily_loss_usd,
    "max_total_exposure_usd": s.max_total_exposure_usd,
    "recorder_batch_size": s.recorder_batch_size,
    "recorder_queue_maxsize": s.recorder_queue_maxsize,
    "recorder_priority_batch_size": s.recorder_priority_batch_size,
    "recorder_priority_queue_maxsize": s.recorder_priority_queue_maxsize,
    "recorder_writer_workers": s.recorder_writer_workers,
    "recorder_flush_interval_seconds": s.recorder_flush_interval_seconds,
}
print("REDACTED_RECORDER_CONFIG", json.dumps(config, sort_keys=True))
assert s.mode is TradingMode.RESEARCH
assert not s.live_trading_enabled
assert s.max_trade_size_usd == s.max_daily_loss_usd == 0
assert s.max_total_exposure_usd == 0
assert s.recorder_batch_size == 100
assert s.recorder_queue_maxsize == 50_000
assert s.recorder_priority_batch_size == 20
assert s.recorder_priority_queue_maxsize == 5_000
assert s.recorder_writer_workers == 4
assert abs(s.recorder_flush_interval_seconds - 0.25) < 1e-9

engine = create_engine(s.database_url, connect_args={
    "options": "-c default_transaction_read_only=on "
               "-c statement_timeout=2000 "
               "-c application_name=bp-v4-cache-preflight-readonly"
})
try:
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        assert connection.execute(text(
            "SHOW default_transaction_read_only"
        )).scalar_one() == "on"
        shared = connection.execute(
            text("SHOW shared_buffers")
        ).scalar_one()
        print("POSTGRES_SHARED_BUFFERS", shared)
        assert shared == "128MB", "Unexpected cache baseline"
finally:
    engine.dispose()
print("SAFETY_AND_CONFIG_GATES=PASS")
PY

echo 'PHASE14_V4_CACHE_READINESS_STATUS=PASS'
echo 'PRODUCTION_MUTATION=false'
echo 'EXPERIMENT_EXECUTED=false'
REMOTE_SCRIPT

printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'READ_ONLY_HOST_PROBE=true\n'
gcloud compute ssh "$VM" --project="$PROJECT" --zone="$ZONE" \
  --quiet --command="$REMOTE"
