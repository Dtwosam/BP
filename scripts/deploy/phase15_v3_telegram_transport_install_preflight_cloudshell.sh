#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
RELEASE_ARCHIVE="${PHASE15_TELEGRAM_TRANSPORT_RELEASE_ARCHIVE:-}"

fail() {
  echo "PHASE15_V3_TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT=FAIL" >&2
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
  gcloud compute instances describe "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --format=json
) || fail "executor_instance_describe_failed"

HOST_JSON=$(
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo bash -s' <<'REMOTE'
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


transport_units = [
    "bp-phase15-telegram-pubsub-streaming-receiver.service",
    "bp-phase15-telegram-transport-claim-worker.service",
    "bp-phase15-telegram-execution-authorization-worker.service",
]
unit_state: dict[str, dict[str, object]] = {}
for unit in transport_units:
    active_rc, active = command("systemctl", "is-active", unit)
    enabled_rc, enabled = command("systemctl", "is-enabled", unit)
    unit_state[unit] = {
        "active": active_rc == 0 and active == "active",
        "enabled": enabled_rc == 0 and enabled == "enabled",
    }

user_rc, user_text = command("id", "bp-transport")
python_path = shutil.which("python3")
systemctl_path = shutil.which("systemctl")
disk = shutil.disk_usage("/")

payload = {
    "hostname": os.uname().nodename,
    "bp_transport_user_exists": user_rc == 0 and bool(user_text),
    "python3_present": bool(python_path),
    "systemctl_present": bool(systemctl_path),
    "free_bytes_root": disk.free,
    "transport_root": path_info("/opt/bp-telegram-transport"),
    "transport_config": path_info("/etc/bp-telegram-transport"),
    "transport_units": unit_state,
    "executor_script": path_info("/opt/bp-canary/executor.sh"),
    "kill_switch": path_info("/etc/bp-canary/KILL"),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
REMOTE
) || fail "executor_host_readonly_probe_failed"

EXECUTOR_JSON=$(
  printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null
) || fail "executor_health_probe_failed"

python3 -   "$VERIFY_JSON"   "$SOURCE_JSON"   "$INSTANCE_JSON"   "$HOST_JSON"   "$EXECUTOR_JSON"   "$EXEC_VM"   "$EXEC_ZONE" <<'PY'
import json
import sys

(
    verified_raw,
    source_raw,
    instance_raw,
    host_raw,
    executor_raw,
    expected_vm,
    expected_zone,
) = sys.argv[1:]

verified = json.loads(verified_raw)
source = json.loads(source_raw)
instance = json.loads(instance_raw)
host = json.loads(host_raw)
executor = json.loads(executor_raw)

blockers: list[str] = []
activation_blockers: list[str] = []

if str(instance.get("name") or "") != expected_vm:
    blockers.append("executor_vm_name_mismatch")
zone = str(instance.get("zone") or "")
if zone and zone.rsplit("/", 1)[-1] != expected_zone:
    blockers.append("executor_vm_zone_mismatch")

service_accounts = instance.get("serviceAccounts") or []
if not isinstance(service_accounts, list) or len(service_accounts) != 1:
    blockers.append("executor_service_account_count_not_one")
else:
    account = service_accounts[0]
    if not isinstance(account, dict) or not str(account.get("email") or ""):
        blockers.append("executor_service_account_missing")
    scopes = account.get("scopes") if isinstance(account, dict) else None
    if (
        not isinstance(scopes, list)
        or "https://www.googleapis.com/auth/cloud-platform" not in scopes
    ):
        activation_blockers.append("executor_cloud_platform_scope_missing")

if host["python3_present"] is not True:
    blockers.append("executor_python3_missing")
if host["systemctl_present"] is not True:
    blockers.append("executor_systemctl_missing")
if int(host["free_bytes_root"]) < 100 * 1024 * 1024:
    blockers.append("executor_root_free_space_below_100mib")

for unit, state in host["transport_units"].items():
    if state["active"] is True:
        blockers.append(f"existing_transport_unit_active:{unit}")
    if state["enabled"] is True:
        blockers.append(f"existing_transport_unit_enabled:{unit}")

if host["transport_root"]["exists"] is True:
    blockers.append("existing_transport_root_present")
if host["transport_config"]["exists"] is True:
    blockers.append("existing_transport_config_present")

if host["executor_script"]["exists"] is not True:
    blockers.append("executor_script_missing")
if host["executor_script"]["is_symlink"] is True:
    blockers.append("executor_script_is_symlink")
if host["kill_switch"]["exists"] is not True:
    blockers.append("kill_switch_missing")

if executor.get("status") != "ok":
    blockers.append("executor_health_not_ok")
if executor.get("kill_switch_engaged") is not True:
    blockers.append("kill_switch_not_engaged")
if executor.get("activation_valid") is not False:
    blockers.append("unexpected_active_authorization")
if executor.get("submission_ready") is not False:
    blockers.append("unexpected_submission_ready")
if executor.get("live_order_submitted") is not False:
    blockers.append("executor_reports_live_order_submitted")

geoblock = executor.get("geoblock") or {}
if geoblock.get("blocked") is not False or geoblock.get("country") != "ZA":
    blockers.append("executor_geoblock_not_eligible")

report = {
    "ready_for_install_review": not blockers,
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
    "executor_vm": expected_vm,
    "executor_zone": expected_zone,
    "bp_transport_user_exists": host["bp_transport_user_exists"],
    "transport_install_present": (
        host["transport_root"]["exists"] is True
        or host["transport_config"]["exists"] is True
        or any(
            state["active"] is True or state["enabled"] is True
            for state in host["transport_units"].values()
        )
    ),
    "executor_safe_idle": all(
        item not in blockers
        for item in (
            "executor_health_not_ok",
            "kill_switch_not_engaged",
            "unexpected_active_authorization",
            "unexpected_submission_ready",
            "executor_reports_live_order_submitted",
            "executor_geoblock_not_eligible",
        )
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
    print("TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT_READY=false")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT=BLOCKED")
else:
    print("TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT_READY=true")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_TRANSPORT_INSTALL_PREFLIGHT=PASS")
PY
