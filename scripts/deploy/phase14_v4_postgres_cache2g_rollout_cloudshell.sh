#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_PG_CACHE2G_ROLLOUT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_PG_CACHE2G_ROLLOUT_ZONE:-us-east1-c}"
VM="${PHASE14_V4_PG_CACHE2G_ROLLOUT_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V4_PG_CACHE2G_ROLLOUT_HELPER_HEAD:-}"
APPROVAL="${PHASE14_V4_PG_CACHE2G_ROLLOUT_APPROVAL:-}"

FROM_HEAD='52b4355d6f077373b873f7a6f42bc37a20ddbc7b'
CANDIDATE_BRANCH='ops/phase14-v4-postgres-cache-candidate'
CANDIDATE_HEAD='85a10255fdb7ecb3d97f8a1b22c4251e819e3f8d'
EXPECTED_BATCH_SIZE=500
TARGET_BATCH_SIZE=100
EXPECTED_QUEUE_MAXSIZE=50000
EXPECTED_SHARED_BUFFERS=128MB
TARGET_SHARED_BUFFERS=2GB
MIN_HOST_MEM_TOTAL_BYTES=$((7 * 1024 * 1024 * 1024))
MIN_HOST_MEM_AVAILABLE_BYTES=$((4 * 1024 * 1024 * 1024))
MIN_POST_TUNE_AVAILABLE_BYTES=$((2 * 1024 * 1024 * 1024))
EXPECTED_SHARED_BUFFERS=128MB
TARGET_SHARED_BUFFERS=2GB
MIN_HOST_MEM_TOTAL_BYTES=$((7 * 1024 * 1024 * 1024))
MIN_HOST_MEM_AVAILABLE_BYTES=$((4 * 1024 * 1024 * 1024))
MIN_POST_TUNE_AVAILABLE_BYTES=$((2 * 1024 * 1024 * 1024))

COMPOSE_PATH='docker-compose.prod.yml'
RUNTIME_PATH='src/bp_engine/recorder/writer.py'
PARTITIONED_PATH='src/bp_engine/storage/partitioned_raw.py'
WRITER_TEST_PATH='tests/recorder/test_writer.py'
PARTITION_TEST_PATH='tests/storage/test_partitioned_raw_postgres.py'

fail_local() {
  echo "PHASE14_V4_PG_CACHE2G_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

git fetch --quiet origin "$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
REMOTE_CANDIDATE="$(git rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")"
[[ "$REMOTE_CANDIDATE" == "$CANDIDATE_HEAD" ]] || fail_local "candidate_branch_changed"
git merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" || fail_local "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF="$(printf '%s\n' "$COMPOSE_PATH" "$RUNTIME_PATH" "$PARTITIONED_PATH" "$WRITER_TEST_PATH" "$PARTITION_TEST_PATH" | sort)"
ACTUAL_DIFF="$(git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] || fail_local "candidate_scope_mismatch"

for path in "$COMPOSE_PATH" "$RUNTIME_PATH" "$PARTITIONED_PATH" "$WRITER_TEST_PATH" "$PARTITION_TEST_PATH"; do
  [[ "$(git rev-parse "$CANDIDATE_HEAD:$path")" == "$(git rev-parse "$HELPER_HEAD:$path")" ]] ||
    fail_local "candidate_blob_not_exact_main:$path"
done

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_PG_CACHE2G_ROLLOUT:${HELPER_HEAD}:${FROM_HEAD}:${CANDIDATE_HEAD}:${EXPECTED_BATCH_SIZE}:${TARGET_BATCH_SIZE}:${EXPECTED_SHARED_BUFFERS}:${TARGET_SHARED_BUFFERS}"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail_local "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

REPORT="$ROOT/scripts/report_v4_recorder_visibility.py"
[[ -r "$REPORT" ]] || fail_local "visibility_report_missing"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

