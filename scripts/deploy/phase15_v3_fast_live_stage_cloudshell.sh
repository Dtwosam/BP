#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_STAGE=FAIL:%s\n' "$1" >&2
  exit 1
}

: "${BP_FAST_LIVE_RELEASE_ARCHIVE:?BP_FAST_LIVE_RELEASE_ARCHIVE is required}"
: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"

ARCHIVE="$BP_FAST_LIVE_RELEASE_ARCHIVE"
[[ -f "$ARCHIVE" && ! -L "$ARCHIVE" ]] || fail "release_archive_invalid"

read -r HEAD ARCHIVE_SHA < <(
  python3 - "$ARCHIVE" <<'PY'
import hashlib
import json
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
with tarfile.open(archive, "r:gz") as tf:
    names = tf.getnames()
    if "RELEASE-MANIFEST.json" not in names:
        raise SystemExit("manifest_missing")
    payload = json.load(tf.extractfile("RELEASE-MANIFEST.json"))
    if payload.get("purpose") != "phase15-v3-fast-live-release-v1":
        raise SystemExit("purpose_mismatch")
    if payload.get("contains_project_state") is not False:
        raise SystemExit("project_state_present")
    if payload.get("contains_authorization") is not False:
        raise SystemExit("authorization_present")
    if payload.get("contains_secret_files") is not False:
        raise SystemExit("secret_present")
    if payload.get("production_mutation_performed") is not False:
        raise SystemExit("manifest_mutation_flag_invalid")
    if payload.get("real_order_submitted") is not False:
        raise SystemExit("manifest_order_flag_invalid")
    head = str(payload.get("commit_sha") or "")
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise SystemExit("commit_invalid")
    forbidden = (
        "PROJECT_STATE.json",
        "authorization.json",
        "receiver.env",
        "phase15-fast-live-source.env",
    )
    if any(name.endswith(forbidden) for name in names):
        raise SystemExit("runtime_authorization_material_in_release")
print(head, hashlib.sha256(archive.read_bytes()).hexdigest())
PY
) || fail "release_manifest_validation_failed"

