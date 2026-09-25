#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
STAGE_ID="${PHASE15_TELEGRAM_TRANSPORT_STAGE_ID:-}"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_ROLLBACK=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$ACCEPT" == "yes" ]] ||
  fail "explicit_transport_stage_rollback_authorization_required"
[[ "$STAGE_ID" =~ ^phase15-telegram-stage-[0-9a-f]{24}$ ]] ||
  fail "stage_id_invalid"

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

SOURCE_JSON=$(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
payload = {
    "live_trading_enabled": state["live_trading_enabled"],
    "phase15_live_trading_enabled": gate["live_trading_enabled"],
    "canary_order_submitted": gate["canary_order_submitted"],
    "second_order_authorized": gate["second_order_authorized"],
    "automated_real_money_submission": gate["automated_real_money_submission"],
    "manual_real_money_submission_required": gate[
        "manual_real_money_submission_required"
    ],
    "telegram_one_tap_submission_authorized": gate.get(
        "telegram_one_tap_submission_authorized", False
    ),
    "telegram_persistent_execution_transport_authorized": gate.get(
        "telegram_persistent_execution_transport_authorized", False
    ),
    "telegram_pubsub_transport_authorized": gate.get(
        "telegram_pubsub_transport_authorized", False
    ),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
) || fail "source_truth_read_failed"

python3 - "$SOURCE_JSON" <<'PY' || fail "source_truth_not_safe_for_stage_rollback"
import json
import sys

source = json.loads(sys.argv[1])
assert source["live_trading_enabled"] is False
assert source["phase15_live_trading_enabled"] is False
assert source["canary_order_submitted"] is True
assert source["second_order_authorized"] is True
assert source["automated_real_money_submission"] is True
assert source["manual_real_money_submission_required"] is False
assert source["telegram_one_tap_submission_authorized"] is True
assert source["telegram_persistent_execution_transport_authorized"] is True
assert source["telegram_pubsub_transport_authorized"] is True
PY

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

RECORDER_PROBE=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT" --zone="$US_ZONE" --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
python3 - <<'PY'
import json
import stat
import subprocess
from pathlib import Path

ROOT = Path("/opt/bp-telegram-transport")
META = ROOT / "STAGE-METADATA.json"
OWNER = Path("/var/lib/bp/phase15-canary-telegram-transport-stage-owner.json")
STATE = Path("/var/lib/bp/phase15-canary-telegram-transport")
SERVICE = "bp-phase15-telegram-pubsub-publisher.service"
UNIT = Path("/etc/systemd/system") / SERVICE
ENV = Path("/etc/bp/telegram-pubsub-publisher.env")
KEY = Path("/etc/bp-telegram-transport/transport.key")


def run(*args: str) -> tuple[int, str]:
    completed = subprocess.run(list(args), check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout.strip()


def load(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


active_rc, active = run("systemctl", "is-active", SERVICE)
enabled_rc, enabled = run("systemctl", "is-enabled", SERVICE)
artifacts = any(
    (
        ROOT.exists() or ROOT.is_symlink(),
        OWNER.exists() or OWNER.is_symlink(),
        STATE.exists() or STATE.is_symlink(),
        UNIT.exists() or UNIT.is_symlink(),
        ENV.exists() or ENV.is_symlink(),
        KEY.exists() or KEY.is_symlink(),
    )
)
print(json.dumps(
    {
        "artifacts_present": artifacts,
        "owner": load(OWNER),
        "metadata": load(META),
        "service_active": active_rc == 0 and active == "active",
        "service_enabled": enabled_rc == 0 and enabled == "enabled",
        "env_present": ENV.exists() or ENV.is_symlink(),
        "key_present": KEY.exists() or KEY.is_symlink(),
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
REMOTE
) || fail "recorder_stage_probe_failed"

EXECUTOR_PROBE=$(
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
HEALTH=$(printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh)
python3 - "$HEALTH" <<'PY'
import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/bp-telegram-transport")
META = ROOT / "STAGE-METADATA.json"
OWNER = Path("/var/lib/bp-canary/telegram-transport-stage-owner.json")
CONFIG = Path("/etc/bp-telegram-transport")
SERVICES = (
    "bp-phase15-telegram-pubsub-streaming-receiver.service",
    "bp-phase15-telegram-transport-claim-worker.service",
    "bp-phase15-telegram-execution-authorization-worker.service",
    "bp-phase15-telegram-privileged-handoff.service",
)
STATE_DIRS = (
    Path("/var/lib/bp-canary/telegram-transport-inbox"),
    Path("/var/lib/bp-canary/telegram-transport-rejections"),
    Path("/var/lib/bp-canary/telegram-transport-claims"),
    Path("/var/lib/bp-canary/telegram-transport-ready"),
    Path("/var/lib/bp-canary/telegram-transport-claim-processed"),
    Path("/var/lib/bp-canary/telegram-transport-claim-failures"),
    Path("/var/lib/bp-canary/telegram-dispatch-claims"),
    Path("/var/lib/bp-canary/telegram-execution-authorized"),
    Path("/var/lib/bp-canary/telegram-execution-auth-processed"),
    Path("/var/lib/bp-canary/telegram-execution-auth-failures"),
    Path("/var/lib/bp-canary/telegram-live-handoff"),
)


def run(*args: str) -> tuple[int, str]:
    completed = subprocess.run(list(args), check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout.strip()


def load(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


services: dict[str, dict[str, bool]] = {}
unit_present = False
for service in SERVICES:
    unit = Path("/etc/systemd/system") / service
    unit_present = unit_present or unit.exists() or unit.is_symlink()
    active_rc, active = run("systemctl", "is-active", service)
    enabled_rc, enabled = run("systemctl", "is-enabled", service)
    services[service] = {
        "active": active_rc == 0 and active == "active",
        "enabled": enabled_rc == 0 and enabled == "enabled",
    }

secret_paths = (
    CONFIG / "receiver.env",
    CONFIG / "claim.env",
    CONFIG / "execution-auth.env",
    CONFIG / "privileged-handoff.env",
    CONFIG / "transport.key",
    CONFIG / "origin.key",
)
secrets_present = any(path.exists() or path.is_symlink() for path in secret_paths)
state_present = any(path.exists() or path.is_symlink() for path in STATE_DIRS)
artifacts = any(
    (
        ROOT.exists() or ROOT.is_symlink(),
        OWNER.exists() or OWNER.is_symlink(),
        CONFIG.exists() or CONFIG.is_symlink(),
        unit_present,
        state_present,
        secrets_present,
    )
)

print(json.dumps(
    {
        "artifacts_present": artifacts,
        "owner": load(OWNER),
        "metadata": load(META),
        "services": services,
        "secrets_present": secrets_present,
        "executor_health": json.loads(sys.argv[1]),
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
REMOTE
) || fail "executor_stage_probe_failed"

VALIDATION=$(
  python3 - "$RECORDER_PROBE" "$EXECUTOR_PROBE" "$STAGE_ID" <<'PY'
import json
import sys

recorder = json.loads(sys.argv[1])
executor = json.loads(sys.argv[2])
stage_id = sys.argv[3]
blockers: list[str] = []


def validate_side(
    *,
    name: str,
    payload: dict[str, object],
    role: str,
) -> tuple[bool, dict[str, object] | None]:
    present = payload.get("artifacts_present") is True
    if not present:
        return False, None
    owner = payload.get("owner")
    meta = payload.get("metadata")
    if not isinstance(owner, dict):
        blockers.append(f"{name}_owner_missing_or_invalid")
        return True, None
    if not isinstance(meta, dict):
        blockers.append(f"{name}_metadata_missing_or_invalid")
        return True, None
    for label, value in (("owner", owner), ("metadata", meta)):
        if value.get("stage_id") != stage_id:
            blockers.append(f"{name}_{label}_stage_id_mismatch")
        if value.get("role") != role:
            blockers.append(f"{name}_{label}_role_mismatch")
    if meta.get("stage_complete") is not True:
        blockers.append(f"{name}_stage_not_complete")
    for field in ("release_head", "archive_sha256"):
        if owner.get(field) != meta.get(field):
            blockers.append(f"{name}_{field}_mismatch")
    return True, meta


rec_present, rec_meta = validate_side(
    name="recorder",
    payload=recorder,
    role="publisher",
)
exe_present, exe_meta = validate_side(
    name="executor",
    payload=executor,
    role="executor",
)

if rec_present:
    if recorder.get("service_active") is not False:
        blockers.append("publisher_service_active")
    if recorder.get("service_enabled") is not False:
        blockers.append("publisher_service_enabled")
    if recorder.get("env_present") is not False:
        blockers.append("publisher_env_present")
    if recorder.get("key_present") is not False:
        blockers.append("publisher_key_present")

if exe_present:
    for service, state in (executor.get("services") or {}).items():
        if state.get("active") is not False:
            blockers.append(f"executor_service_active:{service}")
        if state.get("enabled") is not False:
            blockers.append(f"executor_service_enabled:{service}")
    if executor.get("secrets_present") is not False:
        blockers.append("executor_secret_or_env_present")

if isinstance(rec_meta, dict) and isinstance(exe_meta, dict):
    if rec_meta.get("release_head") != exe_meta.get("release_head"):
        blockers.append("cross_host_release_head_mismatch")
    if rec_meta.get("archive_sha256") != exe_meta.get("archive_sha256"):
        blockers.append("cross_host_archive_sha256_mismatch")

health = executor.get("executor_health") or {}
if exe_present:
    if health.get("status") != "ok":
        blockers.append("executor_health_not_ok")
    if health.get("kill_switch_engaged") is not True:
        blockers.append("kill_switch_not_engaged")
    if health.get("activation_valid") is not False:
        blockers.append("unexpected_active_authorization")
    if health.get("submission_ready") is not False:
        blockers.append("unexpected_submission_ready")
    if health.get("live_order_submitted") is not False:
        blockers.append("executor_reports_live_order_submitted")
    geoblock = health.get("geoblock") or {}
    if geoblock.get("blocked") is not False or geoblock.get("country") != "ZA":
        blockers.append("executor_geoblock_not_eligible")

print(json.dumps(
    {
        "blockers": blockers,
        "recorder_present": rec_present,
        "executor_present": exe_present,
        "already_absent": not rec_present and not exe_present,
    },
    separators=(",", ":"),
    sort_keys=True,
))
if blockers:
    raise SystemExit(1)
PY
) || fail "stage_rollback_preconditions_failed"

RECORDER_PRESENT=$(
  python3 - "$VALIDATION" <<'PY'
import json
import sys
print("true" if json.loads(sys.argv[1])["recorder_present"] else "false")
PY
)
EXECUTOR_PRESENT=$(
  python3 - "$VALIDATION" <<'PY'
import json
import sys
print("true" if json.loads(sys.argv[1])["executor_present"] else "false")
PY
)

if [[ "$EXECUTOR_PRESENT" == "true" ]]; then
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet     --command="sudo env BP_STAGE_ID='$STAGE_ID' bash -s" <<'REMOTE'
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
STATE_DIRS=(
  /var/lib/bp-canary/telegram-transport-inbox
  /var/lib/bp-canary/telegram-transport-rejections
  /var/lib/bp-canary/telegram-transport-claims
  /var/lib/bp-canary/telegram-transport-ready
  /var/lib/bp-canary/telegram-transport-claim-processed
  /var/lib/bp-canary/telegram-transport-claim-failures
  /var/lib/bp-canary/telegram-dispatch-claims
  /var/lib/bp-canary/telegram-execution-authorized
  /var/lib/bp-canary/telegram-execution-auth-processed
  /var/lib/bp-canary/telegram-execution-auth-failures
  /var/lib/bp-canary/telegram-live-handoff
)

readarray -t FLAGS < <(
  python3 - "$OWNER" "$META" "$BP_STAGE_ID" <<'PY'
import json
import sys
from pathlib import Path

owner = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
meta = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert owner["stage_id"] == sys.argv[3] == meta["stage_id"]
assert owner["role"] == "executor" == meta["role"]
assert meta["stage_complete"] is True
assert owner["release_head"] == meta["release_head"]
assert owner["archive_sha256"] == meta["archive_sha256"]
assert owner.get("created_bp_transport_user") == meta.get("created_bp_transport_user")
assert owner.get("created_bp_transport_group") == meta.get("created_bp_transport_group")
print("true" if owner.get("created_bp_transport_user") is True else "false")
print("true" if owner.get("created_bp_transport_group") is True else "false")
PY
)
CREATED_USER="${FLAGS[0]}"
CREATED_GROUP="${FLAGS[1]}"

for service in "${SERVICES[@]}"; do
  ! systemctl is-active --quiet "$service" ||
    { echo "executor transport service active" >&2; exit 1; }
  ! systemctl is-enabled --quiet "$service" 2>/dev/null ||
    { echo "executor transport service enabled" >&2; exit 1; }
done
for secret in   "$CONFIG/receiver.env"   "$CONFIG/claim.env"   "$CONFIG/execution-auth.env"   "$CONFIG/privileged-handoff.env"   "$CONFIG/transport.key"   "$CONFIG/origin.key"
do
  [[ ! -e "$secret" && ! -L "$secret" ]] ||
    { echo "executor transport secret/config present" >&2; exit 1; }
done

HEALTH_BEFORE=$(printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh)
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

if [[ "$CREATED_USER" == "true" ]]; then
  if pgrep -u bp-transport >/dev/null 2>&1; then
    echo "bp-transport still owns running processes" >&2
    exit 1
  fi
fi

for service in "${SERVICES[@]}"; do
  rm -f "/etc/systemd/system/$service"
done
for dir in "${STATE_DIRS[@]}"; do
  rm -rf "$dir"
done
rm -rf "$CONFIG" "$ROOT"
rm -f "$OWNER"
systemctl daemon-reload

if [[ "$CREATED_USER" == "true" ]] && id bp-transport >/dev/null 2>&1; then
  userdel bp-transport
fi
if [[ "$CREATED_GROUP" == "true" ]] && getent group bp-transport >/dev/null 2>&1; then
  groupdel bp-transport
fi
if [[ "$CREATED_USER" == "true" ]]; then
  ! id bp-transport >/dev/null 2>&1
fi
if [[ "$CREATED_GROUP" == "true" ]]; then
  ! getent group bp-transport >/dev/null 2>&1
fi

HEALTH_AFTER=$(printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh)
python3 - "$HEALTH_AFTER" <<'PY'
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

[[ ! -e "$ROOT" && ! -L "$ROOT" ]]
[[ ! -e "$CONFIG" && ! -L "$CONFIG" ]]
[[ ! -e "$OWNER" && ! -L "$OWNER" ]]
for service in "${SERVICES[@]}"; do
  [[ ! -e "/etc/systemd/system/$service" ]]
done
echo "EXECUTOR_STAGE_ROLLBACK=PASS"
REMOTE
fi

if [[ "$RECORDER_PRESENT" == "true" ]]; then
  gcloud compute ssh "$US_VM"     --project="$PROJECT" --zone="$US_ZONE" --quiet     --command="sudo env BP_STAGE_ID='$STAGE_ID' bash -s" <<'REMOTE'
set -Eeuo pipefail
ROOT=/opt/bp-telegram-transport
META=$ROOT/STAGE-METADATA.json
OWNER=/var/lib/bp/phase15-canary-telegram-transport-stage-owner.json
STATE=/var/lib/bp/phase15-canary-telegram-transport
SERVICE=bp-phase15-telegram-pubsub-publisher.service
UNIT=/etc/systemd/system/$SERVICE
ENV=/etc/bp/telegram-pubsub-publisher.env
KEY=/etc/bp-telegram-transport/transport.key

python3 - "$OWNER" "$META" "$BP_STAGE_ID" <<'PY'
import json
import sys
from pathlib import Path

owner = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
meta = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert owner["stage_id"] == sys.argv[3] == meta["stage_id"]
assert owner["role"] == "publisher" == meta["role"]
assert meta["stage_complete"] is True
assert owner["release_head"] == meta["release_head"]
assert owner["archive_sha256"] == meta["archive_sha256"]
PY

! systemctl is-active --quiet "$SERVICE" ||
  { echo "publisher service active" >&2; exit 1; }
! systemctl is-enabled --quiet "$SERVICE" 2>/dev/null ||
  { echo "publisher service enabled" >&2; exit 1; }
[[ ! -e "$ENV" && ! -L "$ENV" ]] ||
  { echo "publisher env present" >&2; exit 1; }
[[ ! -e "$KEY" && ! -L "$KEY" ]] ||
  { echo "publisher transport key present" >&2; exit 1; }

RECORDER_PID_BEFORE=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_BEFORE=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
for unit in   bp-postgres.service   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service
do
  systemctl is-active --quiet "$unit"
done

rm -f "$UNIT"
rm -rf "$STATE" "$ROOT"
rm -f "$OWNER"
systemctl daemon-reload

RECORDER_PID_AFTER=$(systemctl show -p MainPID --value bp-recorder.service)
PREDICTOR_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-frozen-predictor.service)
PAPER_PID_AFTER=$(systemctl show -p MainPID --value bp-v3-paper-execution.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]]
[[ "$PREDICTOR_PID_AFTER" == "$PREDICTOR_PID_BEFORE" ]]
[[ "$PAPER_PID_AFTER" == "$PAPER_PID_BEFORE" ]]
[[ ! -e "$ROOT" && ! -L "$ROOT" ]]
[[ ! -e "$STATE" && ! -L "$STATE" ]]
[[ ! -e "$OWNER" && ! -L "$OWNER" ]]
[[ ! -e "$UNIT" && ! -L "$UNIT" ]]
echo "RECORDER_STAGE_ROLLBACK=PASS"
REMOTE
fi

echo "STAGE_ID=$STAGE_ID"
echo "RECORDER_STAGE_REMOVED=$RECORDER_PRESENT"
echo "EXECUTOR_STAGE_REMOVED=$EXECUTOR_PRESENT"
echo "SERVICES_STARTED=false"
echo "SERVICES_ENABLED=false"
echo "KEY_FILES_REMOVED=false"
echo "ENVIRONMENT_FILES_REMOVED=false"
echo "IAM_CHANGED=false"
echo "PUBSUB_RESOURCES_CHANGED=false"
echo "LIVE_TRADING_ENABLED=false"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_ROLLBACK=PASS"