FROM_HEAD='52b4355d6f077373b873f7a6f42bc37a20ddbc7b'
CANDIDATE_BRANCH='ops/phase14-v4-postgres-cache-candidate'
CANDIDATE_HEAD='85a10255fdb7ecb3d97f8a1b22c4251e819e3f8d'
EXPECTED_BATCH_SIZE=500
TARGET_BATCH_SIZE=100
EXPECTED_QUEUE_MAXSIZE=50000

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
V3_CURRENT=/var/lib/bp/runtime/v3-paper-current
V3_STATE=/var/lib/bp/v3-paper

POSTGRES_UNIT=bp-postgres.service
RECORDER_UNIT=bp-recorder.service
V3_PREDICTOR=bp-v3-frozen-predictor.service
V3_EXECUTION=bp-v3-paper-execution.service
MAINTENANCE_SERVICE=bp-storage-maintenance.service
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_SERVICE=bp-storage-disk-health.service
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_TIMER=bp-v2-forward-coverage.timer
V4_TIMER=bp-v4-forward-coverage.timer

MUTATION_STARTED=0
ROLLBACK_ARMED=0
BACKUP_DIR=''
DISK_BEFORE=''
DISK_AFTER=''
SOAK_FILE=''
VISIBILITY_FILE=''
HOLDOUT_BEFORE=''
HOLDOUT_AFTER=''
RECORDER_PID=''
PREDICTOR_PID=''
EXECUTION_PID=''
RECORDER_RESTARTS=''
PREDICTOR_RESTARTS=''
EXECUTION_RESTARTS=''

