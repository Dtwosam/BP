#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_B_READINESS_WATCH_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_GATE_B_READINESS_WATCH_ZONE:-us-east1-c}"
VM="${PHASE14_V2_GATE_B_READINESS_WATCH_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V2_GATE_B_READINESS_WATCH_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD:-e9c7afc1536880e4612cb6e3d1a7282fa37c69f5}"
ENV_FILE="${PHASE14_V2_GATE_B_READINESS_WATCH_ENV_FILE:-/etc/bp/bp.env}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE:-/mnt/bp-data/evidence/phase14-partitioned-storage-rollout-20260909T070219Z.json}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE_SHA256:-f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19}"

fail_local() {
  echo "PHASE14_V2_GATE_B_READINESS_WATCH_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail_local "deployed_head_invalid"
[[ "$ENV_FILE" == /* ]] || fail_local "env_file_must_be_absolute"
[[ "$STORAGE_EVIDENCE" == /* ]] || fail_local "storage_evidence_path_must_be_absolute"
[[ "$STORAGE_EVIDENCE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail_local "storage_evidence_sha256_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"

REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail_local "remote_main_changed"

for path in   scripts/run_v2_gate_b_readiness_watch.py   src/bp_engine/v2_research/plan.py   deploy/bp-v2-gate-b-readiness-watch.service   deploy/bp-v2-gate-b-readiness-watch.timer; do
  git cat-file -e "$HELPER_HEAD:$path" || fail_local "required_path_missing:$path"
done

command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || fail_local "gcloud_auth_missing"
gcloud config set project "$PROJECT" >/dev/null

ARCHIVE=$(mktemp /tmp/bp-v2-readiness-watch.XXXXXX.tar.gz)
cleanup_local() {
  rm -f "$ARCHIVE"
}
trap cleanup_local EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$HELPER_HEAD"
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
REMOTE_ARCHIVE="/tmp/bp-v2-readiness-watch-$HELPER_HEAD.tar.gz"

gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE"   --project="$PROJECT"   --zone="$ZONE"   --quiet

printf -v HELPER_HEAD_Q '%q' "$HELPER_HEAD"
printf -v DEPLOYED_HEAD_Q '%q' "$DEPLOYED_HEAD"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v STORAGE_EVIDENCE_Q '%q' "$STORAGE_EVIDENCE"
printf -v STORAGE_EVIDENCE_SHA256_Q '%q' "$STORAGE_EVIDENCE_SHA256"
printf -v REMOTE_ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA256_Q '%q' "$ARCHIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${PHASE14_V2_GATE_B_READINESS_WATCH_HEAD:?}"
DEPLOYED_HEAD="${PHASE14_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD:?}"
ENV_FILE="${PHASE14_V2_GATE_B_READINESS_WATCH_ENV_FILE:?}"
STORAGE_EVIDENCE="${PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE:?}"
STORAGE_EVIDENCE_SHA256="${PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE_SHA256:?}"
ARCHIVE="${PHASE14_V2_GATE_B_READINESS_WATCH_ARCHIVE:?}"
ARCHIVE_SHA256="${PHASE14_V2_GATE_B_READINESS_WATCH_ARCHIVE_SHA256:?}"

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
STATUS_DIR=/var/lib/bp/status
STATUS_FILE=$STATUS_DIR/v2-gate-b-readiness.json
SIDECAR_ROOT=/opt/bp-v2-gate-b-readiness-watch
RELEASES_DIR=$SIDECAR_ROOT/releases
RELEASE_DIR=$RELEASES_DIR/$HELPER_HEAD
CURRENT_LINK=$SIDECAR_ROOT/current
SERVICE_UNIT=bp-v2-gate-b-readiness-watch.service
TIMER_UNIT=bp-v2-gate-b-readiness-watch.timer
SERVICE_PATH=/etc/systemd/system/$SERVICE_UNIT
TIMER_PATH=/etc/systemd/system/$TIMER_UNIT
MAINTENANCE_TIMER=bp-storage-maintenance.timer
DISK_HEALTH_TIMER=bp-storage-disk-health.timer
V2_TIMER=bp-v2-forward-coverage.timer

CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)

ROLLBACK_ARMED=0
SERVICE_PREEXISTED=0
TIMER_PREEXISTED=0
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
STATUS_PREEXISTED=0
STATUS_DIR_PREEXISTED=0
CURRENT_LINK_PREEXISTED=0
RELEASE_CREATED=0
SERVICE_BACKUP=''
TIMER_BACKUP=''
STATUS_BACKUP=''
CURRENT_LINK_TARGET=''
DISK_BEFORE=''
DISK_AFTER=''
DIRECT_OUTPUT=''
EVIDENCE_TMP=''
EVIDENCE_PATH=''

fail() {
  echo "PHASE14_V2_GATE_B_READINESS_WATCH_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode=$(read_env "$path" MODE)
    live=$(read_env "$path" LIVE_TRADING_ENABLED)
    trade=$(read_env "$path" MAX_TRADE_SIZE_USD)
    loss=$(read_env "$path" MAX_DAILY_LOSS_USD)
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
  done
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
  done < <(git -c safe.directory="$REPO" -C "$REPO" status --porcelain --untracked-files=all)
}

require_services() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  for service in "$MAINTENANCE_TIMER" "$DISK_HEALTH_TIMER" "$V2_TIMER"; do
    systemctl is-active --quiet "$service" || fail "timer_not_active:$service"
  done
  systemctl is-enabled --quiet "$V2_TIMER" || fail "v2_timer_not_enabled"
}

read_recorder_workers() {
  sudo -u bp env -u RECORDER_WRITER_WORKERS     "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import sys
from bp_engine.config import Settings

print(Settings(_env_file=sys.argv[1]).recorder_writer_workers)
PY
}

require_no_gate_b_artifacts() {
  "$REPO/.venv/bin/python" - "$EVIDENCE_DIR" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

root = Path(sys.argv[1])
names = {"plan.json", "selection.json", "holdout.json", "summary.json"}
if root.exists():
    artifacts = sorted(
        path
        for run_dir in root.glob("phase14-v2-gate-b-*")
        if run_dir.is_dir()
        for path in run_dir.iterdir()
        if path.is_file() and path.name in names
    )
    if artifacts:
        raise SystemExit(f"gate_b_artifact_present:{artifacts[0]}")
PY
}

run_storage_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py"       disk-health --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "storage_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("storage health status is not ok")
if payload.get("storage_mode") != "partitioned":
    raise SystemExit("storage mode is not partitioned")
guards = payload.get("guards") or {}
for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
    if guards.get(name) is not True:
        raise SystemExit(f"storage guard not satisfied: {name}")
PY
}

capture_state() {
  if [[ -f "$SERVICE_PATH" ]]; then
    SERVICE_PREEXISTED=1
    SERVICE_BACKUP=$(mktemp /var/tmp/bp-v2-readiness-watch-service.XXXXXX)
    cp -a "$SERVICE_PATH" "$SERVICE_BACKUP"
  fi
  if [[ -f "$TIMER_PATH" ]]; then
    TIMER_PREEXISTED=1
    TIMER_BACKUP=$(mktemp /var/tmp/bp-v2-readiness-watch-timer.XXXXXX)
    cp -a "$TIMER_PATH" "$TIMER_BACKUP"
  fi
  if systemctl is-enabled --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ENABLED=1
  fi
  if systemctl is-active --quiet "$TIMER_UNIT" 2>/dev/null; then
    TIMER_WAS_ACTIVE=1
  fi
  if [[ -d "$STATUS_DIR" ]]; then
    STATUS_DIR_PREEXISTED=1
  fi
  if [[ -f "$STATUS_FILE" ]]; then
    STATUS_PREEXISTED=1
    STATUS_BACKUP=$(mktemp /var/tmp/bp-v2-readiness-watch-status.XXXXXX)
    cp -a "$STATUS_FILE" "$STATUS_BACKUP"
  fi
  if [[ -L "$CURRENT_LINK" ]]; then
    CURRENT_LINK_PREEXISTED=1
    CURRENT_LINK_TARGET=$(readlink "$CURRENT_LINK")
  elif [[ -e "$CURRENT_LINK" ]]; then
    fail "current_path_not_symlink"
  fi
}

rollback() {
  set +e
  echo "PHASE14_V2_GATE_B_READINESS_WATCH_ROLLBACK=START" >&2

  systemctl disable --now "$TIMER_UNIT" >/dev/null 2>&1 || true

  if (( SERVICE_PREEXISTED )); then
    cp -a "$SERVICE_BACKUP" "$SERVICE_PATH"
  else
    rm -f "$SERVICE_PATH"
  fi
  if (( TIMER_PREEXISTED )); then
    cp -a "$TIMER_BACKUP" "$TIMER_PATH"
  else
    rm -f "$TIMER_PATH"
  fi

  if (( CURRENT_LINK_PREEXISTED )); then
    ln -sfn "$CURRENT_LINK_TARGET" "$CURRENT_LINK"
  else
    rm -f "$CURRENT_LINK"
  fi

  if (( STATUS_PREEXISTED )); then
    cp -a "$STATUS_BACKUP" "$STATUS_FILE"
    chown bp:bp "$STATUS_FILE" >/dev/null 2>&1 || true
    chmod 0640 "$STATUS_FILE" >/dev/null 2>&1 || true
  else
    rm -f "$STATUS_FILE"
  fi

  if (( RELEASE_CREATED )); then
    rm -rf "$RELEASE_DIR"
  fi

  if (( STATUS_DIR_PREEXISTED == 0 )); then
    rmdir "$STATUS_DIR" >/dev/null 2>&1 || true
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  if (( TIMER_WAS_ENABLED )); then
    systemctl enable "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi
  if (( TIMER_WAS_ACTIVE )); then
    systemctl start "$TIMER_UNIT" >/dev/null 2>&1 || true
  fi

  echo "DEPLOYED_HEAD=$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD 2>/dev/null || true)" >&2
  echo "RECORDER_ACTIVE=$(systemctl is-active bp-recorder.service 2>/dev/null || true)" >&2
  echo "PHASE14_V2_GATE_B_READINESS_WATCH_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback
  fi
  rm -f "$ARCHIVE" "$SERVICE_BACKUP" "$TIMER_BACKUP" "$STATUS_BACKUP"     "$DISK_BEFORE" "$DISK_AFTER" "$DIRECT_OUTPUT" "$EVIDENCE_TMP"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] || fail "candidate_archive_sha256_mismatch"
[[ -r "$STORAGE_EVIDENCE" ]] || fail "storage_evidence_missing"
[[ "$(sha256sum "$STORAGE_EVIDENCE" | awk '{print $1}')" == "$STORAGE_EVIDENCE_SHA256" ]] || fail "storage_evidence_sha256_mismatch"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "unexpected_deployed_head"

validate_deployed_checkout
require_research_zero_money
require_services
[[ "$(read_recorder_workers)" == "4" ]] || fail "recorder_config_worker_count_not_4"
require_no_gate_b_artifacts

DISK_BEFORE=$(mktemp /var/tmp/bp-v2-readiness-watch-disk-before.XXXXXX.json)
run_storage_health "$DISK_BEFORE"

[[ ! -e "$RELEASE_DIR" ]] || fail "release_already_exists"
capture_state
ROLLBACK_ARMED=1

install -d -o root -g root -m 0755 "$RELEASES_DIR"
install -d -o bp -g bp -m 0750 "$STATUS_DIR"
install -d -o root -g root -m 0755 "$RELEASE_DIR"
tar -xzf "$ARCHIVE" -C "$RELEASE_DIR"
RELEASE_CREATED=1
cat > "$RELEASE_DIR/REVISION.env" <<EOF
BP_V2_GATE_B_READINESS_WATCH_HEAD=$HELPER_HEAD
BP_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD=$DEPLOYED_HEAD
EOF
chown root:root "$RELEASE_DIR/REVISION.env"
chmod 0644 "$RELEASE_DIR/REVISION.env"

for path in   scripts/run_v2_gate_b_readiness_watch.py   src/bp_engine/v2_research/plan.py   deploy/bp-v2-gate-b-readiness-watch.service   deploy/bp-v2-gate-b-readiness-watch.timer; do
  [[ -f "$RELEASE_DIR/$path" ]] || fail "release_required_path_missing:$path"
done

DIRECT_OUTPUT=$(mktemp /var/tmp/bp-v2-readiness-watch-direct.XXXXXX.txt)
if ! sudo -u bp env     MODE=research     LIVE_TRADING_ENABLED=false     MAX_TRADE_SIZE_USD=0     MAX_DAILY_LOSS_USD=0     PYTHONPATH="$RELEASE_DIR/src"     "$REPO/.venv/bin/python" "$RELEASE_DIR/scripts/run_v2_gate_b_readiness_watch.py"     --env-file "$ENV_FILE"     --safety-env-file "$SAFETY_FILE"     --evidence-dir "$EVIDENCE_DIR"     --deployed-root "$REPO"     --expected-helper-head "$HELPER_HEAD"     --expected-deployed-head "$DEPLOYED_HEAD" > "$DIRECT_OUTPUT"; then
  cat "$DIRECT_OUTPUT" >&2 || true
  fail "direct_readiness_watch_failed"
fi

grep -qx 'PHASE14_V2_GATE_B_READINESS_WATCH=PASS' "$DIRECT_OUTPUT" || fail "direct_watch_pass_marker_missing"
grep -qx 'HOLDOUT_TOUCHED=false' "$DIRECT_OUTPUT" || fail "direct_watch_holdout_marker_missing"

"$REPO/.venv/bin/python" - "$DIRECT_OUTPUT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

first = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0]
payload = json.loads(first)
if not isinstance(payload.get("ready"), bool):
    raise SystemExit("ready must be boolean")
if payload.get("labels_read") is not False:
    raise SystemExit("labels_read changed")
if payload.get("plan_artifact_written") is not False:
    raise SystemExit("plan artifact was written")
if payload.get("selection_artifact_written") is not False:
    raise SystemExit("selection artifact was written")
if payload.get("holdout_touched") is not False:
    raise SystemExit("holdout was touched")
if payload.get("gate_b_artifact_count") != 0:
    raise SystemExit("Gate B artifact count must remain zero")
if payload.get("minimum_contiguous_epoch_seconds") != 64800.0:
    raise SystemExit("minimum contiguous epoch changed")
if payload.get("required_ordinary_folds") != 3:
    raise SystemExit("ordinary fold count changed")
PY

install -o root -g root -m 0644   "$RELEASE_DIR/deploy/$SERVICE_UNIT" "$SERVICE_PATH"
install -o root -g root -m 0644   "$RELEASE_DIR/deploy/$TIMER_UNIT" "$TIMER_PATH"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
systemctl daemon-reload

systemctl reset-failed "$SERVICE_UNIT" >/dev/null 2>&1 || true
systemctl start "$SERVICE_UNIT"
[[ "$(systemctl show -p Result --value "$SERVICE_UNIT")" == "success" ]] || fail "watch_service_result_not_success"
[[ "$(systemctl show -p ExecMainStatus --value "$SERVICE_UNIT")" == "0" ]] || fail "watch_service_exit_nonzero"
[[ -r "$STATUS_FILE" ]] || fail "watch_status_file_missing"

"$REPO/.venv/bin/python" - "$STATUS_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(payload.get("ready"), bool):
    raise SystemExit("ready must be boolean")
if payload.get("labels_read") is not False:
    raise SystemExit("labels_read changed")
if payload.get("plan_artifact_written") is not False:
    raise SystemExit("plan artifact was written")
if payload.get("selection_artifact_written") is not False:
    raise SystemExit("selection artifact was written")
if payload.get("holdout_touched") is not False:
    raise SystemExit("holdout was touched")
if payload.get("gate_b_artifact_count") != 0:
    raise SystemExit("Gate B artifact count must remain zero")
PY

systemctl enable --now "$TIMER_UNIT"
systemctl is-enabled --quiet "$TIMER_UNIT" || fail "watch_timer_not_enabled"
systemctl is-active --quiet "$TIMER_UNIT" || fail "watch_timer_not_active"

require_no_gate_b_artifacts
require_research_zero_money
require_services
[[ "$(read_recorder_workers)" == "4" ]] || fail "recorder_config_worker_count_changed"
[[ "$(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)" == "$DEPLOYED_HEAD" ]] || fail "deployed_head_changed"

DISK_AFTER=$(mktemp /var/tmp/bp-v2-readiness-watch-disk-after.XXXXXX.json)
run_storage_health "$DISK_AFTER"

install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
EVIDENCE_TMP=$(mktemp /var/tmp/bp-v2-readiness-watch-install-evidence.XXXXXX.json)
"$REPO/.venv/bin/python" -   "$DIRECT_OUTPUT" "$STATUS_FILE" "$DISK_BEFORE" "$DISK_AFTER" "$EVIDENCE_TMP"   "$HELPER_HEAD" "$DEPLOYED_HEAD" "$ARCHIVE_SHA256" "$STORAGE_EVIDENCE"   "$STORAGE_EVIDENCE_SHA256" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    direct_output,
    status_file,
    disk_before,
    disk_after,
    output,
    helper_head,
    deployed_head,
    archive_sha256,
    storage_evidence,
    storage_evidence_sha256,
) = sys.argv[1:]

direct = json.loads(Path(direct_output).read_text(encoding="utf-8").splitlines()[0])
status = json.loads(Path(status_file).read_text(encoding="utf-8"))

payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": helper_head,
    "deployed_head_unchanged": deployed_head,
    "archive_sha256": archive_sha256,
    "storage_evidence": storage_evidence,
    "storage_evidence_sha256": storage_evidence_sha256,
    "release_dir": f"/opt/bp-v2-gate-b-readiness-watch/releases/{helper_head}",
    "service_unit": "bp-v2-gate-b-readiness-watch.service",
    "timer_unit": "bp-v2-gate-b-readiness-watch.timer",
    "timer_enabled": True,
    "timer_active": True,
    "manual_service_cycle_result": "success",
    "direct_readiness": direct,
    "status": status,
    "storage_before": json.loads(Path(disk_before).read_text(encoding="utf-8")),
    "storage_after": json.loads(Path(disk_after).read_text(encoding="utf-8")),
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "recorder_writer_workers": 4,
    },
    "gate_b": {
        "actions_performed": False,
        "labels_read": False,
        "plan_artifact_written": False,
        "selection_artifact_written": False,
        "holdout_touched": False,
        "artifact_count": 0,
    },
}
Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v2-gate-b-readiness-watch-install-$STAMP.json"
install -o bp -g bp -m 0640 "$EVIDENCE_TMP" "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

cat "$STATUS_FILE"
echo "EVIDENCE_FILE=$EVIDENCE_PATH"
echo "PHASE14_V2_GATE_B_READINESS_WATCH_INSTALL=PASS"
echo "HOLDOUT_TOUCHED=false"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "STORAGE_EVIDENCE=$STORAGE_EVIDENCE"
echo "STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256"
echo "ARCHIVE_SHA256=$ARCHIVE_SHA256"
echo "This helper installs a read-only two-hour readiness sidecar and systemd timer."
echo "It does not change /opt/bp, restart the recorder, run Gate B, or read the final holdout."
echo "Production installation requires separate explicit authorization."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_B_READINESS_WATCH_HEAD=$HELPER_HEAD_Q PHASE14_V2_GATE_B_READINESS_WATCH_DEPLOYED_HEAD=$DEPLOYED_HEAD_Q PHASE14_V2_GATE_B_READINESS_WATCH_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE=$STORAGE_EVIDENCE_Q PHASE14_V2_GATE_B_READINESS_WATCH_STORAGE_EVIDENCE_SHA256=$STORAGE_EVIDENCE_SHA256_Q PHASE14_V2_GATE_B_READINESS_WATCH_ARCHIVE=$REMOTE_ARCHIVE_Q PHASE14_V2_GATE_B_READINESS_WATCH_ARCHIVE_SHA256=$ARCHIVE_SHA256_Q bash"