[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "release_head_invalid"
[[ "$ARCHIVE_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "release_sha_invalid"

REMOTE_ARCHIVE="/tmp/bp-fast-live-${HEAD}.tar.gz"

gcloud compute scp "$ARCHIVE"   "${BP_FAST_LIVE_RECORDER_VM}:${REMOTE_ARCHIVE}"   --project="$BP_FAST_LIVE_GCP_PROJECT"   --zone="$BP_FAST_LIVE_RECORDER_ZONE"   --quiet >/dev/null || fail "recorder_upload_failed"

gcloud compute scp "$ARCHIVE"   "${BP_FAST_LIVE_EXEC_VM}:${REMOTE_ARCHIVE}"   --project="$BP_FAST_LIVE_GCP_PROJECT"   --zone="$BP_FAST_LIVE_EXEC_ZONE"   --quiet >/dev/null || fail "executor_upload_failed"

RECORDER_OUTPUT=$(
  gcloud compute ssh "$BP_FAST_LIVE_RECORDER_VM"     --project="$BP_FAST_LIVE_GCP_PROJECT"     --zone="$BP_FAST_LIVE_RECORDER_ZONE"     --quiet     --command="sudo bash -s -- '$REMOTE_ARCHIVE' '$HEAD' '$ARCHIVE_SHA'" <<'REMOTE'
set -euo pipefail
archive="$1"
head="$2"
expected_sha="$3"
root=/opt/bp-fast-live
release="$root/releases/$head"
current="$root/current"
unit=bp-phase15-fast-live-source.service

actual_sha=$(python3 - "$archive" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
[[ "$actual_sha" == "$expected_sha" ]]

systemctl is-active --quiet "$unit" && exit 31 || true
systemctl is-enabled --quiet "$unit" && exit 32 || true

install -d -o root -g bp -m 0750 "$root" "$root/releases"
if [[ -e "$release" ]]; then
  exit 33
fi
install -d -o root -g bp -m 0750 "$release"
tar -xzf "$archive" -C "$release"
rm -f "$archive"
chown -hR root:bp "$release"
find "$release" -type d -exec chmod 0750 {} +
find "$release" -type f -exec chmod 0640 {} +

runuser -u bp -- test -r "$release/scripts/run_phase15_v3_fast_live_source.py"
runuser -u bp -- test -r "$release/src/bp_engine/execution/fast_live_prepare.py"
runuser -u bp -- test -r "$release/scripts/run_phase15_v3_canary_telegram_approval.py"
runuser -u bp -- test -r "$release/src/bp_engine/execution/telegram_approval.py"

telegram_root=/opt/bp-phase15-telegram-approval
telegram_release="$telegram_root/releases/$head"
install -d -o root -g root -m 0755 "$telegram_root" "$telegram_root/releases"
if [[ -e "$telegram_release" ]]; then
  [[ -d "$telegram_release" && ! -L "$telegram_release" ]] || exit 36
  for relative in \
    scripts/run_phase15_v3_canary_telegram_approval.py \
    src/bp_engine/execution/telegram_approval.py \
    deploy/bp-phase15-canary-telegram-approval.service
  do
    cmp -s "$release/$relative" "$telegram_release/$relative" || exit 37
  done
else
  install -d -o root -g bp -m 0750 \
    "$telegram_release/scripts" \
    "$telegram_release/src/bp_engine/execution" \
    "$telegram_release/deploy"
  install -o root -g bp -m 0640 \
    "$release/scripts/run_phase15_v3_canary_telegram_approval.py" \
    "$telegram_release/scripts/run_phase15_v3_canary_telegram_approval.py"
  install -o root -g bp -m 0640 \
    "$release/src/bp_engine/execution/telegram_approval.py" \
    "$telegram_release/src/bp_engine/execution/telegram_approval.py"
  install -o root -g bp -m 0640 \
    "$release/deploy/bp-phase15-canary-telegram-approval.service" \
    "$telegram_release/deploy/bp-phase15-canary-telegram-approval.service"
fi
runuser -u bp -- env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$telegram_release/src" \
  /opt/bp/.venv/bin/python -S -c \
  'import bp_engine.execution.telegram_approval' >/dev/null

venv="$root/.venv"
bootstrap_venv="$root/.uv-bootstrap"
managed_python_dir="$root/python"
uv_version="0.12.19"
python_version="3.12.14"

if [[ ! -x "$bootstrap_venv/bin/uv" ]] || \
   [[ "$("$bootstrap_venv/bin/uv" --version 2>/dev/null || true)" != "uv $uv_version" ]]; then
  rm -rf "$bootstrap_venv"
  python3 -m venv "$bootstrap_venv"
  "$bootstrap_venv/bin/pip" install \
    --disable-pip-version-check \
    --no-input \
    "uv==$uv_version"
fi

if [[ -x "$venv/bin/python" ]]; then
  if ! "$venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 12, 14) else 1)'; then
    rm -rf "$venv"
  fi
fi

if [[ ! -x "$venv/bin/python" ]]; then
  UV_PYTHON_INSTALL_DIR="$managed_python_dir" \
  UV_MANAGED_PYTHON=1 \
  "$bootstrap_venv/bin/uv" venv \
    --python "$python_version" \
    --seed \
    "$venv"
fi

"$venv/bin/python" -c 'import sys; assert sys.version_info[:3] == (3, 12, 14)'
"$venv/bin/pip" install --disable-pip-version-check --no-input \
  -r "$release/deploy/phase15-fast-live-executor-requirements.txt"
"$venv/bin/pip" install --disable-pip-version-check --no-input "$release"
"$venv/bin/pip" check

runuser -u bp -- env PYTHONPATH="$release/src" "$venv/bin/python" - <<'PY'
import sys
from importlib.metadata import version

assert sys.version_info[:3] == (3, 12, 14)
assert version("google-cloud-pubsub") == "2.41.0"
import bp_engine.execution.fast_live  # noqa: F401
import bp_engine.execution.fast_live_prepare  # noqa: F401
PY

ln -s "$release" "$root/.current-$head"
mv -Tf "$root/.current-$head" "$current"

install -o root -g root -m 0644   "$release/deploy/bp-phase15-fast-live-source.service"   /etc/systemd/system/bp-phase15-fast-live-source.service
systemctl daemon-reload

install -d -o root -g bp -m 0750 /etc/bp-fast-live
install -d -o bp -g bp -m 0700 /var/lib/bp/phase15-fast-live

[[ ! -e /etc/bp-fast-live/authorization.json ]]
[[ ! -e /etc/bp-fast-live/PROJECT_STATE.json ]]
[[ ! -e /etc/bp-fast-live/transport.key ]]
[[ ! -e /etc/bp/phase15-fast-live-source.env ]]
systemctl is-active --quiet "$unit" && exit 34 || true
systemctl is-enabled --quiet "$unit" && exit 35 || true

printf 'RECORDER_STAGE=PASS\n'
printf 'RECORDER_FAST_LIVE_ACTIVE=false\n'
printf 'RECORDER_FAST_LIVE_ENABLED=false\n'
printf 'RECORDER_AUTHORIZATION_PRESENT=false\n'
printf 'RECORDER_TELEGRAM_APPROVAL_RELEASE_STAGED=true\n'
printf 'RECORDER_TELEGRAM_APPROVAL_RESTARTED=false\n'
printf 'RECORDER_FAST_LIVE_PYTHON=3.12.14\n'
REMOTE
) || {
  printf '%s\n' "$RECORDER_OUTPUT" >&2
  fail "recorder_stage_failed"
}

EXECUTOR_OUTPUT=$(
  gcloud compute ssh "$BP_FAST_LIVE_EXEC_VM"     --project="$BP_FAST_LIVE_GCP_PROJECT"     --zone="$BP_FAST_LIVE_EXEC_ZONE"     --quiet     --command="sudo bash -s -- '$REMOTE_ARCHIVE' '$HEAD' '$ARCHIVE_SHA'" <<'REMOTE'
set -euo pipefail
archive="$1"
head="$2"
expected_sha="$3"
root=/opt/bp-fast-live
release="$root/releases/$head"
current="$root/current"
venv="$root/.venv"
unit=bp-phase15-fast-live-receiver.service
state=/var/lib/bp-canary/fast-live
kill="$state/KILL"

actual_sha=$(python3 - "$archive" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
[[ "$actual_sha" == "$expected_sha" ]]

systemctl is-active --quiet "$unit" && exit 41 || true
systemctl is-enabled --quiet "$unit" && exit 42 || true

install -d -o root -g root -m 0755 "$root" "$root/releases"
if [[ -e "$release" ]]; then
  exit 43
fi
install -d -o root -g root -m 0755 "$release"
tar -xzf "$archive" -C "$release"
rm -f "$archive"
chown -hR root:root "$release"
find "$release" -type d -exec chmod 0755 {} +
find "$release" -type f -exec chmod 0644 {} +

bootstrap_venv="$root/.uv-bootstrap"
managed_python_dir="$root/python"
uv_version="0.12.19"
python_version="3.12.14"

if [[ ! -x "$bootstrap_venv/bin/uv" ]] || \
   [[ "$("$bootstrap_venv/bin/uv" --version 2>/dev/null || true)" != "uv $uv_version" ]]; then
  rm -rf "$bootstrap_venv"
  python3 -m venv "$bootstrap_venv"
  "$bootstrap_venv/bin/pip" install \
    --disable-pip-version-check \
    --no-input \
    "uv==$uv_version"
fi

if [[ -x "$venv/bin/python" ]]; then
  if ! "$venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 12, 14) else 1)'; then
    rm -rf "$venv"
  fi
fi

if [[ ! -x "$venv/bin/python" ]]; then
  UV_PYTHON_INSTALL_DIR="$managed_python_dir" \
  UV_MANAGED_PYTHON=1 \
  "$bootstrap_venv/bin/uv" venv \
    --python "$python_version" \
    --seed \
    "$venv"
fi

"$venv/bin/python" -c 'import sys; assert sys.version_info[:3] == (3, 12, 14)'
"$venv/bin/pip" install --disable-pip-version-check --no-input   -r "$release/deploy/phase15-fast-live-executor-requirements.txt"
"$venv/bin/pip" install --disable-pip-version-check --no-input "$release"
"$venv/bin/pip" check

"$venv/bin/python" - <<'PY'
from importlib.metadata import version
assert version("polymarket-client") == "0.7.1"
assert version("google-cloud-pubsub") == "2.41.0"
assert version("websockets") == "15.0.1"
import bp_engine.execution.fast_live
import bp_engine.execution.fast_live_book
import bp_engine.execution.fast_live_executor
PY

ln -s "$release" "$root/.current-$head"
mv -Tf "$root/.current-$head" "$current"

install -o root -g root -m 0644   "$release/deploy/bp-phase15-fast-live-receiver.service"   /etc/systemd/system/bp-phase15-fast-live-receiver.service
systemctl daemon-reload

install -d -o root -g root -m 0750 /etc/bp-fast-live
install -d -o root -g root -m 0700 "$state"
printf '%s\n' fast-live-stage-safe-stop > "$kill"
chmod 0600 "$kill"

[[ ! -e /etc/bp-fast-live/authorization.json ]]
[[ ! -e /etc/bp-fast-live/PROJECT_STATE.json ]]
[[ ! -e /etc/bp-fast-live/transport.key ]]
[[ ! -e /etc/bp-fast-live/receiver.env ]]
systemctl is-active --quiet "$unit" && exit 44 || true
systemctl is-enabled --quiet "$unit" && exit 45 || true
[[ -f "$kill" ]]

printf 'EXECUTOR_STAGE=PASS\n'
printf 'EXECUTOR_FAST_LIVE_ACTIVE=false\n'
printf 'EXECUTOR_FAST_LIVE_ENABLED=false\n'
printf 'EXECUTOR_KILL_SWITCH_ENGAGED=true\n'
printf 'EXECUTOR_AUTHORIZATION_PRESENT=false\n'
REMOTE
) || {
  printf '%s\n' "$EXECUTOR_OUTPUT" >&2
  fail "executor_stage_failed"
}

printf '%s\n' "$RECORDER_OUTPUT"
printf '%s\n' "$EXECUTOR_OUTPUT"
printf 'PHASE15_FAST_LIVE_STAGE=PASS\n'
printf 'RELEASE_HEAD=%s\n' "$HEAD"
printf 'RELEASE_SHA256=%s\n' "$ARCHIVE_SHA"
printf 'AUTHORIZATION_CREATED=false\n'
printf 'PROJECT_STATE_STAGED=false\n'
printf 'TELEGRAM_APPROVAL_RELEASE_STAGED=true\n'
printf 'TELEGRAM_APPROVAL_RESTARTED=false\n'
printf 'SERVICES_STARTED=false\n'
printf 'SERVICES_ENABLED=false\n'
printf 'PUBSUB_RESOURCES_MUTATED=false\n'
printf 'KILL_SWITCH_REMOVED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