fail() {
  echo "PHASE14_V4_PG_CACHE2G_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

validate_deployed_checkout() {
  local entry code path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode="$(read_env "$path" MODE)"
    live="$(read_env "$path" LIVE_TRADING_ENABLED)"
    trade="$(read_env "$path" MAX_TRADE_SIZE_USD)"
    loss="$(read_env "$path" MAX_DAILY_LOSS_USD)"
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
  done
}

require_automatic_promotion_false() {
  "$REPO/.venv/bin/python" - "$REPO/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
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
if not values or any(value is not False for value in values):
    raise SystemExit("automatic_promotion must remain false")
PY
}

require_recorder_config() {
  local expected_batch_size=$1
  sudo -u bp env \
    -u RECORDER_QUEUE_MAXSIZE \
    -u RECORDER_BATCH_SIZE \
    -u RECORDER_WRITER_WORKERS \
    -u RECORDER_FLUSH_INTERVAL_SECONDS \
    "$REPO/.venv/bin/python" - "$ENV_FILE" "$expected_batch_size" "$EXPECTED_QUEUE_MAXSIZE" <<'PY'
import sys
from bp_engine.config import Settings

settings = Settings(_env_file=sys.argv[1])
expected_batch_size = int(sys.argv[2])
expected_queue_maxsize = int(sys.argv[3])
if settings.recorder_queue_maxsize != expected_queue_maxsize:
    raise SystemExit(
        "recorder queue maxsize must equal "
        f"{expected_queue_maxsize}, got {settings.recorder_queue_maxsize}"
    )
if settings.recorder_batch_size != expected_batch_size:
    raise SystemExit(
        "recorder batch size must equal "
        f"{expected_batch_size}, got {settings.recorder_batch_size}"
    )
if settings.recorder_writer_workers != 4:
    raise SystemExit(
        f"recorder writer workers must equal 4, got {settings.recorder_writer_workers}"
    )
if abs(settings.recorder_flush_interval_seconds - 0.25) > 1e-9:
    raise SystemExit(
        "recorder flush interval must equal 0.25, got "
        f"{settings.recorder_flush_interval_seconds}"
    )
print(f"RECORDER_QUEUE_MAXSIZE={settings.recorder_queue_maxsize}")
print(f"RECORDER_BATCH_SIZE={settings.recorder_batch_size}")
print(f"RECORDER_WRITER_WORKERS={settings.recorder_writer_workers}")
print(
    "RECORDER_FLUSH_INTERVAL_SECONDS="
    f"{settings.recorder_flush_interval_seconds}"
)
PY
}

set_recorder_batch_size() {
  local target=$1
  "$REPO/.venv/bin/python" - "$ENV_FILE" "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
target = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
matches = 0
for line in lines:
    if line.startswith("RECORDER_BATCH_SIZE="):
        updated.append(f"RECORDER_BATCH_SIZE={target}")
        matches += 1
    else:
        updated.append(line)
if matches > 1:
    raise SystemExit("duplicate RECORDER_BATCH_SIZE entries")
if matches == 0:
    updated.append(f"RECORDER_BATCH_SIZE={target}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
  sync -f "$ENV_FILE"
}

set_postgres_shared_buffers() {
  local target=$1
  "$REPO/.venv/bin/python" - "$ENV_FILE" "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
target = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
matches = 0
for line in lines:
    if line.startswith("POSTGRES_SHARED_BUFFERS="):
        updated.append(f"POSTGRES_SHARED_BUFFERS={target}")
        matches += 1
    else:
        updated.append(line)
if matches > 1:
    raise SystemExit("duplicate POSTGRES_SHARED_BUFFERS entries")
if matches == 0:
    updated.append(f"POSTGRES_SHARED_BUFFERS={target}")
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
  sync -f "$ENV_FILE"
}

postgres_shared_buffers() {
  local user db
  user="$(read_env "$ENV_FILE" POSTGRES_USER)"
  db="$(read_env "$ENV_FILE" POSTGRES_DB)"
  [[ -n "$user" && -n "$db" ]] || fail "postgres_identity_missing"
  docker compose --env-file "$ENV_FILE" -f "$REPO/docker-compose.prod.yml" \
    exec -T postgres psql -Atq -U "$user" -d "$db" -c 'SHOW shared_buffers;'
}

require_postgres_shared_buffers() {
  local expected=$1 actual
  actual="$(postgres_shared_buffers)"
  [[ "$actual" == "$expected" ]] || \
    fail "postgres_shared_buffers_mismatch:expected=$expected:actual=$actual"
  echo "POSTGRES_SHARED_BUFFERS=$actual"
}

require_memory_envelope() {
  local minimum_available=$1 total_kib available_kib total_bytes available_bytes
  total_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  total_bytes=$((total_kib * 1024))
  available_bytes=$((available_kib * 1024))
  echo "HOST_MEM_TOTAL_BYTES=$total_bytes"
  echo "HOST_MEM_AVAILABLE_BYTES=$available_bytes"
  (( total_bytes >= MIN_HOST_MEM_TOTAL_BYTES )) || fail "host_memory_total_too_small"
  (( available_bytes >= minimum_available )) || fail "host_memory_available_too_small"
}

restart_postgres() {
  systemctl restart "$POSTGRES_UNIT"
  systemctl is-active --quiet "$POSTGRES_UNIT" || fail "postgres_not_active_after_restart"
  local user db
  user="$(read_env "$ENV_FILE" POSTGRES_USER)"
  db="$(read_env "$ENV_FILE" POSTGRES_DB)"
  for _ in $(seq 1 30); do
    if docker compose --env-file "$ENV_FILE" -f "$REPO/docker-compose.prod.yml" \
      exec -T postgres pg_isready -U "$user" -d "$db" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  fail "postgres_not_ready_after_restart"
}

require_timer_active_enabled() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  systemctl is-active --quiet "$timer" || fail "timer_not_active:$timer"
}

require_timer_enabled_inactive() {
  local timer=$1
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  [[ "$(systemctl show -p ActiveState --value "$timer")" == "inactive" ]] || fail "timer_not_inactive:$timer"
}

wait_for_oneshot_idle_success() {
  local service=$1 timeout_seconds=$2 waited=0 active_state
  while true; do
    active_state="$(systemctl show -p ActiveState --value "$service")"
    case "$active_state" in
      inactive) break ;;
      active|activating)
        (( waited < timeout_seconds )) || fail "oneshot_wait_timeout:$service"
        sleep 5
        waited=$((waited + 5))
        ;;
      *) fail "oneshot_unexpected_state:$service:$active_state" ;;
    esac
  done
  [[ "$(systemctl show -p Result --value "$service")" == "success" ]] ||
    fail "oneshot_last_result_not_success:$service"
}

