#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_TELEGRAM_AUTO_APPROVER_CONTINUOUS_PREFLIGHT=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "repository_missing"
cd "$ROOT"

STATE="$ROOT/PROJECT_STATE.json"
AUTO_ENV="$HOME/.config/bp/telegram-auto-approver.env"
PLIST="$HOME/Library/LaunchAgents/com.bp.telegram-auto-approver.plist"
LABEL="com.bp.telegram-auto-approver"
DOMAIN="gui/$(id -u)"

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
command -v launchctl >/dev/null 2>&1 || fail "launchctl_missing"
command -v plutil >/dev/null 2>&1 || fail "plutil_missing"
[[ "$(uname -s)" == "Darwin" ]] || fail "operator_platform_must_be_macos"
[[ -f "$STATE" && ! -L "$STATE" ]] || fail "project_state_invalid"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTO_STATUS CONTRACT_SHA < <(
  PYTHONPATH="$ROOT/ops/telegram_auto_approver:$ROOT/src"     python3 - "$STATE" <<'PY'
import ast
import json
import sys
from pathlib import Path

from bp_telegram_auto_approver.contract import (
    APPROVAL_CONTRACT_BLOB_SHA,
    approval_source,
    parse_prompt,
    verify_approval_contract,
)

state_path = Path(sys.argv[1]).resolve()
repo_root = state_path.parent


def fast_live_contract_pin() -> str:
    source = (
        repo_root / "src" / "bp_engine" / "execution" / "fast_live.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA"
            ):
                value = ast.literal_eval(node.value)
                if not isinstance(value, str):
                    raise SystemExit("approval_contract_fast_live_pin_invalid")
                return value
    raise SystemExit("approval_contract_fast_live_pin_missing")


state = json.loads(state_path.read_text(encoding="utf-8"))
phase = state.get("phase_15_v3_live_canary")
if not isinstance(phase, dict):
    raise SystemExit("phase15_missing")
auto = phase.get("operator_telegram_auto_approver")
if not isinstance(auto, dict):
    raise SystemExit("auto_approver_source_truth_missing")
if not str(auto.get("status") or "").startswith("ACTIVE_"):
    raise SystemExit("auto_approver_source_truth_not_active")
if auto.get("live_auto_approve_authorized") is not True:
    raise SystemExit("auto_approver_source_truth_not_authorized")
if state.get("live_trading_enabled") is not False:
    raise SystemExit("global_live_flag_not_false")
if phase.get("live_trading_enabled") is not False:
    raise SystemExit("phase_live_flag_not_false")
if phase.get("fast_live_preauthorization") is not None:
    raise SystemExit("continuous_fast_live_authorization_already_present")
actual = verify_approval_contract()
fast_live_pin = fast_live_contract_pin()
if actual != fast_live_pin:
    raise SystemExit("approval_contract_fast_live_pin_mismatch")
if APPROVAL_CONTRACT_BLOB_SHA != fast_live_pin:
    raise SystemExit("approval_contract_auto_approver_pin_mismatch")
approval = approval_source()

from datetime import UTC, datetime, timedelta

now = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
prepared = {
    "status": "prepared",
    "action": "submit",
    "intent_id": "continuous-preflight-intent",
    "prediction_id": "continuous-preflight-prediction",
    "paper_order_id": "continuous-preflight-paper",
    "market_end_at": (now + timedelta(seconds=50)).isoformat(),
    "request": {
        "selected_side": "up",
        "limit_price": "0.59",
        "requested_shares": "8.238141",
        "target_notional_usd": "5",
    },
    "policy": {
        "policy_version": "v3-live-canary-v1",
        "max_submission_attempts": 1,
    },
}
prompt = approval.build_candidate_prompt(prepared, observed_at=now)
if parse_prompt(prompt) is None:
    raise SystemExit("exact_candidate_prompt_not_accepted")
if parse_prompt(prompt.replace(
    "Approval does not bypass them.",
    "Approval bypasses them.",
)) is not None:
    raise SystemExit("mutated_candidate_prompt_not_rejected")
print(str(auto["status"]), actual)
PY
) || fail "source_truth_or_candidate_contract_invalid"

[[ -r "$AUTO_ENV" ]] || fail "auto_approver_env_missing"
grep -qx 'BP_TELEGRAM_AUTO_APPROVE=true' "$AUTO_ENV" ||
  fail "auto_approver_not_live_enabled"
[[ -f "$PLIST" && ! -L "$PLIST" ]] || fail "auto_approver_plist_missing"

WORKING_DIR="$(plutil -extract WorkingDirectory raw -o - "$PLIST" 2>/dev/null || true)"
[[ "$WORKING_DIR" == "$ROOT" ]] || fail "auto_approver_plist_wrong_checkout"
PYTHONPATH_VALUE="$(
  plutil -extract EnvironmentVariables.PYTHONPATH raw -o - "$PLIST" 2>/dev/null || true
)"
EXPECTED_PYTHONPATH="$ROOT/ops/telegram_auto_approver:$ROOT/src"
[[ "$PYTHONPATH_VALUE" == "$EXPECTED_PYTHONPATH" ]] ||
  fail "auto_approver_plist_pythonpath_mismatch"

LAUNCH_STATE="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)" ||
  fail "auto_approver_service_not_running"
printf '%s\n' "$LAUNCH_STATE" |
  grep -E -q '(^|[[:space:]])"?BP_TELEGRAM_AUTO_APPROVE"?[[:space:]]*=>[[:space:]]*"?true"?([[:space:]]|$)' ||
  fail "auto_approver_launchd_not_live_enabled"
SERVICE_PID="$(
  printf '%s\n' "$LAUNCH_STATE" |
    awk -F'= ' '/^[[:space:]]*pid = / {gsub(/[^0-9]/, "", $2); print $2; exit}'
)"
[[ "$SERVICE_PID" =~ ^[0-9]+$ ]] || fail "auto_approver_pid_missing"
kill -0 "$SERVICE_PID" 2>/dev/null || fail "auto_approver_process_not_alive"

printf 'PHASE15_TELEGRAM_AUTO_APPROVER_CONTINUOUS_PREFLIGHT=PASS\n'
printf 'REPOSITORY_MAIN=%s\n' "$HEAD"
printf 'AUTO_APPROVER_SOURCE_TRUTH_STATUS=%s\n' "$AUTO_STATUS"
printf 'APPROVAL_CONTRACT_GIT_BLOB_SHA=%s\n' "$CONTRACT_SHA"
printf 'AUTO_APPROVER_SERVICE_ACTIVE=true\n'
printf 'AUTO_APPROVER_SERVICE_PID=%s\n' "$SERVICE_PID"
printf 'AUTO_APPROVE_RUNTIME_CONFIGURED=true\n'
printf 'AUTO_APPROVE_LAUNCHD_ENVIRONMENT=true\n'
printf 'EXACT_CANDIDATE_PROMPT_ACCEPTED=true\n'
printf 'MUTATED_CANDIDATE_PROMPT_REJECTED=true\n'
printf 'RESTART_REQUIRED_FOR_RUNNING_PROCESS_UPGRADE=true\n'
printf 'MUTATIONS_PERFORMED=false\n'
printf 'PROJECT_STATE_MUTATED=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
