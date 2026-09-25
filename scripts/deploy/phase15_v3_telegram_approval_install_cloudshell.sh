#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_APPROVAL_INSTALL:-}"

fail_local() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail_local "explicit_telegram_approval_install_authorization_required"
[[ -r /dev/tty && -w /dev/tty ]] || fail_local "interactive_terminal_required"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail_local "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail_local "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail_local "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail_local "gcloud_auth_missing"

if ! python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert gate["automated_real_money_submission"] is False
assert gate["second_order_authorized"] is False
PY
then
  fail_local "source_truth_not_safe_for_listener_install"
fi

printf '%s\n' "Open the BP Telegram bot in a private chat and send /start first." >/dev/tty
printf '%s' "Telegram bot token (hidden): " >/dev/tty
IFS= read -r -s BOT_TOKEN </dev/tty || fail_local "telegram_bot_token_input_closed"
printf '\n' >/dev/tty
[[ -n "$BOT_TOKEN" ]] || fail_local "telegram_bot_token_missing"
[[ "$BOT_TOKEN" != *$'\n'* && "$BOT_TOKEN" != *$'\r'* ]] ||
  fail_local "telegram_bot_token_invalid"

printf '%s' "Telegram numeric user ID: " >/dev/tty
IFS= read -r TELEGRAM_USER_ID </dev/tty || fail_local "telegram_user_id_input_closed"
printf '%s' "Telegram private-chat ID: " >/dev/tty
IFS= read -r TELEGRAM_CHAT_ID </dev/tty || fail_local "telegram_chat_id_input_closed"

[[ "$TELEGRAM_USER_ID" =~ ^[1-9][0-9]*$ ]] || fail_local "telegram_user_id_invalid"
[[ "$TELEGRAM_CHAT_ID" =~ ^[1-9][0-9]*$ ]] || fail_local "telegram_chat_id_invalid"
[[ "$TELEGRAM_USER_ID" == "$TELEGRAM_CHAT_ID" ]] ||
  fail_local "private_chat_id_must_match_user_id"