validate_unit_file() {
  local unit=$1 expected=$2 fragment dropins
  fragment="$(systemctl show -p FragmentPath --value "$unit")"
  [[ -r "$fragment" ]] || fail "unit_fragment_missing:$unit"
  cmp -s "$fragment" "$expected" || fail "unit_fragment_mismatch:$unit"
  dropins="$(systemctl show -p DropInPaths --value "$unit")"
  [[ -z "$dropins" ]] || fail "unit_dropins_present:$unit"
}

validate_units() {
  validate_unit_file "$RECORDER_UNIT" "$REPO/deploy/systemd/bp-recorder.service"
  [[ -L "$V3_CURRENT" ]] || fail "v3_current_link_missing"
  validate_unit_file "$V3_PREDICTOR" "$V3_CURRENT/deploy/bp-v3-frozen-predictor.service"
  validate_unit_file "$V3_EXECUTION" "$V3_CURRENT/deploy/bp-v3-paper-execution.service"
}

validate_v3_activation() {
  [[ -r "$V3_STATE/activation.json" ]] || fail "v3_activation_missing"
  [[ -r "$V3_STATE/frozen-model.joblib" ]] || fail "v3_model_missing"
  "$REPO/.venv/bin/python" - "$V3_STATE/activation.json" "$V3_STATE/frozen-model.joblib" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
activation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
model_sha = hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()
if activation.get("model_sha256") != model_sha:
    raise SystemExit("v3 activation/model digest mismatch")
if activation.get("prediction_version") != "v3-frozen-paper-v1":
    raise SystemExit("unexpected V3 prediction version")
if activation.get("execution_version") != "paper-execution-v3-frozen-v1":
    raise SystemExit("unexpected V3 execution version")
PY
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("storage health is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not true: {name}")
PY
}

gate_b_fingerprint() {
  "$REPO/.venv/bin/python" - "$EVIDENCE_DIR" <<'PY'
import hashlib
import sys
from pathlib import Path
root = Path(sys.argv[1])
digest = hashlib.sha256()
if root.exists():
    names = {"plan.json", "selection.json", "summary.json", "holdout-attempt.json"}
    for path in sorted((p for p in root.glob("phase14-v2-gate-b-*/*") if p.name in names), key=lambda p: str(p)):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
print(digest.hexdigest())
PY
}

run_soak() {
  SOAK_FILE="$(mktemp /var/tmp/bp-phase14-v4-postgres-cache2g-rollout-soak.XXXXXX.json)"
  if ! sudo -u bp bash -c 'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008' _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "natural_load_soak_failed"
  fi
  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("passed") is not True:
    raise SystemExit(f"soak failed: {payload.get('failures')}")
required = {"polymarket/market", "bybit/spot", "bybit/linear", "coinbase/spot"}
feeds = payload.get("feeds") or {}
missing = sorted(
    label
    for label in required
    if int((feeds.get(label) or {}).get("event_count", 0)) <= 0
)
if missing:
    raise SystemExit(f"required feeds missing events: {missing}")
for label in required:
    incidents = (payload.get("incidents") or {}).get(label) or {}
    if int(incidents.get("backpressure", 0)) != 0:
        raise SystemExit(f"backpressure recorded for {label}")
PY
}

verify_dashboard_safety() {
  local snapshot
  snapshot="$(mktemp /var/tmp/bp-phase14-v4-postgres-cache2g-rollout-dashboard.XXXXXX.json)"
  curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$snapshot" || fail "dashboard_snapshot_unavailable"
  "$REPO/.venv/bin/python" - "$snapshot" <<'PY'
import json
import sys
from pathlib import Path
snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = snapshot.get("mode") or {}
if mode.get("trading_mode") != "RESEARCH":
    raise SystemExit("dashboard left RESEARCH mode")
if mode.get("live_trading_enabled") is not False:
    raise SystemExit("dashboard reports live trading enabled")
if mode.get("execution_available") is not False:
    raise SystemExit("dashboard reports real execution available")
PY
  rm -f "$snapshot"
}

