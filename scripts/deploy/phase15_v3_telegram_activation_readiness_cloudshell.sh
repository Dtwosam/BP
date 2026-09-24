#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"

fail() {
  echo "PHASE15_V3_TELEGRAM_ACTIVATION_READINESS=FAIL" >&2
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

SOURCE=$(
python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state["phase_15_v3_live_canary"]
payload = {
    "live_trading_enabled": state["live_trading_enabled"],
    "canary_order_submitted": gate["canary_order_submitted"],
    "second_order_authorized": gate["second_order_authorized"],
    "automated_real_money_submission": gate["automated_real_money_submission"],
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

LISTENER=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command='sudo /opt/bp/.venv/bin/python -' <<'PY'
import json
import subprocess
from pathlib import Path

service = "bp-phase15-canary-telegram-approval.service"
env_path = Path("/etc/bp/telegram-approval.env")


def systemctl(*args: str) -> str:
    completed = subprocess.run(
        ["systemctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


keys: list[str] = []
if env_path.is_file():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        if raw and not raw.startswith("#") and "=" in raw:
            keys.append(raw.split("=", 1)[0])

payload = {
    "active": systemctl("is-active", service) == "active",
    "enabled": systemctl("is-enabled", service) == "enabled",
    "env_exists": env_path.is_file(),
    "handoff_env_present": any(key.startswith("BP_TELEGRAM_HANDOFF_") for key in keys),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
) || fail "listener_readiness_probe_failed"

EXECUTOR=$(
  printf '%s' '{"action":"health"}' |
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT"     --zone="$EXEC_ZONE"     --quiet     --command='sudo /opt/bp-canary/executor.sh' 2>/dev/null
) || fail "executor_health_probe_failed"

python3 - "$SOURCE" "$LISTENER" "$EXECUTOR" <<'PY'
import json
import sys
from decimal import Decimal

source = json.loads(sys.argv[1])
listener = json.loads(sys.argv[2])
executor = json.loads(sys.argv[3])

blockers: list[str] = []
if source["live_trading_enabled"] is not False:
    blockers.append("global_live_trading_not_disabled")
if source["canary_order_submitted"] is not True:
    blockers.append("first_canary_not_recorded")
if source["second_order_authorized"] is not True:
    blockers.append("second_order_not_authorized")
if source["automated_real_money_submission"] is not True:
    blockers.append("automated_submission_not_authorized")
if source["telegram_one_tap_submission_authorized"] is not True:
    blockers.append("telegram_one_tap_not_authorized")
if source["telegram_persistent_execution_transport_authorized"] is not True:
    blockers.append("persistent_execution_transport_not_authorized")
if source["telegram_pubsub_transport_authorized"] is not True:
    blockers.append("telegram_pubsub_transport_not_authorized")
if listener["active"] is not True:
    blockers.append("telegram_listener_not_active")
if listener["enabled"] is not True:
    blockers.append("telegram_listener_not_enabled")
if listener["env_exists"] is not True:
    blockers.append("telegram_listener_env_missing")
if listener["handoff_env_present"] is True:
    blockers.append("listener_contains_unreviewed_handoff_env")

if executor.get("status") != "ok":
    blockers.append("executor_health_not_ok")
geoblock = executor.get("geoblock") or {}
if geoblock.get("blocked") is not False or geoblock.get("country") != "ZA":
    blockers.append("executor_geoblock_not_eligible")
account = executor.get("account") or {}
if int(account.get("open_order_count", -1)) != 0:
    blockers.append("official_open_orders_present")
if Decimal(str(account.get("collateral_balance_usd", "-1"))) < Decimal("5"):
    blockers.append("insufficient_official_collateral")
if account.get("clean_for_canary") is not True:
    blockers.append("official_account_not_clean")
if executor.get("kill_switch_engaged") is not True:
    blockers.append("kill_switch_not_engaged")
if executor.get("activation_valid") is not False:
    blockers.append("unexpected_active_authorization")
if executor.get("submission_ready") is not False:
    blockers.append("unexpected_submission_ready")
if executor.get("live_order_submitted") is not False:
    blockers.append("executor_reports_live_order_submitted")

report = {
    "activation_ready": not blockers,
    "blockers": blockers,
    "source_truth": source,
    "listener": listener,
    "executor_safe_idle": all(
        item not in blockers
        for item in (
            "executor_health_not_ok",
            "executor_geoblock_not_eligible",
            "official_open_orders_present",
            "insufficient_official_collateral",
            "official_account_not_clean",
            "kill_switch_not_engaged",
            "unexpected_active_authorization",
            "unexpected_submission_ready",
            "executor_reports_live_order_submitted",
        )
    ),
}
print(json.dumps(report, indent=2, sort_keys=True))
if blockers:
    print("TELEGRAM_ACTIVATION_READY=false")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_ACTIVATION_READINESS=BLOCKED")
else:
    print("TELEGRAM_ACTIVATION_READY=true")
    print("NO_MUTATION_PERFORMED=true")
    print("PHASE15_V3_TELEGRAM_ACTIVATION_READINESS=PASS")
PY
