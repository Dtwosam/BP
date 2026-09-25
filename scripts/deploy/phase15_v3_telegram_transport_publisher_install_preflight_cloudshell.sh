#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
RELEASE_ARCHIVE="${PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

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

VERIFY_JSON=$(
  python3 "$ROOT/scripts/deploy/phase15_v3_telegram_transport_verify_release.py"     "$RELEASE_ARCHIVE"     --expected-commit-sha "$LOCAL_HEAD"
) || fail "transport_release_verification_failed"

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
        "wallet_material_allowed_on_us_host": gate[
            "wallet_material_allowed_on_us_host"
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

if ! python3 - "$VERIFY_JSON" "$SOURCE_JSON" "$LOCAL_HEAD" <<'PY'
import json
import sys

verified = json.loads(sys.argv[1])
source = json.loads(sys.argv[2])
head = sys.argv[3]

assert verified["status"] == "verified"
assert verified["commit_sha"] == head
assert verified["contains_secret_files"] is False
assert verified["contains_environment_files"] is False
assert verified["contains_key_files"] is False
assert verified["production_mutation_performed"] is False
assert verified["network_action_performed"] is False
assert verified["real_order_submitted"] is False

assert source["live_trading_enabled"] is False
assert source["phase15_live_trading_enabled"] is False
assert source["canary_order_submitted"] is True
assert source["second_order_authorized"] is True
assert source["automated_real_money_submission"] is True
assert source["manual_real_money_submission_required"] is False
assert source["wallet_material_allowed_on_us_host"] is False
assert source["telegram_one_tap_submission_authorized"] is True
assert source["telegram_persistent_execution_transport_authorized"] is True
assert source["telegram_pubsub_transport_authorized"] is True
PY
then
  fail "local_preflight_not_safe"
fi

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

INSTANCE_JSON=$(
  gcloud compute instances describe "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --format=json
) || fail "recorder_instance_describe_failed"

HOST_JSON=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail

python3 - <<'PY'
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


def command(*args: str) -> tuple[int, str]:
    completed = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def path_info(path: str) -> dict[str, object]:
    candidate = Path(path)
    try:
        info = candidate.lstat()
    except OSError:
        return {"exists": False}
    return {
        "exists": True,
        "is_symlink": stat.S_ISLNK(info.st_mode),
        "is_dir": stat.S_ISDIR(info.st_mode),
        "is_file": stat.S_ISREG(info.st_mode),
        "mode": oct(stat.S_IMODE(info.st_mode)),
    }


core_units = [
    "bp-postgres.service",
    "bp-recorder.service",
    "bp-v3-frozen-predictor.service",
    "bp-v3-paper-execution.service",
]
core_state: dict[str, dict[str, object]] = {}
for unit in core_units:
    active_rc, active = command("systemctl", "is-active", unit)
    pid_rc, pid = command("systemctl", "show", "-p", "MainPID", "--value", unit)
    type_rc, service_type = command("systemctl", "show", "-p", "Type", "--value", unit)
    remain_rc, remain_after_exit = command(
        "systemctl", "show", "-p", "RemainAfterExit", "--value", unit
    )
    core_state[unit] = {
        "active": active_rc == 0 and active == "active",
        "main_pid": pid if pid_rc == 0 else "",
        "type": service_type if type_rc == 0 else "",
        "remain_after_exit": remain_after_exit if remain_rc == 0 else "",
    }

publisher = "bp-phase15-telegram-pubsub-publisher.service"
active_rc, active = command("systemctl", "is-active", publisher)
enabled_rc, enabled = command("systemctl", "is-enabled", publisher)

bp_rc, bp_text = command("id", "bp")
disk = shutil.disk_usage("/")

payload = {
    "hostname": os.uname().nodename,
    "bp_user_exists": bp_rc == 0 and bool(bp_text),
    "python3_present": bool(shutil.which("python3")),
    "systemctl_present": bool(shutil.which("systemctl")),
    "free_bytes_root": disk.free,
    "core_units": core_state,
    "publisher_unit": {
        "active": active_rc == 0 and active == "active",
        "enabled": enabled_rc == 0 and enabled == "enabled",
    },
    "transport_root": path_info("/opt/bp-telegram-transport"),
    "transport_config": path_info("/etc/bp-telegram-transport"),
    "publisher_env": path_info("/etc/bp/telegram-pubsub-publisher.env"),
    "transport_state": path_info(
        "/var/lib/bp/phase15-canary-telegram-transport"
    ),
    "canary_wallet_root": path_info("/etc/bp-canary"),
    "approval_sidecar": path_info("/opt/bp-phase15-telegram-approval"),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
REMOTE
) || fail "recorder_host_readonly_probe_failed"

python3 -   "$VERIFY_JSON"   "$SOURCE_JSON"   "$INSTANCE_JSON"   "$HOST_JSON"   "$US_VM"   "$US_ZONE" <<'PY'
import json
import sys

(
    verified_raw,
    source_raw,
    instance_raw,
    host_raw,
    expected_vm,
    expected_zone,
) = sys.argv[1:]

verified = json.loads(verified_raw)
source = json.loads(source_raw)
instance = json.loads(instance_raw)
host = json.loads(host_raw)

blockers: list[str] = []
activation_blockers: list[str] = []

if str(instance.get("name") or "") != expected_vm:
    blockers.append("recorder_vm_name_mismatch")
zone = str(instance.get("zone") or "")
if zone and zone.rsplit("/", 1)[-1] != expected_zone:
    blockers.append("recorder_vm_zone_mismatch")

service_accounts = instance.get("serviceAccounts") or []
if not isinstance(service_accounts, list) or len(service_accounts) != 1:
    blockers.append("publisher_service_account_count_not_one")
else:
    account = service_accounts[0]
    if not isinstance(account, dict) or not str(account.get("email") or ""):
        blockers.append("publisher_service_account_missing")
    scopes = account.get("scopes") if isinstance(account, dict) else None
    if (
        not isinstance(scopes, list)
        or "https://www.googleapis.com/auth/cloud-platform" not in scopes
    ):
        activation_blockers.append("publisher_cloud_platform_scope_missing")

if host["bp_user_exists"] is not True:
    blockers.append("bp_user_missing")
if host["python3_present"] is not True:
    blockers.append("recorder_python3_missing")
if host["systemctl_present"] is not True:
    blockers.append("recorder_systemctl_missing")
if int(host["free_bytes_root"]) < 100 * 1024 * 1024:
    blockers.append("recorder_root_free_space_below_100mib")

for unit, state in host["core_units"].items():
    if state["active"] is not True:
        blockers.append(f"core_service_not_active:{unit}")
    pid_valid = str(state["main_pid"]).isdigit() and int(state["main_pid"]) > 0
    active_oneshot = (
        state["active"] is True
        and state.get("type") == "oneshot"
        and state.get("remain_after_exit") == "yes"
    )
    if not pid_valid and not active_oneshot:
        blockers.append(f"core_service_pid_invalid:{unit}")

if host["publisher_unit"]["active"] is True:
    blockers.append("existing_publisher_unit_active")
if host["publisher_unit"]["enabled"] is True:
    blockers.append("existing_publisher_unit_enabled")

if host["transport_root"]["exists"] is True:
    blockers.append("existing_transport_root_present")
if host["transport_config"]["exists"] is True:
    blockers.append("existing_transport_config_present")
if host["publisher_env"]["exists"] is True:
    blockers.append("existing_publisher_env_present")
if host["transport_state"]["exists"] is True:
    blockers.append("existing_transport_state_present")

if host["canary_wallet_root"]["exists"] is True:
    blockers.append("wallet_material_path_present_on_recorder")

report = {
    "ready_for_publisher_install_review": not blockers,
    "blockers": blockers,
    "activation_ready": not blockers and not activation_blockers,
    "activation_blockers": activation_blockers,
    "release": {
        "commit_sha": verified["commit_sha"],
        "manifest_sha256": verified["manifest_sha256"],
        "archive_sha256": verified["archive_sha256"],
        "file_count": verified["file_count"],
    },
    "source_truth": source,
    "recorder_vm": expected_vm,
    "recorder_zone": expected_zone,
    "core_units": host["core_units"],
    "approval_sidecar_present": host["approval_sidecar"]["exists"],
    "publisher_install_present": (
        host["transport_root"]["exists"] is True
        or host["transport_config"]["exists"] is True
        or host["publisher_env"]["exists"] is True
        or host["transport_state"]["exists"] is True
        or host["publisher_unit"]["active"] is True
        or host["publisher_unit"]["enabled"] is True
    ),
    "mutation_performed": False,
    "release_copied": False,
    "iam_changed": False,
    "service_installed": False,
    "service_enabled": False,
    "service_started": False,
    "real_order_submitted": False,
}
print(json.dumps(report, indent=2, sort_keys=True))
if blockers:
    print("TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT_READY=false")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT=BLOCKED")
else:
    print("TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT_READY=true")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_PUBLISHER_INSTALL_PREFLIGHT=PASS")
PY