restore_generated_files() {
  [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || return 0
  if [[ -f "$BACKUP_DIR/next-env.d.ts" ]]; then
    cp -a "$BACKUP_DIR/next-env.d.ts" "$REPO/apps/dashboard/next-env.d.ts"
  fi
  if [[ -f "$BACKUP_DIR/tsconfig.json" ]]; then
    cp -a "$BACKUP_DIR/tsconfig.json" "$REPO/apps/dashboard/tsconfig.json"
  fi
}

start_chain() {
  systemctl reset-failed "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl start "$RECORDER_UNIT"
  for _ in $(seq 1 45); do systemctl is-active --quiet "$RECORDER_UNIT" && break; sleep 1; done
  systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_start"

  systemctl start "$V3_PREDICTOR"
  for _ in $(seq 1 30); do systemctl is-active --quiet "$V3_PREDICTOR" && break; sleep 1; done
  systemctl is-active --quiet "$V3_PREDICTOR" || fail "v3_predictor_not_active_after_start"

  systemctl start "$V3_EXECUTION"
  for _ in $(seq 1 30); do systemctl is-active --quiet "$V3_EXECUTION" && break; sleep 1; done
  systemctl is-active --quiet "$V3_EXECUTION" || fail "v3_execution_not_active_after_start"
}

run_visibility_acceptance() {
  local tmp
  tmp="$(mktemp -d /var/tmp/bp-v4-postgres-cache2g-rollout.XXXXXX)"
  chmod 0755 "$tmp"
  printf '%s' '__REPORT_B64__' | base64 -d > "$tmp/report_v4_recorder_visibility.py"
  chmod 0644 "$tmp/report_v4_recorder_visibility.py"
  VISIBILITY_FILE="$(mktemp /var/tmp/bp-v4-postgres-cache2g-rollout-report.XXXXXX.json)"

  timeout --signal=TERM --kill-after=5s 60s sudo -u bp env     PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src"     "$REPO/.venv/bin/python" "$tmp/report_v4_recorder_visibility.py"     --env-file "$ENV_FILE" > "$VISIBILITY_FILE"

  rm -rf "$tmp"

  if "$REPO/.venv/bin/python" - "$VISIBILITY_FILE" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.find("{")
if start < 0:
    raise SystemExit("visibility JSON missing")
report, _ = json.JSONDecoder().raw_decode(text[start:])
venues = report.get("venues") or {}
required = ("coinbase", "bybit_spot", "bybit_linear")
for venue in required:
    payload = venues.get(venue) or {}
    if int(payload.get("query_timeout_count", -1)) != 0:
        raise SystemExit(f"{venue}: visibility query timeout")
    if int(payload.get("query_failure_count", -1)) != 0:
        raise SystemExit(f"{venue}: visibility query failure")
    if int(payload.get("row_seen_count", 0)) < 40:
        raise SystemExit(f"{venue}: fewer than 40/80 samples saw a committed row")
    visible = payload.get("visible_age_seconds") or {}
    if float(visible.get("p95", 999.0)) > 2.0:
        raise SystemExit(f"{venue}: p95 committed-row age exceeds 2s")
    ready = payload.get("timestamp_window_ready_fraction")
    if ready is None or float(ready) < 0.5:
        raise SystemExit(f"{venue}: timestamp-window readiness below 50%")
PY
  then
    return 0
  fi

  echo "PHASE14_V4_PG_CACHE2G_VISIBILITY_ACCEPTANCE=FAIL" >&2
  cat "$VISIBILITY_FILE" >&2 || true
  return 1
}

rollback() {
  set +e
  echo "PHASE14_V4_PG_CACHE2G_ROLLOUT_ROLLBACK=START" >&2
  systemctl stop "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl stop "$V3_PREDICTOR" >/dev/null 2>&1 || true
  systemctl stop "$RECORDER_UNIT" >/dev/null 2>&1 || true
  git -C "$REPO" checkout --detach --force "$FROM_HEAD" >/dev/null 2>&1 || true
  restore_generated_files
  if [[ -f "$BACKUP_DIR/bp.env" ]]; then
    cp -a "$BACKUP_DIR/bp.env" "$ENV_FILE" >/dev/null 2>&1 || true
    sync -f "$ENV_FILE" >/dev/null 2>&1 || true
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart "$POSTGRES_UNIT" >/dev/null 2>&1 || true
  systemctl reset-failed "$RECORDER_UNIT" "$V3_PREDICTOR" "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl start "$RECORDER_UNIT" >/dev/null 2>&1 || true
  systemctl start "$V3_PREDICTOR" >/dev/null 2>&1 || true
  systemctl start "$V3_EXECUTION" >/dev/null 2>&1 || true
  systemctl start "$MAINTENANCE_TIMER" >/dev/null 2>&1 || true
  echo "DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)" >&2
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true)" >&2
  echo "V3_PREDICTOR_ACTIVE=$(systemctl is-active "$V3_PREDICTOR" 2>/dev/null || true)" >&2
  echo "V3_EXECUTION_ACTIVE=$(systemctl is-active "$V3_EXECUTION" 2>/dev/null || true)" >&2
  echo "RECORDER_BATCH_SIZE=$(read_env "$ENV_FILE" RECORDER_BATCH_SIZE)" >&2
  echo "POSTGRES_SHARED_BUFFERS=$(postgres_shared_buffers 2>/dev/null || true)" >&2
  echo "MAINTENANCE_TIMER_ACTIVE=$(systemctl is-active "$MAINTENANCE_TIMER" 2>/dev/null || true)" >&2
  echo "PHASE14_V4_PG_CACHE2G_ROLLOUT_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && MUTATION_STARTED == 1 && ROLLBACK_ARMED == 1 )); then
    rollback
  fi
  rm -f "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE" "$VISIBILITY_FILE"
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] || fail "unexpected_deployed_head"
validate_deployed_checkout
require_research_zero_money
require_automatic_promotion_false
require_recorder_config "$EXPECTED_BATCH_SIZE"
require_memory_envelope "$MIN_HOST_MEM_AVAILABLE_BYTES"
require_postgres_shared_buffers "$EXPECTED_SHARED_BUFFERS"
validate_units
validate_v3_activation

