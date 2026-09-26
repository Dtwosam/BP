#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_APPROVAL_RUNTIME_REPAIR:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_RUNTIME_REPAIR=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_telegram_approval_runtime_repair_authorization_required"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

if ! python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
listener = gate["telegram_approval_listener_install_authorization"]
activation = gate["telegram_transport_activation_authorization"]
stage = gate["telegram_transport_stage"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert listener["status"] == "RUNTIME_REPAIR_REQUIRED"
assert listener["target_host"] == "bp-recorder"
assert listener["handoff_configured"] is False
assert listener["handoff_env_present"] is False
assert listener["runtime_repair_authorized"] is True
assert activation["status"] == "AUTHORIZED_NOT_ACTIVATED"
assert activation["does_not_submit_real_order"] is True
assert stage["status"] == "PRODUCTION_STAGED_INACTIVE"
assert stage["activation_authorized"] is True
assert gate["pending_unsubmitted_intent"] is None
PY
then
  fail "source_truth_not_authorized_for_listener_runtime_repair"
fi

ARCHIVE=$(mktemp /tmp/bp-phase15-telegram-approval-repair.XXXXXX.tar.gz)
cleanup_local() {
  rm -f "$ARCHIVE"
}
trap cleanup_local EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD" \
  scripts/run_phase15_v3_canary_telegram_approval.py \
  src/bp_engine/execution/telegram_approval.py \
  deploy/bp-phase15-canary-telegram-approval.service

ARCHIVE_SHA256=$(
  python3 - "$ARCHIVE" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
) || fail "archive_sha256_failed"
[[ "$ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "archive_sha256_invalid"

REMOTE_ARCHIVE="/tmp/bp-phase15-telegram-approval-repair-$LOCAL_HEAD.tar.gz"
gcloud compute scp "$ARCHIVE" "$US_VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet ||
  fail "repair_archive_upload_failed"

printf -v HEAD_Q '%q' "$LOCAL_HEAD"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA_Q '%q' "$ARCHIVE_SHA256"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail
umask 077

HELPER_HEAD="${BP_PHASE15_HELPER_HEAD:?}"
ARCHIVE="${BP_PHASE15_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_PHASE15_ARCHIVE_SHA256:?}"

SIDECAR=/opt/bp-phase15-telegram-approval
RELEASES=$SIDECAR/releases
RELEASE=$RELEASES/$HELPER_HEAD
CURRENT=$SIDECAR/current
SERVICE=bp-phase15-canary-telegram-approval.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
ENV_PATH=/etc/bp/telegram-approval.env
HANDOFF_ENV_PATH=/etc/bp/telegram-approval-handoff.env
STATE_ROOT=/var/lib/bp/phase15-canary-telegram-approval
BACKUP=$(mktemp -d /var/tmp/bp-phase15-telegram-repair-rollback.XXXXXX)
COMMITTED=false

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_RUNTIME_REPAIR=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

cleanup() {
  status=$?
  rm -f "$ARCHIVE"
  if [[ "$COMMITTED" == "true" ]]; then
    rm -rf "$BACKUP"
    return
  fi
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  if [[ -f "$BACKUP/service" ]]; then
    install -o root -g root -m 0644 "$BACKUP/service" "$SERVICE_PATH"
  fi
  if [[ -f "$BACKUP/current-target" ]]; then
    ln -sfn "$(cat "$BACKUP/current-target")" "$CURRENT"
  fi
  systemctl daemon-reload
  if [[ -f "$BACKUP/enabled" ]]; then
    systemctl enable "$SERVICE" >/dev/null 2>&1 || true
  else
    systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  fi
  if [[ -f "$BACKUP/active" ]]; then
    systemctl restart "$SERVICE" >/dev/null 2>&1 || true
  fi
  rm -rf "$RELEASE" "$BACKUP"
  exit "$status"
}
trap cleanup EXIT

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ -r "$ARCHIVE" ]] || fail "repair_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "repair_archive_sha256_mismatch"
[[ -x /opt/bp/.venv/bin/python ]] || fail "production_python_missing"
id bp >/dev/null 2>&1 || fail "bp_user_missing"
[[ -L "$CURRENT" ]] || fail "listener_current_symlink_missing"
[[ -f "$SERVICE_PATH" ]] || fail "listener_unit_missing"
[[ -f "$ENV_PATH" ]] || fail "listener_env_missing"
[[ ! -e "$HANDOFF_ENV_PATH" && ! -L "$HANDOFF_ENV_PATH" ]] ||
  fail "handoff_env_must_remain_absent"

ENV_META=$(stat -c '%U:%G:%a' "$ENV_PATH")
[[ "$ENV_META" == "root:bp:640" ]] || fail "listener_env_metadata_invalid"
EXPECTED_KEYS=$'BP_TELEGRAM_BOT_TOKEN\nBP_TELEGRAM_CHAT_ID\nBP_TELEGRAM_USER_ID'
ACTUAL_KEYS=$(cut -d= -f1 "$ENV_PATH" | sort)
[[ "$ACTUAL_KEYS" == "$EXPECTED_KEYS" ]] || fail "listener_env_keys_invalid"
grep -q '^BP_TELEGRAM_BOT_TOKEN=.' "$ENV_PATH" || fail "telegram_bot_token_missing"
grep -Eq '^BP_TELEGRAM_USER_ID=[1-9][0-9]*$' "$ENV_PATH" ||
  fail "telegram_user_id_invalid"
grep -Eq '^BP_TELEGRAM_CHAT_ID=[1-9][0-9]*$' "$ENV_PATH" ||
  fail "telegram_chat_id_invalid"
! grep -q '^BP_TELEGRAM_HANDOFF_' "$ENV_PATH" ||
  fail "handoff_configuration_forbidden"

for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "core_service_not_active:$unit"
done
RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)

