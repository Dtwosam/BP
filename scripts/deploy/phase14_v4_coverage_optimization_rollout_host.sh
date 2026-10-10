#!/usr/bin/env bash
set -Eeuo pipefail

# Root-side half of a NEW, separately approved V4 coverage optimization rollout.
# Never execute directly
# without the exact guarded controller approval and validated archive hashes.
[[ "$#" -eq 8 ]] || { echo 'HOST_GATE=FAIL:arguments' >&2; exit 2; }
ARCHIVE="$1"
CANDIDATE_SHA="$2"
EXPECTED_DEPLOYED="$3"
EXPECTED_OLD_TARGET="$4"
EXPECTED_OLD_CODE_SHA256="$5"
EXPECTED_ARCHIVE_SHA256="$6"
EXPECTED_HOST_SHA256="$7"
APPROVAL="$8"

fail() { echo "PHASE14_V4_COVERAGE_ROLLOUT=FAIL:$1" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail "requires_root"
command -v pgrep >/dev/null 2>&1 || fail "pgrep_required_for_writer_safety"
[[ "$CANDIDATE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "candidate_sha_invalid"
[[ "$EXPECTED_DEPLOYED" =~ ^[0-9a-f]{40}$ ]] || fail "deployed_sha_invalid"
[[ "$EXPECTED_OLD_CODE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "old_hash_invalid"
[[ "$EXPECTED_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "archive_hash_invalid"
[[ "$EXPECTED_HOST_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "host_hash_invalid"
[[ "$ARCHIVE" == /tmp/bp-v4-bounded-*/candidate.tar.gz ]] || fail "archive_path_invalid"
[[ "$EXPECTED_OLD_TARGET" == /var/lib/bp/runtime/v4-forward-e7a21462374a1c19beae878c98ca319f13fb2d69 ]] ||
  fail "old_runtime_path_invalid"
EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_COVERAGE_OPTIMIZATION:$CANDIDATE_SHA:$EXPECTED_DEPLOYED:$EXPECTED_OLD_CODE_SHA256"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "approval_invalid"
[[ "$(sha256sum "$0" | awk '{print $1}')" == "$EXPECTED_HOST_SHA256" ]] ||
  fail "host_script_sha256_changed"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$EXPECTED_ARCHIVE_SHA256" ]] ||
  fail "candidate_archive_sha256_changed"

REPO=/opt/bp
RUNTIME_ROOT=/var/lib/bp/runtime
LINK="$RUNTIME_ROOT/v4-forward-current"
VERSION_DIR="$RUNTIME_ROOT/v4-forward-$CANDIDATE_SHA"
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
SERVICE=bp-v4-forward-coverage.service
TIMER=bp-v4-forward-coverage.timer
SERVICE_PATH=/etc/systemd/system/bp-v4-forward-coverage.service
TIMER_PATH=/etc/systemd/system/bp-v4-forward-coverage.timer
EVIDENCE_ROOT=/var/lib/bp/evidence
ROLLBACK_ARMED=0
SWITCHED=0
STAGING=''
SWAP=''
EVIDENCE=''
ORIGINAL_SERVICE_HASH=''
ORIGINAL_TIMER_HASH=''
RECORDER_PID=''
PREDICTOR_PID=''
PAPER_PID=''
POSTGRES_CONTAINER_ID=''

v4_writer_running() {
  pgrep -f '[/]run_v4_forward_coverage.py' >/dev/null 2>&1
}

atomic_switch() {
  local target=$1
  SWAP="$RUNTIME_ROOT/.v4-forward-link-swap-$$"
  ln -s "$target" "$SWAP" || return 1
  mv -Tf "$SWAP" "$LINK"
  SWAP=''
}

rollback() {
  set +e
  echo 'PHASE14_V4_COVERAGE_ROLLBACK=START' >&2
  if ! systemctl stop "$TIMER" >/dev/null 2>&1 ||
     [[ "$(systemctl is-active "$TIMER" || true)" == active ]]; then
    echo 'ROLLBACK_TIMER_STOP_FAILED' >&2
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
    return 1
  fi
  # Starting the timer after validation may trigger another V4 oneshot.
  # Never repoint a runtime symlink while that DB writer is active.
  local rollback_idle=0
  local rollback_state=''
  for _ in $(seq 1 150); do
    rollback_state="$(systemctl show -P ActiveState "$SERVICE" 2>/dev/null)"
    if [[ "$rollback_state" == inactive || "$rollback_state" == failed ]]; then
      rollback_idle=1
      break
    fi
    sleep 1
  done
  if (( rollback_idle != 1 )); then
    echo 'ROLLBACK_BLOCKED_ACTIVE_V4_ONESHOT_TIMER_LEFT_STOPPED' >&2
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
    return 1
  fi
  # Direct validation may outlive timeout without a systemd unit.
  # If it does, keep the timer stopped instead of switching its runtime.
  local direct_idle=0
  for _ in $(seq 1 30); do
    if ! v4_writer_running; then direct_idle=1; break; fi
    sleep 1
  done
  if (( direct_idle != 1 )); then
    echo 'ROLLBACK_BLOCKED_DIRECT_V4_WRITER_TIMER_LEFT_STOPPED' >&2
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
    return 1
  fi
  if (( SWITCHED == 1 )); then
    if ! atomic_switch "$EXPECTED_OLD_TARGET"; then
      echo 'ROLLBACK_LINK_RESTORE_FAILED_TIMER_LEFT_STOPPED' >&2
      echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
      return 1
    fi
  fi
  if ! systemctl start "$TIMER" >/dev/null 2>&1; then
    echo 'ROLLBACK_TIMER_RESTORE_FAILED' >&2
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
    return 1
  fi
  if [[ -L "$LINK" && "$(readlink -f "$LINK")" == "$EXPECTED_OLD_TARGET" ]] &&
     [[ "$(systemctl is-active "$TIMER" || true)" == active ]]; then
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=PASS' >&2
  else
    echo 'PHASE14_V4_COVERAGE_ROLLBACK=INCOMPLETE_OPERATOR_ACTION_REQUIRED' >&2
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  if (( status != 0 && ROLLBACK_ARMED == 1 )); then rollback; fi
  [[ -z "$SWAP" ]] || rm -f "$SWAP"
  if [[ -n "$EVIDENCE" ]]; then
    echo "V4_COVERAGE_EVIDENCE_DIRECTORY=$EVIDENCE" >&2
  fi
  exit "$status"
}
trap on_exit EXIT

# Do not alter /opt/bp or the frozen V3 runtime.
[[ "$(git -c safe.directory=/opt/bp -C "$REPO" rev-parse HEAD)" == "$EXPECTED_DEPLOYED" ]] ||
  fail "deployed_checkout_changed"
[[ -L "$LINK" ]] || fail "old_runtime_not_symlink"
[[ "$(readlink -f "$LINK")" == "$EXPECTED_OLD_TARGET" ]] ||
  fail "old_runtime_target_changed"
[[ "$(sha256sum "$LINK/src/bp_engine/features/v4_forward.py" | awk '{print $1}')" == "$EXPECTED_OLD_CODE_SHA256" ]] || fail "old_runtime_code_changed"
[[ -f "$SERVICE_PATH" && -f "$TIMER_PATH" ]] || fail "v4_unit_missing"
[[ "$(systemctl show -P LoadState "$SERVICE")" == loaded ]] || fail "service_not_loaded"
[[ "$(systemctl show -P TimeoutStartUSec "$SERVICE")" == 2min ]] ||
  fail "v4_timeout_changed"
[[ "$(systemctl show -P User "$SERVICE")" == bp ]] || fail "service_user_changed"
systemctl show -P ExecStart "$SERVICE" |
  grep -Fq '/var/lib/bp/runtime/v4-forward-current/scripts/run_v4_forward_coverage.py' ||
  fail "v4_unit_exec_path_changed"
[[ "$(systemctl is-active "$TIMER" || true)" == active ]] || fail "timer_not_active"
[[ "$(systemctl is-enabled "$TIMER" || true)" == enabled ]] || fail "timer_not_enabled"
ORIGINAL_SERVICE_HASH="$(sha256sum "$SERVICE_PATH" | awk '{print $1}')"
ORIGINAL_TIMER_HASH="$(sha256sum "$TIMER_PATH" | awk '{print $1}')"

for unit in bp-postgres.service bp-recorder.service \
  bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  [[ "$(systemctl is-active "$unit" || true)" == active ]] ||
    fail "core_service_not_active:$unit"
done
RECORDER_PID="$(systemctl show -P MainPID bp-recorder.service)"
PREDICTOR_PID="$(systemctl show -P MainPID bp-v3-frozen-predictor.service)"
PAPER_PID="$(systemctl show -P MainPID bp-v3-paper-execution.service)"
[[ "$RECORDER_PID" =~ ^[1-9][0-9]*$ ]] || fail "recorder_pid_invalid"
[[ "$PREDICTOR_PID" =~ ^[1-9][0-9]*$ ]] || fail "predictor_pid_invalid"
[[ "$PAPER_PID" =~ ^[1-9][0-9]*$ ]] || fail "paper_pid_invalid"
mapfile -t postgres_ids < <(docker ps --filter label=com.docker.compose.service=postgres --format '{{.ID}}')
[[ "${#postgres_ids[@]}" == 1 ]] || fail "postgres_container_identity_ambiguous"
POSTGRES_CONTAINER_ID="${postgres_ids[0]}"

for file in "$ENV_FILE" "$SAFETY_FILE"; do
  [[ -r "$file" ]] || fail "safety_file_missing"
  for pair in MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0; do
    grep -Fxq "$pair" "$file" || fail "research_zero_money_mismatch"
  done
done

# Always use the pinned recorder checkout's Settings, not the older V4 runtime.
sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/bp/src \
  /opt/bp/.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from bp_engine.config import Settings, TradingMode
s = Settings(_env_file="/etc/bp/bp.env")
assert s.mode is TradingMode.RESEARCH
assert not s.live_trading_enabled
assert s.max_trade_size_usd == s.max_daily_loss_usd == s.max_total_exposure_usd == 0
assert s.recorder_batch_size == 100
assert s.recorder_priority_batch_size == 20
assert s.recorder_writer_workers == 4
engine = create_engine(s.database_url, connect_args={
    "options": "-c default_transaction_read_only=on "
               "-c statement_timeout=3000 -c lock_timeout=1000 "
               "-c application_name=bp-v4-bounded-rollout-safety"
})
try:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        assert c.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        assert c.execute(text("SHOW shared_buffers")).scalar_one() == "128MB"
finally:
    engine.dispose()
print("V4_COVERAGE_ROLLOUT_SAFETY_AND_DB_BASELINE=PASS", flush=True)
PY

# The safety state file must still disable automatic model promotion.
# This check reads only the pinned production checkout's source-of-truth.
/opt/bp/.venv/bin/python - <<'PY'
import json
from pathlib import Path
state=json.loads(Path("/opt/bp/PROJECT_STATE.json").read_text())
flags=[]
def walk(value):
    if isinstance(value, dict):
        for key, entry in value.items():
            if key == "automatic_promotion":
                flags.append(entry)
            walk(entry)
    elif isinstance(value, list):
        for entry in value:
            walk(entry)
walk(state)
assert flags and all(value is False for value in flags)
print("AUTOMATIC_PROMOTION=DISABLED", flush=True)
PY

# Serialize separate operators before touching the V4 runtime or timer.
exec 9>/run/lock/bp-v4-bounded-forward-rollout.lock
flock -n 9 || fail "concurrent_rollout"
[[ ! -e "$VERSION_DIR" ]] || fail "candidate_version_already_present"
# Reject unsafe archive members even if local Git source was compromised.
if tar -tzf "$ARCHIVE" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
  fail "unsafe_archive_member"
fi
STAGING="$(mktemp -d "$RUNTIME_ROOT/.v4-forward-$CANDIDATE_SHA.XXXXXXXX")"
tar -xzf "$ARCHIVE" -C "$STAGING"
grep -Fxq 'V4_FORWARD_MARKETS_PER_CYCLE = 1' \
  "$STAGING/src/bp_engine/features/v4_forward.py" || fail "not_single_market"
grep -Fq 'V4_FORWARD_STAGE=' \
  "$STAGING/src/bp_engine/features/v4_forward_cli.py" || fail "stage_logging_missing"
grep -Fq 'from bp_engine.features.v4_coverage import build_v4_forward_coverage_summary' \
  "$STAGING/src/bp_engine/features/v4_forward.py" || fail "optimized_summary_not_used"
grep -Fq 'def build_v4_forward_coverage_summary(' \
  "$STAGING/src/bp_engine/features/v4_coverage.py" || fail "optimized_summary_missing"
[[ -f "$STAGING/scripts/run_v4_forward_coverage.py" ]] ||
  fail "candidate_entrypoint_missing"
chown -R root:bp "$STAGING"
chmod -R a-w "$STAGING"
chmod -R a+rX "$STAGING"
mv "$STAGING" "$VERSION_DIR"
STAGING=''

# Keep forensic evidence in a private, version-scoped directory even on failure.
EVIDENCE="$(mktemp -d "$EVIDENCE_ROOT/phase14-v4-coverage-optimized-$CANDIDATE_SHA-XXXXXXXX")"
chmod 0750 "$EVIDENCE"
printf '%s\n' "$EXPECTED_OLD_TARGET" > "$EVIDENCE/original-runtime.txt"
printf '%s\n' "$ORIGINAL_SERVICE_HASH" > "$EVIDENCE/original-service-sha256.txt"
printf '%s\n' "$ORIGINAL_TIMER_HASH" > "$EVIDENCE/original-timer-sha256.txt"

# Stop only the V4 timer; let an already-running, DB-writing oneshot finish
# naturally before changing the symlink or starting another writer.
ROLLBACK_ARMED=1
systemctl stop "$TIMER" || fail "timer_stop_failed"
[[ "$(systemctl is-active "$TIMER" || true)" != active ]] ||
  fail "timer_remains_active"
idle=0
for _ in $(seq 1 150); do
  state="$(systemctl show -P ActiveState "$SERVICE")"
  if [[ "$state" == inactive || "$state" == failed ]]; then idle=1; break; fi
  sleep 1
done
(( idle == 1 )) || fail "old_oneshot_did_not_quiesce"
if v4_writer_running; then fail "orphan_v4_writer_before_switch"; fi
echo 'V4_OLD_ONESHOT_QUIESCED=true'

feature_count() {
  sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/bp/src \
    /opt/bp/.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from bp_engine.config import Settings
s=Settings(_env_file="/etc/bp/bp.env")
e=create_engine(s.database_url,connect_args={"options":
    "-c default_transaction_read_only=on -c statement_timeout=8000 "
    "-c lock_timeout=1000 -c application_name=bp-v4-bounded-row-audit"})
try:
    with e.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        assert c.execute(text("SHOW default_transaction_read_only")).scalar_one()=="on"
        print(c.execute(text(
            "SELECT count(*) FROM market_features "
            "WHERE feature_version='core-v4-regime-aware'"
        )).scalar_one())
finally:
    e.dispose()
PY
}

BEFORE="$(feature_count)"
[[ "$BEFORE" =~ ^[0-9]+$ ]] || fail "before_count_invalid"
printf 'FEATURE_ROWS_BEFORE=%s\n' "$BEFORE"
atomic_switch "$VERSION_DIR" || fail "symlink_switch_failed"
SWITCHED=1
[[ "$(readlink -f "$LINK")" == "$VERSION_DIR" ]] || fail "symlink_switch_not_applied"

# Direct isolated cycle with timer held stopped; feature inserts are immutable.
# Hard timeout below systemd's 120-second service limit.
if ! timeout --kill-after=5s 110s \
    sudo -n -u bp env \
      MODE=research LIVE_TRADING_ENABLED=false \
      MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 \
      PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$VERSION_DIR/src" \
      /opt/bp/.venv/bin/python \
      "$VERSION_DIR/scripts/run_v4_forward_coverage.py" \
      once --env-file "$ENV_FILE" \
      >"$EVIDENCE/cycle.json" 2>"$EVIDENCE/cycle-stages.log"; then
  cat "$EVIDENCE/cycle-stages.log" >&2 || true
  fail "bounded_validation_cycle_failed"
fi
AFTER="$(feature_count)"
[[ "$AFTER" =~ ^[0-9]+$ ]] || fail "after_count_invalid"
printf 'FEATURE_ROWS_AFTER=%s\n' "$AFTER"
# Evidence directory is root-private until the validation is complete.
/opt/bp/.venv/bin/python - "$BEFORE" "$AFTER" "$EVIDENCE/cycle.json" <<'PY'
import json, sys
from pathlib import Path
before, after = map(int, sys.argv[1:3])
cycle=json.loads(Path(sys.argv[3]).read_text())
assert cycle["epoch"]=="2026-09-20T12:40:53+00:00"
# If backlog has genuinely reached zero, the bounded validation cycle
# must be a no-op; it still runs all full-epoch safety/invariant checks and
# must exit within 110s. Never invent an eligible market or insert test rows.
assert cycle["eligible_targets"] in (0, 1)
if cycle["eligible_targets"] == 0:
    assert cycle["planned_rows"] == 0
    assert cycle["inserted"] == 0
    assert cycle["existing"] == 0
    assert cycle["remaining_pending_targets"] == 0
    assert after == before
    print("V4_ZERO_BACKLOG_NOOP_VALIDATED=true", flush=True)
else:
    assert cycle["planned_rows"] == 4
    assert 1 <= cycle["inserted"] <= 4
    assert cycle["existing"] + cycle["inserted"] == 4
    assert after - before == cycle["inserted"]
assert cycle["remaining_pending_targets"] >= 0
for key in ("future_cutoff_violation_count",
            "polymarket_predictor_key_count",
            "regime_invariant_violation_count"):
    assert cycle[key] == 0, key
for key in ("policy_selected", "training_run", "automatic_promotion"):
    assert cycle[key] is False, key
print("V4_COVERAGE_FEATURE_COMMIT_VERIFIED=true",flush=True)
PY

# Unit/timer bytes stay untouched. Do not restart recorder/Postgres/V3.
[[ "$(sha256sum "$SERVICE_PATH" | awk '{print $1}')" == "$ORIGINAL_SERVICE_HASH" ]] ||
  fail "v4_service_unit_file_changed"
[[ "$(sha256sum "$TIMER_PATH" | awk '{print $1}')" == "$ORIGINAL_TIMER_HASH" ]] ||
  fail "v4_timer_unit_file_changed"
for entry in \
  "bp-recorder.service:$RECORDER_PID" \
  "bp-v3-frozen-predictor.service:$PREDICTOR_PID" \
  "bp-v3-paper-execution.service:$PAPER_PID"; do
  unit="${entry%%:*}"
  pid="${entry#*:}"
  [[ "$(systemctl is-active "$unit" || true)" == active ]] ||
    fail "core_service_stopped:$unit"
  [[ "$(systemctl show -P MainPID "$unit")" == "$pid" ]] ||
    fail "core_service_pid_changed:$unit"
done
[[ "$(systemctl is-active bp-postgres.service || true)" == active ]] ||
  fail "postgres_service_not_active"
[[ "$(docker ps --filter label=com.docker.compose.service=postgres --format '{{.ID}}')" == "$POSTGRES_CONTAINER_ID" ]] || fail "postgres_container_changed"
[[ "$(git -c safe.directory=/opt/bp -C "$REPO" rev-parse HEAD)" == "$EXPECTED_DEPLOYED" ]] || fail "recorder_checkout_changed"
systemctl start "$TIMER" || fail "timer_restore_failed"
[[ "$(systemctl is-active "$TIMER" || true)" == active ]] ||
  fail "timer_not_active_after"
[[ "$(systemctl is-enabled "$TIMER" || true)" == enabled ]] ||
  fail "timer_not_enabled_after"

/opt/bp/.venv/bin/python - \
  "$EVIDENCE/cycle.json" "$EVIDENCE/evidence.json" \
  "$CANDIDATE_SHA" "$EXPECTED_DEPLOYED" "$EXPECTED_OLD_TARGET" \
  "$VERSION_DIR" "$BEFORE" "$AFTER" <<'PY'
import json,sys
from datetime import datetime,UTC
from pathlib import Path
cycle,out,candidate,deployed,old,new,before,after=sys.argv[1:]
data={
    "verdict":"PASS","recorded_at":datetime.now(UTC).isoformat(),
    "candidate_sha":candidate,"deployed_checkout_unchanged":deployed,
    "previous_runtime":old,"current_runtime":new,
    "v4_timer_restored_active":True,"recorder_v3_pid_continuity":True,
    "feature_rows_before":int(before),"feature_rows_after":int(after),
    "feature_rows_added":int(after)-int(before),
    "cycle":json.loads(Path(cycle).read_text()),
    "automatic_promotion":False,"live_trading_enabled":False,
}
Path(out).write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
PY
chmod -R g+rX,o-rwx "$EVIDENCE"
ROLLBACK_ARMED=0
echo "PHASE14_V4_COVERAGE_ROLLOUT=PASS"
echo "PRODUCTION_MUTATION=true"
echo "LIVE_TRADING_ENABLED=false"
echo "EVIDENCE_PATH=$EVIDENCE/evidence.json"
