#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_STATUS=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

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

SOURCE_JSON=$(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
print(json.dumps(
    {
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
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
) || fail "source_truth_read_failed"

RECORDER_JSON=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
python3 - <<'PY'
import hashlib
import json
import os
import pwd
import grp
import stat
import subprocess
from pathlib import Path

ROOT = Path("/opt/bp-telegram-transport")
META = ROOT / "STAGE-METADATA.json"
OWNER = Path("/var/lib/bp/phase15-canary-telegram-transport-stage-owner.json")
CURRENT = ROOT / "current"
VENV = ROOT / ".venv"
HANDOFF = ROOT / "bin" / "approved-outbox-handoff"
STATE = Path("/var/lib/bp/phase15-canary-telegram-transport")
SERVICE = "bp-phase15-telegram-pubsub-publisher.service"
SERVICE_PATH = Path("/etc/systemd/system") / SERVICE
ENV_PATH = Path("/etc/bp/telegram-pubsub-publisher.env")
KEY_PATH = Path("/etc/bp-telegram-transport/transport.key")


def run(*args: str) -> tuple[int, str]:
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def load_json(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def path_info(path: Path) -> dict[str, object]:
    try:
        info = path.lstat()
    except OSError:
        return {"exists": False}
    result: dict[str, object] = {
        "exists": True,
        "is_symlink": stat.S_ISLNK(info.st_mode),
        "is_dir": stat.S_ISDIR(info.st_mode),
        "is_file": stat.S_ISREG(info.st_mode),
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "owner": pwd.getpwuid(info.st_uid).pw_name,
        "group": grp.getgrgid(info.st_gid).gr_name,
    }
    if stat.S_ISLNK(info.st_mode):
        result["target"] = os.readlink(path)
    return result


def sha256(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


active_rc, active = run("systemctl", "is-active", SERVICE)
enabled_rc, enabled = run("systemctl", "is-enabled", SERVICE)

core: dict[str, dict[str, object]] = {}
for unit in (
    "bp-postgres.service",
    "bp-recorder.service",
    "bp-v3-frozen-predictor.service",
    "bp-v3-paper-execution.service",
):
    active_code, active_text = run("systemctl", "is-active", unit)
    pid_code, pid_text = run("systemctl", "show", "-p", "MainPID", "--value", unit)
    type_code, service_type = run("systemctl", "show", "-p", "Type", "--value", unit)
    remain_code, remain_after_exit = run(
        "systemctl", "show", "-p", "RemainAfterExit", "--value", unit
    )
    core[unit] = {
        "active": active_code == 0 and active_text == "active",
        "main_pid": pid_text if pid_code == 0 else "",
        "type": service_type if type_code == 0 else "",
        "remain_after_exit": remain_after_exit if remain_code == 0 else "",
    }

versions: dict[str, str] = {}
python = VENV / "bin" / "python"
if python.is_file():
    code, output = run(
        str(python),
        "-c",
        (
            "from importlib.metadata import version;"
            "print(version('httpx'));"
            "print(version('google-cloud-pubsub'))"
        ),
    )
    if code == 0:
        parts = output.splitlines()
        if len(parts) == 2:
            versions = {"httpx": parts[0], "google-cloud-pubsub": parts[1]}

current_target = ""
if CURRENT.is_symlink():
    current_target = os.readlink(CURRENT)
release_unit = Path(current_target) / "deploy" / SERVICE if current_target else Path("/")
release_handoff = (
    Path(current_target) / "deploy" / "phase15-telegram-approved-outbox-handoff.sh"
    if current_target
    else Path("/")
)

print(json.dumps(
    {
        "metadata": load_json(META),
        "owner": load_json(OWNER),
        "root": path_info(ROOT),
        "current": path_info(CURRENT),
        "state": path_info(STATE),
        "runtime_versions": versions,
        "handoff": {
            **path_info(HANDOFF),
            "installed_sha256": sha256(HANDOFF),
            "release_sha256": sha256(release_handoff),
        },
        "service": {
            "active": active_rc == 0 and active == "active",
            "enabled": enabled_rc == 0 and enabled == "enabled",
            "installed_sha256": sha256(SERVICE_PATH),
            "release_sha256": sha256(release_unit),
        },
        "env_present": ENV_PATH.exists() or ENV_PATH.is_symlink(),
        "transport_key_present": KEY_PATH.exists() or KEY_PATH.is_symlink(),
        "core": core,
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
REMOTE
) || fail "recorder_stage_status_probe_failed"

EXECUTOR_JSON=$(
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
HEALTH=$(printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh)
python3 - "$HEALTH" <<'PY'
import hashlib
import json
import os
import pwd
import grp
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/bp-telegram-transport")
META = ROOT / "STAGE-METADATA.json"
OWNER = Path("/var/lib/bp-canary/telegram-transport-stage-owner.json")
CURRENT = ROOT / "current"
VENV = ROOT / ".venv"
CONFIG = Path("/etc/bp-telegram-transport")
SERVICES = (
    "bp-phase15-telegram-pubsub-streaming-receiver.service",
    "bp-phase15-telegram-transport-claim-worker.service",
    "bp-phase15-telegram-execution-authorization-worker.service",
    "bp-phase15-telegram-privileged-handoff.service",
)
TRANSPORT_STATE_ROOT = Path("/var/lib/bp-telegram-transport")
TRANSPORT_STATE_DIRS = (
    TRANSPORT_STATE_ROOT / "inbox",
    TRANSPORT_STATE_ROOT / "rejections",
    TRANSPORT_STATE_ROOT / "claims",
    TRANSPORT_STATE_ROOT / "ready",
    TRANSPORT_STATE_ROOT / "processed",
    TRANSPORT_STATE_ROOT / "failures",
)
AUTH_STATE_DIRS = (
    Path("/var/lib/bp-canary/telegram-dispatch-claims"),
    Path("/var/lib/bp-canary/telegram-execution-authorized"),
    Path("/var/lib/bp-canary/telegram-execution-auth-processed"),
    Path("/var/lib/bp-canary/telegram-execution-auth-failures"),
    Path("/var/lib/bp-canary/telegram-live-handoff"),
)
STATE_DIRS = TRANSPORT_STATE_DIRS + AUTH_STATE_DIRS
LEGACY_TRANSPORT_STATE_DIRS = (
    Path("/var/lib/bp-canary/telegram-transport-inbox"),
    Path("/var/lib/bp-canary/telegram-transport-rejections"),
    Path("/var/lib/bp-canary/telegram-transport-claims"),
    Path("/var/lib/bp-canary/telegram-transport-ready"),
    Path("/var/lib/bp-canary/telegram-transport-claim-processed"),
    Path("/var/lib/bp-canary/telegram-transport-claim-failures"),
)


def run(*args: str) -> tuple[int, str]:
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def load_json(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def path_info(path: Path) -> dict[str, object]:
    try:
        info = path.lstat()
    except OSError:
        return {"exists": False}
    result: dict[str, object] = {
        "exists": True,
        "is_symlink": stat.S_ISLNK(info.st_mode),
        "is_dir": stat.S_ISDIR(info.st_mode),
        "is_file": stat.S_ISREG(info.st_mode),
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "owner": pwd.getpwuid(info.st_uid).pw_name,
        "group": grp.getgrgid(info.st_gid).gr_name,
    }
    if stat.S_ISLNK(info.st_mode):
        result["target"] = os.readlink(path)
    return result


def sha256(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


services: dict[str, dict[str, object]] = {}
current_target = os.readlink(CURRENT) if CURRENT.is_symlink() else ""
for service in SERVICES:
    active_rc, active = run("systemctl", "is-active", service)
    enabled_rc, enabled = run("systemctl", "is-enabled", service)
    installed = Path("/etc/systemd/system") / service
    release = Path(current_target) / "deploy" / service if current_target else Path("/")
    services[service] = {
        "active": active_rc == 0 and active == "active",
        "enabled": enabled_rc == 0 and enabled == "enabled",
        "installed_sha256": sha256(installed),
        "release_sha256": sha256(release),
    }

versions: dict[str, str] = {}
python = VENV / "bin" / "python"
if python.is_file():
    code, output = run(
        str(python),
        "-c",
        (
            "from importlib.metadata import version;"
            "print(version('httpx'));"
            "print(version('google-cloud-pubsub'))"
        ),
    )
    if code == 0:
        parts = output.splitlines()
        if len(parts) == 2:
            versions = {"httpx": parts[0], "google-cloud-pubsub": parts[1]}

user_rc, _ = run("id", "bp-transport")
group_rc, group = run("id", "-gn", "bp-transport")
receiver_script = (
    Path(current_target)
    / "scripts"
    / "run_phase15_v3_telegram_pubsub_streaming_receive.py"
    if current_target
    else Path("/")
)
claim_script = (
    Path(current_target)
    / "scripts"
    / "run_phase15_v3_telegram_transport_claim_worker.py"
    if current_target
    else Path("/")
)
receiver_read_rc, _ = run(
    "runuser", "-u", "bp-transport", "--", "test", "-r", str(receiver_script)
)
claim_read_rc, _ = run(
    "runuser", "-u", "bp-transport", "--", "test", "-r", str(claim_script)
)
health = json.loads(sys.argv[1])

print(json.dumps(
    {
        "metadata": load_json(META),
        "owner": load_json(OWNER),
        "root": path_info(ROOT),
        "current": path_info(CURRENT),
        "config": path_info(CONFIG),
        "transport_state_root": path_info(TRANSPORT_STATE_ROOT),
        "state_dirs": {str(path): path_info(path) for path in STATE_DIRS},
        "legacy_transport_state_present": any(
            path.exists() or path.is_symlink()
            for path in LEGACY_TRANSPORT_STATE_DIRS
        ),
        "runtime_versions": versions,
        "services": services,
        "bp_transport_user_exists": user_rc == 0,
        "bp_transport_primary_group": group if group_rc == 0 else "",
        "release_service_user_readable": (
            receiver_read_rc == 0 and claim_read_rc == 0
        ),
        "receiver_env_present": (CONFIG / "receiver.env").exists(),
        "claim_env_present": (CONFIG / "claim.env").exists(),
        "execution_auth_env_present": (
            CONFIG / "execution-auth.env"
        ).exists(),
        "privileged_handoff_env_present": (
            CONFIG / "privileged-handoff.env"
        ).exists(),
        "transport_key_present": (CONFIG / "transport.key").exists(),
        "origin_key_present": (CONFIG / "origin.key").exists(),
        "executor_health": health,
    },
    separators=(",", ":"),
    sort_keys=True,
))
PY
REMOTE
) || fail "executor_stage_status_probe_failed"

STAGED_RELEASE_HEAD=$(
  python3 - "$RECORDER_JSON" "$EXECUTOR_JSON" <<'PY'
import json
import sys

recorder = json.loads(sys.argv[1])
executor = json.loads(sys.argv[2])
heads = {
    str(payload.get("release_head") or "")
    for payload in (
        recorder.get("metadata"),
        recorder.get("owner"),
        executor.get("metadata"),
        executor.get("owner"),
    )
    if isinstance(payload, dict)
}
if len(heads) != 1 or "" in heads:
    raise SystemExit("staged release head missing or inconsistent")
print(next(iter(heads)))
PY
) || fail "staged_release_head_resolution_failed"
[[ "$STAGED_RELEASE_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "staged_release_head_invalid"
git cat-file -e "$STAGED_RELEASE_HEAD^{commit}" 2>/dev/null ||
  fail "staged_release_head_missing_locally"

readarray -t RELEASE_BINDING_PATHS < <(
  python3 - "$ROOT/scripts/deploy/phase15_v3_telegram_transport_build_release.py" <<'PY'
import importlib.util
import sys
from pathlib import Path

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("phase15_transport_release", path)
if spec is None or spec.loader is None:
    raise SystemExit("release module load failed")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for value in module.RELEASE_FILES:
    print(value)
PY
) || fail "release_binding_paths_read_failed"
[[ "${#RELEASE_BINDING_PATHS[@]}" -gt 0 ]] || fail "release_binding_paths_empty"

STAGE_BINDING_CURRENT=false
if git diff --quiet "$STAGED_RELEASE_HEAD" "$LOCAL_HEAD" -- "${RELEASE_BINDING_PATHS[@]}"; then
  STAGE_BINDING_CURRENT=true
fi

python3 -   "$SOURCE_JSON"   "$RECORDER_JSON"   "$EXECUTOR_JSON"   "$LOCAL_HEAD"   "$STAGED_RELEASE_HEAD"   "$STAGE_BINDING_CURRENT" <<'PY'
import json
import sys

source = json.loads(sys.argv[1])
recorder = json.loads(sys.argv[2])
executor = json.loads(sys.argv[3])
head = sys.argv[4]
staged_release_head = sys.argv[5]
stage_binding_current = sys.argv[6] == "true"
blockers: list[str] = []

for key, expected in (
    ("live_trading_enabled", False),
    ("phase15_live_trading_enabled", False),
    ("canary_order_submitted", True),
    ("second_order_authorized", True),
    ("automated_real_money_submission", True),
    ("manual_real_money_submission_required", False),
    ("telegram_one_tap_submission_authorized", True),
    ("telegram_persistent_execution_transport_authorized", True),
    ("telegram_pubsub_transport_authorized", True),
):
    if source.get(key) is not expected:
        blockers.append(f"source_truth_{key}_unexpected")

rec_meta = recorder.get("metadata")
rec_owner = recorder.get("owner")
exe_meta = executor.get("metadata")
exe_owner = executor.get("owner")
for name, payload, role in (
    ("recorder_metadata", rec_meta, "publisher"),
    ("recorder_owner", rec_owner, "publisher"),
    ("executor_metadata", exe_meta, "executor"),
    ("executor_owner", exe_owner, "executor"),
):
    if not isinstance(payload, dict):
        blockers.append(f"{name}_missing_or_invalid")
        continue
    if payload.get("role") != role:
        blockers.append(f"{name}_role_mismatch")
    if payload.get("release_head") != staged_release_head:
        blockers.append(f"{name}_release_head_mismatch")
    if name.endswith("metadata") and payload.get("stage_complete") is not True:
        blockers.append(f"{name}_not_complete")

if not stage_binding_current:
    blockers.append("staged_release_binding_changed_after_stage")

stage_ids = {
    str(payload.get("stage_id") or "")
    for payload in (rec_meta, rec_owner, exe_meta, exe_owner)
    if isinstance(payload, dict)
}
if len(stage_ids) != 1 or "" in stage_ids:
    blockers.append("stage_id_mismatch")
archive_hashes = {
    str(payload.get("archive_sha256") or "")
    for payload in (rec_meta, rec_owner, exe_meta, exe_owner)
    if isinstance(payload, dict)
}
if len(archive_hashes) != 1 or "" in archive_hashes:
    blockers.append("archive_sha256_mismatch")

handoff = recorder.get("handoff") or {}
if (
    handoff.get("exists") is not True
    or handoff.get("is_file") is not True
    or handoff.get("is_symlink") is True
    or handoff.get("mode") != "0o750"
    or handoff.get("owner") != "root"
    or handoff.get("group") != "bp"
    or not handoff.get("installed_sha256")
    or handoff.get("installed_sha256") != handoff.get("release_sha256")
):
    blockers.append("approved_outbox_handoff_invalid")

if recorder.get("env_present") is not False:
    blockers.append("publisher_env_present")
if recorder.get("transport_key_present") is not False:
    blockers.append("publisher_transport_key_present")
service = recorder.get("service") or {}
if service.get("active") is not False:
    blockers.append("publisher_service_active")
if service.get("enabled") is not False:
    blockers.append("publisher_service_enabled")
if (
    not service.get("installed_sha256")
    or service.get("installed_sha256") != service.get("release_sha256")
):
    blockers.append("publisher_unit_hash_mismatch")
if recorder.get("runtime_versions") != {
    "httpx": "0.28.1",
    "google-cloud-pubsub": "2.41.0",
}:
    blockers.append("publisher_runtime_versions_mismatch")
for unit, state in (recorder.get("core") or {}).items():
    if state.get("active") is not True:
        blockers.append(f"core_service_not_active:{unit}")
    pid = str(state.get("main_pid") or "")
    pid_valid = pid.isdigit() and int(pid) > 0
    active_oneshot = (
        state.get("active") is True
        and state.get("type") == "oneshot"
        and state.get("remain_after_exit") == "yes"
    )
    if not pid_valid and not active_oneshot:
        blockers.append(f"core_service_pid_invalid:{unit}")

if executor.get("bp_transport_user_exists") is not True:
    blockers.append("bp_transport_user_missing")
if executor.get("bp_transport_primary_group") != "bp-transport":
    blockers.append("bp_transport_primary_group_invalid")
for name, blocker in (
    ("receiver_env_present", "executor_receiver_env_present"),
    ("claim_env_present", "executor_claim_env_present"),
    ("execution_auth_env_present", "executor_execution_auth_env_present"),
    ("privileged_handoff_env_present", "executor_privileged_handoff_env_present"),
    ("transport_key_present", "executor_transport_key_present"),
    ("origin_key_present", "executor_origin_key_present"),
):
    if executor.get(name) is not False:
        blockers.append(blocker)
for service_name, state in (executor.get("services") or {}).items():
    if state.get("active") is not False:
        blockers.append(f"transport_service_active:{service_name}")
    if state.get("enabled") is not False:
        blockers.append(f"transport_service_enabled:{service_name}")
    if (
        not state.get("installed_sha256")
        or state.get("installed_sha256") != state.get("release_sha256")
    ):
        blockers.append(f"transport_unit_hash_mismatch:{service_name}")
if executor.get("runtime_versions") != {
    "httpx": "0.28.1",
    "google-cloud-pubsub": "2.41.0",
}:
    blockers.append("executor_runtime_versions_mismatch")
transport_state_root = executor.get("transport_state_root") or {}
if (
    transport_state_root.get("exists") is not True
    or transport_state_root.get("is_dir") is not True
    or transport_state_root.get("is_symlink") is True
    or transport_state_root.get("mode") != "0o710"
    or transport_state_root.get("owner") != "root"
    or transport_state_root.get("group") != "bp-transport"
):
    blockers.append("executor_transport_state_root_invalid")
if executor.get("legacy_transport_state_present") is not False:
    blockers.append("legacy_transport_state_present")
if executor.get("release_service_user_readable") is not True:
    blockers.append("executor_release_not_readable_by_service_user")

root_authorization_state_dirs = {
    "/var/lib/bp-canary/telegram-dispatch-claims",
    "/var/lib/bp-canary/telegram-execution-authorized",
    "/var/lib/bp-canary/telegram-execution-auth-processed",
    "/var/lib/bp-canary/telegram-execution-auth-failures",
    "/var/lib/bp-canary/telegram-live-handoff",
}
ready_state_dir = "/var/lib/bp-telegram-transport/ready"
for path, info in (executor.get("state_dirs") or {}).items():
    if path in root_authorization_state_dirs:
        expected_owner = "root"
        expected_group = "root"
        expected_mode = "0o700"
    elif path == ready_state_dir:
        expected_owner = "bp-transport"
        expected_group = "bp-transport"
        expected_mode = "0o750"
    else:
        expected_owner = "bp-transport"
        expected_group = "bp-transport"
        expected_mode = "0o700"
    if (
        info.get("exists") is not True
        or info.get("is_dir") is not True
        or info.get("is_symlink") is True
        or info.get("mode") != expected_mode
        or info.get("owner") != expected_owner
        or info.get("group") != expected_group
    ):
        blockers.append(f"executor_state_dir_invalid:{path}")

health = executor.get("executor_health") or {}
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

report = {
    "stage_ready_for_later_configuration_review": not blockers,
    "blockers": blockers,
    "stage_id": next(iter(stage_ids)) if len(stage_ids) == 1 else None,
    "release_head": staged_release_head,
    "current_head": head,
    "stage_binding_current": stage_binding_current,
    "source_truth": source,
    "publisher_service_active": service.get("active"),
    "executor_services_active": {
        name: state.get("active")
        for name, state in (executor.get("services") or {}).items()
    },
    "environment_files_present": any(
        (
            recorder.get("env_present") is True,
            executor.get("receiver_env_present") is True,
            executor.get("claim_env_present") is True,
            executor.get("execution_auth_env_present") is True,
        )
    ),
    "key_files_present": any(
        (
            recorder.get("transport_key_present") is True,
            executor.get("transport_key_present") is True,
            executor.get("origin_key_present") is True,
        )
    ),
    "mutation_performed": False,
    "service_started": False,
    "service_enabled": False,
    "real_order_submitted": False,
}
print(json.dumps(report, indent=2, sort_keys=True))
if blockers:
    print("TELEGRAM_TRANSPORT_STAGE_READY=false")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_STATUS=BLOCKED")
else:
    print("TELEGRAM_TRANSPORT_STAGE_READY=true")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_STAGE_STATUS=PASS")
PY