for service in bp-postgres.service bp-recorder.service bp-dashboard-api.service bp-dashboard-web.service bp-paper-execution.service bp-live-predictor.service bp-prospective-outcomes.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$service" || fail "required_service_not_active:$service"
done
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"

git -C "$REPO" fetch --no-tags origin "refs/heads/$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
[[ "$(git -C "$REPO" rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH")" == "$CANDIDATE_HEAD" ]] ||
  fail "candidate_head_changed"
git -C "$REPO" merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" ||
  fail "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF="$(printf '%s\n'   docker-compose.prod.yml   src/bp_engine/recorder/writer.py   src/bp_engine/storage/partitioned_raw.py   tests/recorder/test_writer.py   tests/storage/test_partitioned_raw_postgres.py | sort)"
ACTUAL_DIFF="$(git -C "$REPO" diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" | sort)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] || fail "candidate_scope_mismatch"

BACKUP_DIR="$(mktemp -d /var/tmp/bp-v4-postgres-cache2g-rollout-backup.XXXXXX)"
cp -a "$ENV_FILE" "$BACKUP_DIR/bp.env"
if ! git -C "$REPO" diff --quiet HEAD -- apps/dashboard/next-env.d.ts; then
  cp -a "$REPO/apps/dashboard/next-env.d.ts" "$BACKUP_DIR/next-env.d.ts"
