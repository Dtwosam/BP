#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
RELEASE_ARCHIVE="${PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE:-}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_INSTALL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_transport_stage_install_authorization_required"
[[ -n "$RELEASE_ARCHIVE" ]] || fail "transport_release_archive_not_configured"
[[ -r "$RELEASE_ARCHIVE" ]] || fail "transport_release_archive_missing"

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

VERIFY_JSON=$(
  python3 "$ROOT/scripts/deploy/phase15_v3_telegram_transport_verify_release.py"     "$RELEASE_ARCHIVE"     --expected-commit-sha "$LOCAL_HEAD"
) || fail "transport_release_verification_failed"

ARCHIVE_SHA256=$(
  python3 - "$VERIFY_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["status"] == "verified"
print(payload["archive_sha256"])
PY
) || fail "transport_release_digest_read_failed"

PUBLISHER_PREFLIGHT=$(mktemp /tmp/bp-telegram-publisher-preflight.XXXXXX)
EXEC_PREFLIGHT=$(mktemp /tmp/bp-telegram-exec-preflight.XXXXXX)
STAGE_ID=$(
  python3 - <<'PY'
import secrets
print("phase15-telegram-stage-" + secrets.token_hex(12))
PY
)
[[ "$STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "stage_id_invalid"

REMOTE_ARCHIVE="/tmp/bp-phase15-telegram-transport-$LOCAL_HEAD.tar.gz"
RECORDER_STAGED=false
EXEC_STAGED=false
COMMITTED=false

rollback_recorder() {
  [[ "$RECORDER_STAGED" == "true" ]] || return 0
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command="sudo env BP_STAGE_ID='$STAGE_ID' bash -s" <<'REMOTE' || true
set -Eeuo pipefail
ROOT=/opt/bp-telegram-transport
META=$ROOT/STAGE-METADATA.json
OWNER=/var/lib/bp/phase15-canary-telegram-transport-stage-owner.json
STATE=/var/lib/bp/phase15-canary-telegram-transport
SERVICE=bp-phase15-telegram-pubsub-publisher.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
[[ -f "$OWNER" ]] || exit 0
python3 - "$OWNER" "$BP_STAGE_ID" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["stage_id"] == sys.argv[2]
assert payload["role"] == "publisher"
PY
systemctl stop "$SERVICE" >/dev/null 2>&1 || true
systemctl disable "$SERVICE" >/dev/null 2>&1 || true
rm -f "$SERVICE_PATH"
rm -rf "$STATE" "$ROOT"
rm -f "$OWNER"
systemctl daemon-reload
REMOTE
}

rollback_executor() {
  [[ "$EXEC_STAGED" == "true" ]] || return 0
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command="sudo env BP_STAGE_ID='$STAGE_ID' bash -s" <<'REMOTE' || true
set -Eeuo pipefail
ROOT=/opt/bp-telegram-transport
META=$ROOT/STAGE-METADATA.json
OWNER=/var/lib/bp-canary/telegram-transport-stage-owner.json
CONFIG=/etc/bp-telegram-transport
SERVICES=(
  bp-phase15-telegram-pubsub-streaming-receiver.service
  bp-phase15-telegram-transport-claim-worker.service
  bp-phase15-telegram-execution-authorization-worker.service
  bp-phase15-telegram-privileged-handoff.service
)
[[ -f "$OWNER" ]] || exit 0
readarray -t CREATED_FLAGS < <(
  python3 - "$OWNER" "$BP_STAGE_ID" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["stage_id"] == sys.argv[2]
assert payload["role"] == "executor"
print("true" if payload.get("created_bp_transport_user") is True else "false")
print("true" if payload.get("created_bp_transport_group") is True else "false")
PY
)
CREATED_USER="${CREATED_FLAGS[0]}"
CREATED_GROUP="${CREATED_FLAGS[1]}"
for service in "${SERVICES[@]}"; do
  systemctl stop "$service" >/dev/null 2>&1 || true
  systemctl disable "$service" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$service"
done
rm -rf   /var/lib/bp-telegram-transport   /var/lib/bp-canary/telegram-dispatch-claims   /var/lib/bp-canary/telegram-execution-authorized   /var/lib/bp-canary/telegram-execution-auth-processed   /var/lib/bp-canary/telegram-execution-auth-failures   /var/lib/bp-canary/telegram-live-handoff   "$CONFIG"   "$ROOT"
rm -f "$OWNER"
systemctl daemon-reload
if [[ "$CREATED_USER" == "true" ]]; then
  userdel bp-transport >/dev/null 2>&1 || true
fi
if [[ "$CREATED_GROUP" == "true" ]]; then
  groupdel bp-transport >/dev/null 2>&1 || true
fi
REMOTE
}

cleanup() {
  rm -f "$PUBLISHER_PREFLIGHT" "$EXEC_PREFLIGHT"
  if [[ "$COMMITTED" != "true" ]]; then
    rollback_executor
    rollback_recorder
  fi
  gcloud compute ssh "$US_VM"     --project="$PROJECT" --zone="$US_ZONE" --quiet     --command="rm -f '$REMOTE_ARCHIVE'" >/dev/null 2>&1 || true
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet     --command="rm -f '$REMOTE_ARCHIVE'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE="$RELEASE_ARCHIVE"   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_publisher_install_preflight_cloudshell.sh"   >"$PUBLISHER_PREFLIGHT" 2>&1 ||
  {
    cat "$PUBLISHER_PREFLIGHT" >&2
    fail "publisher_install_preflight_failed"
  }
grep -q '^PHASE15_V3_TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT=PASS$'   "$PUBLISHER_PREFLIGHT" ||
  fail "publisher_install_preflight_not_pass"

PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE="$RELEASE_ARCHIVE"   bash "$ROOT/scripts/deploy/phase15_v3_telegram_transport_install_preflight_cloudshell.sh"   >"$EXEC_PREFLIGHT" 2>&1 ||
  {
    cat "$EXEC_PREFLIGHT" >&2
    fail "executor_install_preflight_failed"
  }
grep -q '^PHASE15_V3_TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT=PASS$'   "$EXEC_PREFLIGHT" ||
  fail "executor_install_preflight_not_pass"

gcloud compute scp "$RELEASE_ARCHIVE" "$US_VM:$REMOTE_ARCHIVE"   --project="$PROJECT" --zone="$US_ZONE" --quiet ||
  fail "publisher_release_upload_failed"
gcloud compute scp "$RELEASE_ARCHIVE" "$EXEC_VM:$REMOTE_ARCHIVE"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet ||
  fail "executor_release_upload_failed"

RECORDER_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail
umask 077

HEAD="${BP_RELEASE_HEAD:?}"
ARCHIVE="${BP_RELEASE_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_RELEASE_ARCHIVE_SHA256:?}"
STAGE_ID="${BP_STAGE_ID:?}"

ROOT=/opt/bp-telegram-transport
RELEASES=$ROOT/releases
RELEASE=$RELEASES/$HEAD
CURRENT=$ROOT/current
VENV=$ROOT/.venv
BIN=$ROOT/bin
HANDOFF=$BIN/approved-outbox-handoff
META=$ROOT/STAGE-METADATA.json
OWNER=/var/lib/bp/phase15-canary-telegram-transport-stage-owner.json
STATE=/var/lib/bp/phase15-canary-telegram-transport
SERVICE=bp-phase15-telegram-pubsub-publisher.service
SERVICE_PATH=/etc/systemd/system/$SERVICE
ENV_PATH=/etc/bp/telegram-pubsub-publisher.env
KEY_PATH=/etc/bp-telegram-transport/transport.key
COMMITTED=false

fail() {
  echo "PUBLISHER_STAGE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

cleanup() {
  rm -f "$ARCHIVE"
  if [[ "$COMMITTED" == "true" ]]; then
    return
  fi
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  rm -f "$SERVICE_PATH"
  rm -rf "$STATE" "$ROOT"
  rm -f "$OWNER"
  systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup EXIT

[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "release_head_invalid"
[[ "$STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "stage_id_invalid"
[[ -r "$ARCHIVE" ]] || fail "release_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "release_archive_sha256_mismatch"
id bp >/dev/null 2>&1 || fail "bp_user_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
command -v runuser >/dev/null 2>&1 || fail "runuser_missing"
python3 -m venv --help >/dev/null 2>&1 || fail "python_venv_unavailable"

[[ ! -e "$ROOT" && ! -L "$ROOT" ]] || fail "transport_root_already_exists"
[[ ! -e "$STATE" && ! -L "$STATE" ]] || fail "transport_state_already_exists"
[[ ! -e "$OWNER" && ! -L "$OWNER" ]] || fail "stage_owner_already_exists"
[[ ! -e "$SERVICE_PATH" && ! -L "$SERVICE_PATH" ]] ||
  fail "publisher_unit_file_already_exists"
[[ ! -e "$ENV_PATH" && ! -L "$ENV_PATH" ]] ||
  fail "publisher_env_must_not_exist_before_stage"
[[ ! -e "$KEY_PATH" && ! -L "$KEY_PATH" ]] ||
  fail "transport_key_must_not_exist_before_stage"

for unit in   bp-postgres.service   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service
do
  systemctl is-active --quiet "$unit" || fail "core_service_not_active:$unit"
done
RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)

python3 - "$OWNER" "$STAGE_ID" "$HEAD" "$ARCHIVE_SHA256" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "stage_id": sys.argv[2],
    "role": "publisher",
    "release_head": sys.argv[3],
    "archive_sha256": sys.argv[4],
    "created_bp_transport_user": False,
    "created_bp_transport_group": False,
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

install -d -o root -g root -m 0755 "$RELEASES"
install -d -o root -g root -m 0755 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
chown -hR root:bp "$RELEASE"
find "$RELEASE" -type d -exec chmod 0750 {} +
find "$RELEASE" -type f -exec chmod 0640 {} +

for required in   RELEASE-MANIFEST.json   deploy/bp-phase15-telegram-pubsub-publisher.service   deploy/phase15-telegram-approved-outbox-handoff.sh   deploy/phase15-telegram-transport-runtime-requirements.txt   scripts/run_phase15_v3_telegram_pubsub_publish_worker.py
do
  [[ -f "$RELEASE/$required" ]] || fail "release_required_path_missing:$required"
done

python3 -m venv "$VENV"
PIP_DISABLE_PIP_VERSION_CHECK=1   "$VENV/bin/pip" install --no-input --only-binary=:all:   -r "$RELEASE/deploy/phase15-telegram-transport-runtime-requirements.txt"
"$VENV/bin/pip" check
"$VENV/bin/python" - <<'PY'
from importlib.metadata import version

assert version("httpx") == "0.28.1"
assert version("google-cloud-pubsub") == "2.41.0"
PY

chown -hR root:bp "$VENV"
chmod -R g+rX,o-rwx "$VENV"
runuser -u bp -- "$VENV/bin/python" -c 'import httpx; import google.cloud.pubsub_v1' >/dev/null ||
  fail "publisher_venv_not_usable_by_service_user"
runuser -u bp -- test -r "$RELEASE/scripts/run_phase15_v3_telegram_pubsub_publish_worker.py" ||
  fail "publisher_release_not_readable_by_service_user"

install -d -o root -g root -m 0755 "$BIN"
install -o root -g bp -m 0750   "$RELEASE/deploy/phase15-telegram-approved-outbox-handoff.sh"   "$HANDOFF"
ln -s "$RELEASE" "$CURRENT"
install -d -o bp -g bp -m 0700 "$STATE"
install -o root -g root -m 0644   "$RELEASE/deploy/$SERVICE"   "$SERVICE_PATH"
systemctl daemon-reload

systemctl is-active --quiet "$SERVICE" &&
  fail "publisher_service_unexpectedly_active" || true
systemctl is-enabled --quiet "$SERVICE" 2>/dev/null &&
  fail "publisher_service_unexpectedly_enabled" || true
[[ ! -e "$ENV_PATH" && ! -L "$ENV_PATH" ]] ||
  fail "publisher_env_created_during_stage"
[[ ! -e "$KEY_PATH" && ! -L "$KEY_PATH" ]] ||
  fail "transport_key_created_during_stage"

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_restarted"
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]] || fail "predictor_restarted"
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]] || fail "paper_executor_restarted"

python3 - "$META" "$STAGE_ID" "$HEAD" "$ARCHIVE_SHA256" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "stage_id": sys.argv[2],
    "role": "publisher",
    "release_head": sys.argv[3],
    "archive_sha256": sys.argv[4],
    "stage_complete": True,
    "services_started": False,
    "services_enabled": False,
    "environment_files_created": False,
    "key_files_created": False,
    "iam_changed": False,
    "pubsub_resources_changed": False,
    "real_order_submitted": False,
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

COMMITTED=true
trap - EXIT
rm -f "$ARCHIVE"

echo "PUBLISHER_STAGE=PASS"
echo "SERVICES_STARTED=false"
echo "SERVICES_ENABLED=false"
echo "ENVIRONMENT_FILES_CREATED=false"
echo "KEY_FILES_CREATED=false"
echo "APPROVED_OUTBOX_HANDOFF_STAGED=true"
echo "CORE_SERVICE_PIDS_PRESERVED=true"
REMOTE
)

EXECUTOR_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail
umask 077

HEAD="${BP_RELEASE_HEAD:?}"
ARCHIVE="${BP_RELEASE_ARCHIVE:?}"
ARCHIVE_SHA256="${BP_RELEASE_ARCHIVE_SHA256:?}"
STAGE_ID="${BP_STAGE_ID:?}"

ROOT=/opt/bp-telegram-transport
RELEASES=$ROOT/releases
RELEASE=$RELEASES/$HEAD
CURRENT=$ROOT/current
VENV=$ROOT/.venv
META=$ROOT/STAGE-METADATA.json
OWNER=/var/lib/bp-canary/telegram-transport-stage-owner.json
CONFIG=/etc/bp-telegram-transport
SERVICES=(
  bp-phase15-telegram-pubsub-streaming-receiver.service
  bp-phase15-telegram-transport-claim-worker.service
  bp-phase15-telegram-execution-authorization-worker.service
  bp-phase15-telegram-privileged-handoff.service
)
TRANSPORT_STATE_ROOT=/var/lib/bp-telegram-transport
TRANSPORT_PRIVATE_STATE_DIRS=(
  "$TRANSPORT_STATE_ROOT/inbox"
  "$TRANSPORT_STATE_ROOT/rejections"
  "$TRANSPORT_STATE_ROOT/claims"
  "$TRANSPORT_STATE_ROOT/processed"
  "$TRANSPORT_STATE_ROOT/failures"
)
TRANSPORT_READY_DIR="$TRANSPORT_STATE_ROOT/ready"
LEGACY_TRANSPORT_STATE_DIRS=(
  /var/lib/bp-canary/telegram-transport-inbox
  /var/lib/bp-canary/telegram-transport-rejections
  /var/lib/bp-canary/telegram-transport-claims
  /var/lib/bp-canary/telegram-transport-ready
  /var/lib/bp-canary/telegram-transport-claim-processed
  /var/lib/bp-canary/telegram-transport-claim-failures
)
AUTH_STATE_DIRS=(
  /var/lib/bp-canary/telegram-dispatch-claims
  /var/lib/bp-canary/telegram-execution-authorized
  /var/lib/bp-canary/telegram-execution-auth-processed
  /var/lib/bp-canary/telegram-execution-auth-failures
  /var/lib/bp-canary/telegram-live-handoff
)
STATE_DIRS=(
  "$TRANSPORT_STATE_ROOT"
  "${AUTH_STATE_DIRS[@]}"
)
PREEXISTING_STATE_PATHS=(
  "$TRANSPORT_STATE_ROOT"
  "$TRANSPORT_READY_DIR"
  "${TRANSPORT_PRIVATE_STATE_DIRS[@]}"
  "${LEGACY_TRANSPORT_STATE_DIRS[@]}"
  "${AUTH_STATE_DIRS[@]}"
)
CREATED_USER=false
CREATED_GROUP=false
COMMITTED=false

fail() {
  echo "EXECUTOR_STAGE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

cleanup() {
  rm -f "$ARCHIVE"
  if [[ "$COMMITTED" == "true" ]]; then
    return
  fi
  for service in "${SERVICES[@]}"; do
    systemctl stop "$service" >/dev/null 2>&1 || true
    systemctl disable "$service" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$service"
  done
  rm -rf "$TRANSPORT_STATE_ROOT"
  for dir in "${AUTH_STATE_DIRS[@]}"; do
    rm -rf "$dir"
  done
  rm -rf "$CONFIG" "$ROOT"
  rm -f "$OWNER"
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ "$CREATED_USER" == "true" ]]; then
    userdel bp-transport >/dev/null 2>&1 || true
  fi
  if [[ "$CREATED_GROUP" == "true" ]]; then
    groupdel bp-transport >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "release_head_invalid"
[[ "$STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "stage_id_invalid"
[[ -r "$ARCHIVE" ]] || fail "release_archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "release_archive_sha256_mismatch"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
command -v runuser >/dev/null 2>&1 || fail "runuser_missing"
python3 -m venv --help >/dev/null 2>&1 || fail "python_venv_unavailable"

[[ ! -e "$ROOT" && ! -L "$ROOT" ]] || fail "transport_root_already_exists"
[[ ! -e "$CONFIG" && ! -L "$CONFIG" ]] || fail "transport_config_already_exists"
[[ ! -e "$OWNER" && ! -L "$OWNER" ]] || fail "stage_owner_already_exists"
for service in "${SERVICES[@]}"; do
  [[ ! -e "/etc/systemd/system/$service" ]] ||
    fail "transport_unit_file_already_exists:$service"
  systemctl is-active --quiet "$service" &&
    fail "transport_service_unexpectedly_active:$service" || true
  systemctl is-enabled --quiet "$service" 2>/dev/null &&
    fail "transport_service_unexpectedly_enabled:$service" || true
done
for dir in "${PREEXISTING_STATE_PATHS[@]}"; do
  [[ ! -e "$dir" && ! -L "$dir" ]] || fail "transport_state_already_exists:$dir"
done

if id bp-transport >/dev/null 2>&1; then
  [[ "$(id -gn bp-transport)" == "bp-transport" ]] ||
    fail "bp_transport_primary_group_invalid"
else
  CREATED_USER=true
  if ! getent group bp-transport >/dev/null 2>&1; then
    CREATED_GROUP=true
  fi
fi

health() {
  printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh
}

HEALTH_BEFORE=$(health) || fail "executor_health_before_failed"
python3 - "$HEALTH_BEFORE" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["kill_switch_engaged"] is True
assert payload["activation_valid"] is False
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
geoblock = payload.get("geoblock") or {}
assert geoblock.get("blocked") is False
assert geoblock.get("country") == "ZA"
PY

python3 -   "$OWNER"   "$STAGE_ID"   "$HEAD"   "$ARCHIVE_SHA256"   "$CREATED_USER"   "$CREATED_GROUP" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "stage_id": sys.argv[2],
    "role": "executor",
    "release_head": sys.argv[3],
    "archive_sha256": sys.argv[4],
    "created_bp_transport_user": sys.argv[5] == "true",
    "created_bp_transport_group": sys.argv[6] == "true",
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

if [[ "$CREATED_GROUP" == "true" ]]; then
  groupadd --system bp-transport
fi
if [[ "$CREATED_USER" == "true" ]]; then
  useradd     --system     --gid bp-transport     --home-dir /nonexistent     --shell /usr/sbin/nologin     --no-create-home     bp-transport
fi

install -d -o root -g root -m 0755 "$RELEASES"
install -d -o root -g root -m 0755 "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
chown -hR root:bp-transport "$RELEASE"
find "$RELEASE" -type d -exec chmod 0750 {} +
find "$RELEASE" -type f -exec chmod 0640 {} +

for required in   RELEASE-MANIFEST.json   deploy/bp-phase15-telegram-pubsub-streaming-receiver.service   deploy/bp-phase15-telegram-transport-claim-worker.service   deploy/bp-phase15-telegram-execution-authorization-worker.service   deploy/bp-phase15-telegram-privileged-handoff.service   deploy/phase15-telegram-transport-runtime-requirements.txt   scripts/run_phase15_v3_telegram_pubsub_streaming_receive.py   scripts/run_phase15_v3_telegram_transport_claim_worker.py   scripts/run_phase15_v3_telegram_execution_ready_verify.py   scripts/run_phase15_v3_telegram_execution_authorization_worker.py   scripts/run_phase15_v3_telegram_execution_package_verify.py   scripts/run_phase15_v3_telegram_privileged_handoff_verify.py   scripts/run_phase15_v3_telegram_privileged_handoff_worker.py   src/bp_engine/execution/telegram_execution_package.py   src/bp_engine/execution/telegram_privileged_handoff.py   src/bp_engine/execution/telegram_privileged_consumer.py
do
  [[ -f "$RELEASE/$required" ]] || fail "release_required_path_missing:$required"
done

python3 -m venv "$VENV"
PIP_DISABLE_PIP_VERSION_CHECK=1   "$VENV/bin/pip" install --no-input --only-binary=:all:   -r "$RELEASE/deploy/phase15-telegram-transport-runtime-requirements.txt"
"$VENV/bin/pip" check
"$VENV/bin/python" - <<'PY'
from importlib.metadata import version

assert version("httpx") == "0.28.1"
assert version("google-cloud-pubsub") == "2.41.0"
PY

chown -hR root:bp-transport "$VENV"
chmod -R g+rX,o-rwx "$VENV"
runuser -u bp-transport -- "$VENV/bin/python" -c 'import httpx; import google.cloud.pubsub_v1' >/dev/null ||
  fail "executor_venv_not_usable_by_service_user"
runuser -u bp-transport -- test -r "$RELEASE/scripts/run_phase15_v3_telegram_pubsub_streaming_receive.py" ||
  fail "executor_release_not_readable_by_service_user"
runuser -u bp-transport -- test -r "$RELEASE/scripts/run_phase15_v3_telegram_transport_claim_worker.py" ||
  fail "executor_claim_worker_not_readable_by_service_user"

ln -s "$RELEASE" "$CURRENT"
install -d -o root -g bp-transport -m 0750 "$CONFIG"
install -d -o root -g bp-transport -m 0710 "$TRANSPORT_STATE_ROOT"
for dir in "${TRANSPORT_PRIVATE_STATE_DIRS[@]}"; do
  install -d -o bp-transport -g bp-transport -m 0700 "$dir"
done
install -d -o bp-transport -g bp-transport -m 0750 "$TRANSPORT_READY_DIR"
for dir in "${AUTH_STATE_DIRS[@]}"; do
  install -d -o root -g root -m 0700 "$dir"
done
for service in "${SERVICES[@]}"; do
  install -o root -g root -m 0644     "$RELEASE/deploy/$service"     "/etc/systemd/system/$service"
  cmp -s "$RELEASE/deploy/$service" "/etc/systemd/system/$service" ||
    fail "transport_unit_install_hash_mismatch:$service"
done
systemctl daemon-reload

for service in "${SERVICES[@]}"; do
  systemctl is-active --quiet "$service" &&
    fail "transport_service_started_during_stage:$service" || true
  systemctl is-enabled --quiet "$service" 2>/dev/null &&
    fail "transport_service_enabled_during_stage:$service" || true
done
for secret in   "$CONFIG/receiver.env"   "$CONFIG/claim.env"   "$CONFIG/execution-auth.env"   "$CONFIG/privileged-handoff.env"   "$CONFIG/transport.key"   "$CONFIG/origin.key"
do
  [[ ! -e "$secret" && ! -L "$secret" ]] ||
    fail "secret_or_env_created_during_stage:$secret"
done

HEALTH_AFTER=$(health) || fail "executor_health_after_failed"
python3 - "$HEALTH_BEFORE" "$HEALTH_AFTER" <<'PY'
import json
import sys

before = json.loads(sys.argv[1])
after = json.loads(sys.argv[2])
for payload in (before, after):
    assert payload["status"] == "ok"
    assert payload["kill_switch_engaged"] is True
    assert payload["activation_valid"] is False
    assert payload["submission_ready"] is False
    assert payload["live_order_submitted"] is False
    geoblock = payload.get("geoblock") or {}
    assert geoblock.get("blocked") is False
    assert geoblock.get("country") == "ZA"
PY

python3 -   "$META"   "$STAGE_ID"   "$HEAD"   "$ARCHIVE_SHA256"   "$CREATED_USER"   "$CREATED_GROUP" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "stage_id": sys.argv[2],
    "role": "executor",
    "release_head": sys.argv[3],
    "archive_sha256": sys.argv[4],
    "created_bp_transport_user": sys.argv[5] == "true",
    "created_bp_transport_group": sys.argv[6] == "true",
    "stage_complete": True,
    "services_started": False,
    "services_enabled": False,
    "environment_files_created": False,
    "key_files_created": False,
    "iam_changed": False,
    "pubsub_resources_changed": False,
    "real_order_submitted": False,
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

COMMITTED=true
trap - EXIT
rm -f "$ARCHIVE"

echo "EXECUTOR_STAGE=PASS"
echo "SERVICES_STARTED=false"
echo "SERVICES_ENABLED=false"
echo "ENVIRONMENT_FILES_CREATED=false"
echo "KEY_FILES_CREATED=false"
echo "EXECUTOR_SAFE_IDLE_PRESERVED=true"
echo "EXECUTION_AUTHORIZATION_WORKER_STAGED=true"
echo "PRIVILEGED_HANDOFF_WORKER_STAGED=true"
REMOTE
)

printf -v HEAD_Q '%q' "$LOCAL_HEAD"
printf -v ARCHIVE_Q '%q' "$REMOTE_ARCHIVE"
printf -v ARCHIVE_SHA_Q '%q' "$ARCHIVE_SHA256"
printf -v STAGE_ID_Q '%q' "$STAGE_ID"

RECORDER_B64=$(printf '%s' "$RECORDER_SCRIPT" | base64 -w0)
RECORDER_STAGED=true
gcloud compute ssh "$US_VM"   --project="$PROJECT"   --zone="$US_ZONE"   --quiet   --command="printf '%s' '$RECORDER_B64' | base64 -d | sudo env BP_RELEASE_HEAD=$HEAD_Q BP_RELEASE_ARCHIVE=$ARCHIVE_Q BP_RELEASE_ARCHIVE_SHA256=$ARCHIVE_SHA_Q BP_STAGE_ID=$STAGE_ID_Q bash" ||
  fail "publisher_stage_failed"

EXECUTOR_B64=$(printf '%s' "$EXECUTOR_SCRIPT" | base64 -w0)
EXEC_STAGED=true
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT"   --zone="$EXEC_ZONE"   --quiet   --command="printf '%s' '$EXECUTOR_B64' | base64 -d | sudo env BP_RELEASE_HEAD=$HEAD_Q BP_RELEASE_ARCHIVE=$ARCHIVE_Q BP_RELEASE_ARCHIVE_SHA256=$ARCHIVE_SHA_Q BP_STAGE_ID=$STAGE_ID_Q bash" ||
  fail "executor_stage_failed"

REMOTE_MAIN_AFTER=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$REMOTE_MAIN_AFTER" == "$LOCAL_HEAD" ]] ||
  fail "remote_main_changed_during_stage"

COMMITTED=true
trap - EXIT
rm -f "$PUBLISHER_PREFLIGHT" "$EXEC_PREFLIGHT"
gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="rm -f '$REMOTE_ARCHIVE'" >/dev/null 2>&1 || true
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="rm -f '$REMOTE_ARCHIVE'" >/dev/null 2>&1 || true

echo "STAGE_ID=$STAGE_ID"
echo "RELEASE_HEAD=$LOCAL_HEAD"
echo "RELEASE_SHA256=$ARCHIVE_SHA256"
echo "PUBLISHER_STAGED=true"
echo "EXECUTOR_STAGED=true"
echo "SERVICES_STARTED=false"
echo "SERVICES_ENABLED=false"
echo "ENVIRONMENT_FILES_CREATED=false"
echo "KEY_FILES_CREATED=false"
echo "IAM_CHANGED=false"
echo "PUBSUB_RESOURCES_CHANGED=false"
echo "LIVE_TRADING_ENABLED=false"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_INSTALL=PASS"