cp -a "$SERVICE_PATH" "$BACKUP/service"
readlink "$CURRENT" > "$BACKUP/current-target"
systemctl is-enabled --quiet "$SERVICE" 2>/dev/null && touch "$BACKUP/enabled" || true
systemctl is-active --quiet "$SERVICE" 2>/dev/null && touch "$BACKUP/active" || true

[[ ! -e "$RELEASE" && ! -L "$RELEASE" ]] || fail "repair_release_already_exists"
install -d -o root -g root -m 0755 "$RELEASES"
install -d -o root -g root -m 0755 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
chown -hR root:bp "$RELEASE"
find "$RELEASE" -type d -exec chmod 0750 {} +
find "$RELEASE" -type f -exec chmod 0640 {} +

for path in \
  scripts/run_phase15_v3_canary_telegram_approval.py \
  src/bp_engine/execution/telegram_approval.py \
  deploy/bp-phase15-canary-telegram-approval.service
do
  [[ -f "$RELEASE/$path" ]] || fail "repair_release_required_path_missing:$path"
done

runuser -u bp -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$RELEASE/src" \
  /opt/bp/.venv/bin/python -S -c \
  'import bp_engine.execution.telegram_approval' >/dev/null ||
  fail "repair_release_import_not_usable_by_service_user"

install -d -o bp -g bp -m 0700 "$STATE_ROOT"
ln -sfn "$RELEASE" "$CURRENT"
install -o root -g root -m 0644 "$RELEASE/deploy/$SERVICE" "$SERVICE_PATH"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 3

systemctl is-active --quiet "$SERVICE" || {
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail "listener_not_active_after_repair"
}
PID_FIRST=$(systemctl show -p MainPID --value "$SERVICE")
[[ "$PID_FIRST" =~ ^[1-9][0-9]*$ ]] || fail "listener_pid_invalid_after_repair"
sleep 7
systemctl is-active --quiet "$SERVICE" || {
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail "listener_not_stable_after_repair"
}
PID_SECOND=$(systemctl show -p MainPID --value "$SERVICE")
[[ "$PID_SECOND" == "$PID_FIRST" ]] || {
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail "listener_restarted_during_repair_stability_window"
}

[[ ! -e "$HANDOFF_ENV_PATH" && ! -L "$HANDOFF_ENV_PATH" ]] ||
  fail "handoff_env_created_during_repair"

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_restarted"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_restarted"
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]] || fail "paper_executor_restarted"

COMMITTED=true
trap - EXIT
rm -f "$ARCHIVE"
rm -rf "$BACKUP"

echo "DEPLOYED_HEAD=$HELPER_HEAD"
echo "RELEASE_IMPORT_USABLE_BY_SERVICE_USER=true"
echo "SERVICE_ACTIVE=true"
echo "SERVICE_ENABLED=true"
echo "SERVICE_PID_STABLE=true"
echo "EXISTING_TELEGRAM_ENV_REUSED=true"
echo "HANDOFF_CONFIGURED=false"
echo "LIVE_TRADING_ENABLED=false"
echo "CORE_SERVICE_PIDS_PRESERVED=true"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_RUNTIME_REPAIR=PASS"
REMOTE
)

REMOTE_B64=$(
  printf '%s' "$REMOTE_SCRIPT" |
    python3 -c 'import base64,sys; sys.stdout.write(base64.b64encode(sys.stdin.buffer.read()).decode("ascii"))'
)

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" \
  --zone="$US_ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env BP_PHASE15_HELPER_HEAD=$HEAD_Q BP_PHASE15_ARCHIVE=$ARCHIVE_Q BP_PHASE15_ARCHIVE_SHA256=$ARCHIVE_SHA_Q bash"

echo "BOT_TOKEN_STORED_IN_GIT=false"
echo "EXISTING_TELEGRAM_ENV_REUSED=true"
echo "HANDOFF_CONFIGURED=false"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_RUNTIME_REPAIR=PASS"