BOT_USERNAME=$(
  BP_INSTALL_TELEGRAM_TOKEN="$BOT_TOKEN"   python3 - "$TELEGRAM_CHAT_ID" <<'PY'
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

token = os.environ["BP_INSTALL_TELEGRAM_TOKEN"]
chat_id = int(sys.argv[1])


def call(method: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit("telegram_api_validation_failed") from exc
    if body.get("ok") is not True:
        raise SystemExit(f"telegram_{method}_validation_failed")
    result = body.get("result")
    if not isinstance(result, dict):
        raise SystemExit(f"telegram_{method}_result_invalid")
    return result


me = call("getMe", {})
chat = call("getChat", {"chat_id": chat_id})
if chat.get("type") != "private":
    raise SystemExit("telegram_chat_must_be_private")
if int(chat.get("id", 0)) != chat_id:
    raise SystemExit("telegram_chat_id_mismatch")
username = str(me.get("username") or "")
if not username:
    raise SystemExit("telegram_bot_username_missing")
print(username)
PY
) || fail_local "telegram_identity_validation_failed"

ARCHIVE=$(mktemp /tmp/bp-phase15-telegram-approval.XXXXXX.tar.gz)
ENV_LOCAL=$(mktemp /tmp/bp-phase15-telegram-approval-env.XXXXXX)
cleanup() {
  rm -f "$ARCHIVE" "$ENV_LOCAL"
  unset BOT_TOKEN
}
trap cleanup EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD"   scripts/run_phase15_v3_canary_telegram_approval.py   src/bp_engine/execution/telegram_approval.py   deploy/bp-phase15-canary-telegram-approval.service
ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')

cat > "$ENV_LOCAL" <<EOF
BP_TELEGRAM_BOT_TOKEN=$BOT_TOKEN
BP_TELEGRAM_USER_ID=$TELEGRAM_USER_ID
BP_TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
EOF
chmod 0600 "$ENV_LOCAL"

REMOTE_ARCHIVE="/tmp/bp-phase15-telegram-approval-$LOCAL_HEAD.tar.gz"
REMOTE_ENV="/tmp/bp-phase15-telegram-approval-env-$LOCAL_HEAD"
gcloud compute scp "$ARCHIVE" "$US_VM:$REMOTE_ARCHIVE"   --project="$PROJECT" --zone="$US_ZONE" --quiet
gcloud compute scp "$ENV_LOCAL" "$US_VM:$REMOTE_ENV"   --project="$PROJECT" --zone="$US_ZONE" --quiet

printf -v HEAD_Q '%q' "$LOCAL_HEAD"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA_Q '%q' "$ARCHIVE_SHA256"
printf -v ENV_Q '%q' "$REMOTE_ENV"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

HELPER_HEAD="${BP_PHASE15_HELPER_HEAD:?}"
ARCHIVE="${BP_PHASE15_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_PHASE15_ARCHIVE_SHA256:?}"
ENV_UPLOAD="${BP_PHASE15_ENV_UPLOAD:?}"

SIDECAR=/opt/bp-phase15-telegram-approval
RELEASES=$SIDECAR/releases
RELEASE=$RELEASES/$HELPER_HEAD
CURRENT=$SIDECAR/current
STATE_ROOT=/var/lib/bp/phase15-canary-telegram-approval
SERVICE=bp-phase15-canary-telegram-approval.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
ENV_PATH=/etc/bp/telegram-approval.env
HANDOFF_ENV_PATH=/etc/bp/telegram-approval-handoff.env
BACKUP=$(mktemp -d /var/tmp/bp-phase15-telegram-rollback.XXXXXX)
COMMITTED=false

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

cleanup() {
  rm -f "$ARCHIVE" "$ENV_UPLOAD"
  if [[ "$COMMITTED" == "true" ]]; then
    rm -rf "$BACKUP"
    return
  fi

  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  if [[ -f "$BACKUP/service" ]]; then
    install -o root -g root -m 0644 "$BACKUP/service" "$SERVICE_PATH"
  else
    rm -f "$SERVICE_PATH"
  fi
  if [[ -f "$BACKUP/env" ]]; then
    install -o root -g bp -m 0640 "$BACKUP/env" "$ENV_PATH"
  else
    rm -f "$ENV_PATH"
  fi
  if [[ -f "$BACKUP/current-target" ]]; then
    ln -sfn "$(cat "$BACKUP/current-target")" "$CURRENT"
  else
    rm -f "$CURRENT"
  fi
  systemctl daemon-reload
  if [[ -f "$BACKUP/enabled" ]]; then
    systemctl enable "$SERVICE" >/dev/null 2>&1 || true
  fi
  if [[ -f "$BACKUP/active" ]]; then
    systemctl start "$SERVICE" >/dev/null 2>&1 || true
  fi
  rm -rf "$BACKUP"
}
trap cleanup EXIT

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ -r "$ARCHIVE" ]] || fail "candidate_archive_missing"
[[ -r "$ENV_UPLOAD" ]] || fail "telegram_env_upload_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "candidate_archive_sha256_mismatch"
[[ -x /opt/bp/.venv/bin/python ]] || fail "production_python_missing"
id bp >/dev/null 2>&1 || fail "bp_user_missing"
[[ ! -e "$HANDOFF_ENV_PATH" && ! -L "$HANDOFF_ENV_PATH" ]] ||
  fail "handoff_env_must_not_exist_for_listener_install"

for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  systemctl is-active --quiet "$unit" || fail "core_service_not_active:$unit"
done
RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)

[[ -f "$SERVICE_PATH" ]] && cp -a "$SERVICE_PATH" "$BACKUP/service"
[[ -f "$ENV_PATH" ]] && cp -a "$ENV_PATH" "$BACKUP/env"
if [[ -L "$CURRENT" ]]; then
  readlink "$CURRENT" > "$BACKUP/current-target"