fi
if ! git -C "$REPO" diff --quiet HEAD -- apps/dashboard/tsconfig.json; then
  cp -a "$REPO/apps/dashboard/tsconfig.json" "$BACKUP_DIR/tsconfig.json"
fi

MUTATION_STARTED=1
ROLLBACK_ARMED=1

systemctl stop "$MAINTENANCE_TIMER"
require_timer_enabled_inactive "$MAINTENANCE_TIMER"
wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600
wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 30

DISK_BEFORE="$(mktemp /var/tmp/bp-v4-postgres-cache2g-rollout-disk-before.XXXXXX.json)"
run_storage_health "$DISK_BEFORE"
HOLDOUT_BEFORE="$(gate_b_fingerprint)"

systemctl stop "$V3_EXECUTION"
systemctl stop "$V3_PREDICTOR"
systemctl stop "$RECORDER_UNIT"

git -C "$REPO" checkout --detach --force "$CANDIDATE_HEAD"
restore_generated_files
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "candidate_checkout_failed"
validate_deployed_checkout
set_recorder_batch_size "$TARGET_BATCH_SIZE"
set_postgres_shared_buffers "$TARGET_SHARED_BUFFERS"
require_recorder_config "$TARGET_BATCH_SIZE"
systemctl daemon-reload
restart_postgres
require_postgres_shared_buffers "$TARGET_SHARED_BUFFERS"
require_memory_envelope "$MIN_POST_TUNE_AVAILABLE_BYTES"

start_chain

RECORDER_PID="$(systemctl show -p MainPID --value "$RECORDER_UNIT")"
PREDICTOR_PID="$(systemctl show -p MainPID --value "$V3_PREDICTOR")"
EXECUTION_PID="$(systemctl show -p MainPID --value "$V3_EXECUTION")"
RECORDER_RESTARTS="$(systemctl show -p NRestarts --value "$RECORDER_UNIT")"
PREDICTOR_RESTARTS="$(systemctl show -p NRestarts --value "$V3_PREDICTOR")"
EXECUTION_RESTARTS="$(systemctl show -p NRestarts --value "$V3_EXECUTION")"

sleep 120
run_soak
verify_dashboard_safety
require_research_zero_money
require_recorder_config "$TARGET_BATCH_SIZE"
require_postgres_shared_buffers "$TARGET_SHARED_BUFFERS"
require_memory_envelope "$MIN_POST_TUNE_AVAILABLE_BYTES"
run_visibility_acceptance

[[ "$(systemctl show -p MainPID --value "$RECORDER_UNIT")" == "$RECORDER_PID" ]] || fail "recorder_pid_changed"
[[ "$(systemctl show -p MainPID --value "$V3_PREDICTOR")" == "$PREDICTOR_PID" ]] || fail "v3_predictor_pid_changed"
[[ "$(systemctl show -p MainPID --value "$V3_EXECUTION")" == "$EXECUTION_PID" ]] || fail "v3_execution_pid_changed"
[[ "$(systemctl show -p NRestarts --value "$RECORDER_UNIT")" == "$RECORDER_RESTARTS" ]] || fail "recorder_restarted_during_rollout"
[[ "$(systemctl show -p NRestarts --value "$V3_PREDICTOR")" == "$PREDICTOR_RESTARTS" ]] || fail "v3_predictor_restarted_during_rollout"
[[ "$(systemctl show -p NRestarts --value "$V3_EXECUTION")" == "$EXECUTION_RESTARTS" ]] || fail "v3_execution_restarted_during_rollout"

DISK_AFTER="$(mktemp /var/tmp/bp-v4-postgres-cache2g-rollout-disk-after.XXXXXX.json)"
run_storage_health "$DISK_AFTER"
HOLDOUT_AFTER="$(gate_b_fingerprint)"
[[ "$HOLDOUT_AFTER" == "$HOLDOUT_BEFORE" ]] || fail "gate_b_artifacts_changed"