fi
systemctl is-enabled --quiet "$SERVICE" 2>/dev/null && touch "$BACKUP/enabled" || true
systemctl is-active --quiet "$SERVICE" 2>/dev/null && touch "$BACKUP/active" || true

install -d -o root -g root -m 0755 "$RELEASES"
if [[ ! -d "$RELEASE" ]]; then
  install -d -o root -g root -m 0755 "$RELEASE"
  tar -xzf "$ARCHIVE" -C "$RELEASE"
fi
for path in   scripts/run_phase15_v3_canary_telegram_approval.py   src/bp_engine/execution/telegram_approval.py   deploy/bp-phase15-canary-telegram-approval.service
do
  [[ -f "$RELEASE/$path" ]] || fail "release_required_path_missing:$path"
done

install -d -o bp -g bp -m 0700 "$STATE_ROOT"
install -o root -g bp -m 0640 "$ENV_UPLOAD" "$ENV_PATH"

EXPECTED_KEYS=$'BP_TELEGRAM_BOT_TOKEN\nBP_TELEGRAM_CHAT_ID\nBP_TELEGRAM_USER_ID'
ACTUAL_KEYS=$(cut -d= -f1 "$ENV_PATH" | sort)
[[ "$ACTUAL_KEYS" == "$EXPECTED_KEYS" ]] || fail "telegram_env_keys_invalid"
grep -q '^BP_TELEGRAM_BOT_TOKEN=.' "$ENV_PATH" || fail "telegram_bot_token_missing"
grep -Eq '^BP_TELEGRAM_USER_ID=[1-9][0-9]*$' "$ENV_PATH" || fail "telegram_user_id_invalid"
grep -Eq '^BP_TELEGRAM_CHAT_ID=[1-9][0-9]*$' "$ENV_PATH" || fail "telegram_chat_id_invalid"
! grep -q '^BP_TELEGRAM_HANDOFF_' "$ENV_PATH" || fail "handoff_configuration_forbidden"
[[ ! -e "$HANDOFF_ENV_PATH" && ! -L "$HANDOFF_ENV_PATH" ]] ||
  fail "handoff_env_created_or_present"

ln -sfn "$RELEASE" "$CURRENT"
install -o root -g root -m 0644 "$RELEASE/deploy/$SERVICE" "$SERVICE_PATH"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 2

systemctl is-enabled --quiet "$SERVICE" || fail "telegram_service_not_enabled"
systemctl is-active --quiet "$SERVICE" || {
  journalctl -u "$SERVICE" -n 50 --no-pager >&2 || true
  fail "telegram_service_not_active"
}

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_restarted"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_restarted"
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]] || fail "paper_executor_restarted"

UNIT_SHA=$(sha256sum "$SERVICE_PATH" | awk '{print $1}')
RELEASE_UNIT_SHA=$(sha256sum "$RELEASE/deploy/$SERVICE" | awk '{print $1}')
[[ "$UNIT_SHA" == "$RELEASE_UNIT_SHA" ]] || fail "installed_unit_hash_mismatch"

COMMITTED=true
trap - EXIT
rm -f "$ARCHIVE" "$ENV_UPLOAD"
rm -rf "$BACKUP"

echo "DEPLOYED_HEAD=$HELPER_HEAD"
echo "SERVICE_ACTIVE=true"
echo "SERVICE_ENABLED=true"
echo "HANDOFF_CONFIGURED=false"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
echo "CORE_SERVICE_PIDS_PRESERVED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_INSTALL=PASS"
REMOTE
)

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)
gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env BP_PHASE15_HELPER_HEAD=$HEAD_Q BP_PHASE15_ARCHIVE=$ARCHIVE_Q BP_PHASE15_ARCHIVE_SHA256=$ARCHIVE_SHA_Q BP_PHASE15_ENV_UPLOAD=$ENV_Q bash"

echo "BOT_USERNAME=@$BOT_USERNAME"
echo "BOT_TOKEN_STORED_IN_GIT=false"
echo "HANDOFF_CONFIGURED=false"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_INSTALL=PASS"