systemctl start "$MAINTENANCE_TIMER"
require_timer_active_enabled "$MAINTENANCE_TIMER"
require_timer_active_enabled "$DISK_HEALTH_TIMER"
require_timer_active_enabled "$V2_TIMER"
require_timer_active_enabled "$V4_TIMER"

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] || fail "deployed_head_changed"
validate_deployed_checkout

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v4-postgres-cache2g-rollout-$STAMP.json"
EVIDENCE_TMP="$(mktemp /var/tmp/bp-v4-postgres-cache2g-rollout-evidence.XXXXXX.json)"
"$REPO/.venv/bin/python" -   "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE" "$VISIBILITY_FILE" "$EVIDENCE_TMP"   "$FROM_HEAD" "$CANDIDATE_HEAD" "$RECORDER_PID" "$PREDICTOR_PID" "$EXECUTION_PID" "$HOLDOUT_AFTER" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    before_path,
    after_path,
    soak_path,
    visibility_path,
    output_path,
    from_head,
    candidate_head,
    recorder_pid,
    predictor_pid,
    execution_pid,
    fingerprint,
) = sys.argv[1:]

visibility_text = Path(visibility_path).read_text(encoding="utf-8")
start = visibility_text.find("{")
visibility, _ = json.JSONDecoder().raw_decode(visibility_text[start:])

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "from_head": from_head,
    "deployed_head": candidate_head,
    "services": {
        "bp-recorder.service": {"active": True, "main_pid": int(recorder_pid)},
        "bp-v3-frozen-predictor.service": {"active": True, "main_pid": int(predictor_pid)},
        "bp-v3-paper-execution.service": {"active": True, "main_pid": int(execution_pid)},
    },
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "automatic_promotion": False,
    },
    "storage_before": json.loads(Path(before_path).read_text(encoding="utf-8")),
    "storage_after": json.loads(Path(after_path).read_text(encoding="utf-8")),
    "soak": json.loads(Path(soak_path).read_text(encoding="utf-8")),
    "visibility": visibility,
    "gate_b_artifacts_fingerprint": fingerprint,
    "recorder_batch_size": 100,
    "postgres_shared_buffers": "2GB",
    "maintenance_timer_active": True,
}
Path(output_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"
sync -f "$EVIDENCE_PATH"
rm -f "$EVIDENCE_TMP"

ROLLBACK_ARMED=0
MUTATION_STARTED=0

cat "$VISIBILITY_FILE"
echo "PHASE14_V4_PG_CACHE2G_ROLLOUT_GATE=PASS"
echo "FROM_HEAD=$FROM_HEAD"
echo "DEPLOYED_HEAD=$CANDIDATE_HEAD"
echo "RECORDER_PID=$RECORDER_PID"
echo "V3_PREDICTOR_PID=$PREDICTOR_PID"
echo "V3_EXECUTION_PID=$EXECUTION_PID"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
echo "RECORDER_BATCH_SIZE=$TARGET_BATCH_SIZE"
echo "POSTGRES_SHARED_BUFFERS=$TARGET_SHARED_BUFFERS"
echo "MAINTENANCE_TIMER_ACTIVE=active"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
REMOTE

REMOTE_SCRIPT="${REMOTE_SCRIPT/__REPORT_B64__/$REPORT_B64}"
REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "FROM_HEAD=$FROM_HEAD"
echo "CANDIDATE_BRANCH=$CANDIDATE_BRANCH"
echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
echo "EXPECTED_BATCH_SIZE=$EXPECTED_BATCH_SIZE"
echo "TARGET_BATCH_SIZE=$TARGET_BATCH_SIZE"
echo "EXPECTED_SHARED_BUFFERS=$EXPECTED_SHARED_BUFFERS"
echo "TARGET_SHARED_BUFFERS=$TARGET_SHARED_BUFFERS"
echo "This helper mutates the production checkout, PostgreSQL shared-buffer env, recorder batch-size env, and restarts PostgreSQL plus the recorder/frozen-V3 paper chain."
echo "It fails closed to the previous checkout and exact prior env/cache/batch settings, then restores automatic maintenance."
echo "Live trading remains disabled and money limits remain zero."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
