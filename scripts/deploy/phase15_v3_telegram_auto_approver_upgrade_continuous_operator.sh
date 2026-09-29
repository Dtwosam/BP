#!/usr/bin/env bash
set -euo pipefail
umask 077

fail() {
  printf 'PHASE15_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE=FAIL:%s\n' "$1" >&2
  exit 1
}

ACCEPT="${PHASE15_ACCEPT_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE:-}"
[[ "$ACCEPT" == "I_ACCEPT_RESTART_AUTO_APPROVER_WITH_CONTINUOUS_CANDIDATE_CONTRACT" ]] ||
  fail "explicit_auto_approver_continuous_upgrade_acceptance_required"

: "${BP_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE_EVIDENCE:?BP_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE_EVIDENCE is required}"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "repository_missing"
cd "$ROOT"

STATE="$ROOT/PROJECT_STATE.json"
AUTO_ENV="$HOME/.config/bp/telegram-auto-approver.env"
PLIST="$HOME/Library/LaunchAgents/com.bp.telegram-auto-approver.plist"
LABEL="com.bp.telegram-auto-approver"
DOMAIN="gui/$(id -u)"
EVIDENCE="$BP_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE_EVIDENCE"

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
command -v launchctl >/dev/null 2>&1 || fail "launchctl_missing"
command -v plutil >/dev/null 2>&1 || fail "plutil_missing"
[[ "$(uname -s)" == "Darwin" ]] || fail "operator_platform_must_be_macos"
[[ -f "$STATE" && ! -L "$STATE" ]] || fail "project_state_invalid"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

[[ "$EVIDENCE" = /* ]] || fail "evidence_path_must_be_absolute"
[[ ! -e "$EVIDENCE" ]] || fail "evidence_path_already_exists"
mkdir -p "$(dirname "$EVIDENCE")"
chmod 0700 "$(dirname "$EVIDENCE")"
EVIDENCE_DIR="$(cd "$(dirname "$EVIDENCE")" && pwd)"
case "$EVIDENCE_DIR/" in
  "$ROOT/docs/evidence/"*) ;;
  *) fail "evidence_path_must_be_under_repo_docs_evidence" ;;
esac

HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTO_STATUS CONTRACT_SHA < <(
  PYTHONPATH="$ROOT/ops/telegram_auto_approver:$ROOT/src"     python3 - "$STATE" <<'PY'
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bp_engine.execution.fast_live import (
    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA,
)
from bp_engine.execution.telegram_approval import (
    build_candidate_prompt,
    callback_data,
)
from bp_telegram_auto_approver.contract import (
    APPROVAL_CONTRACT_BLOB_SHA,
    CallbackButton,
    parse_prompt,
    verify_approval_contract,
)
from bp_telegram_auto_approver.decider import (
    Decider,
    IncomingMessage,
    PreparedClick,
)
from bp_telegram_auto_approver.log import ListLogger
from bp_telegram_auto_approver.state import ApprovalStore
import tempfile

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
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
if actual != FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA:
    raise SystemExit("approval_contract_fast_live_pin_mismatch")
if APPROVAL_CONTRACT_BLOB_SHA != FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA:
    raise SystemExit("approval_contract_auto_approver_pin_mismatch")

now = datetime.now(UTC)
prepared = {
    "status": "prepared",
    "action": "submit",
    "intent_id": "continuous-upgrade-selftest-intent",
    "prediction_id": "continuous-upgrade-selftest-prediction",
    "paper_order_id": "continuous-upgrade-selftest-paper",
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
prompt = build_candidate_prompt(prepared, observed_at=now)
parsed = parse_prompt(prompt)
if parsed is None:
    raise SystemExit("exact_candidate_prompt_not_accepted")
mutated = prompt.replace(
    "Approval does not bypass them.",
    "Approval bypasses them.",
)
if parse_prompt(mutated) is not None:
    raise SystemExit("mutated_candidate_prompt_not_rejected")

class Clock:
    def now(self):
        return now + timedelta(seconds=1)

with tempfile.TemporaryDirectory() as directory:
    store = ApprovalStore(Path(directory) / "state.sqlite")
    logger = ListLogger()
    decider = Decider(
        store=store,
        bot_user_id=424242,
        bot_username="bp_approval_bot",
        live=True,
        started_at=now - timedelta(seconds=1),
        clock=Clock(),
        logger=logger,
    )
    decider.confirm_identity(
        user_id=424242,
        username="bp_approval_bot",
        is_bot=True,
    )
    nonce = "Abcdefghijklmnop"
    keyboard = (
        (
            CallbackButton(
                "APPROVE",
                callback_data("approve", nonce).encode("ascii"),
            ),
            CallbackButton(
                "SKIP",
                callback_data("skip", nonce).encode("ascii"),
            ),
        ),
    )
    message = IncomingMessage(
        message_id=1,
        chat_id=424242,
        sender_id=424242,
        sender_username="bp_approval_bot",
        text=prompt,
        sent_at=now,
        edit_date=None,
        forwarded=False,
        reply=False,
        outgoing=False,
        is_private=True,
        buttons=keyboard,
    )
    outcome = decider.prepare(message)
    if not isinstance(outcome, PreparedClick):
        raise SystemExit("exact_candidate_prompt_not_prepared_for_auto_click")
    if outcome.callback_data != callback_data(
        "approve",
        nonce,
    ).encode("ascii"):
        raise SystemExit("candidate_approve_callback_changed")
    store.close()

print(str(auto["status"]), actual)
PY
) || fail "source_truth_or_candidate_contract_invalid"

[[ -r "$AUTO_ENV" && ! -L "$AUTO_ENV" ]] || fail "auto_approver_env_missing"
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
PYTHON_BIN="$(
  plutil -extract ProgramArguments.0 raw -o - "$PLIST" 2>/dev/null || true
)"
ARG1="$(plutil -extract ProgramArguments.1 raw -o - "$PLIST" 2>/dev/null || true)"
ARG2="$(plutil -extract ProgramArguments.2 raw -o - "$PLIST" 2>/dev/null || true)"
[[ -x "$PYTHON_BIN" ]] || fail "auto_approver_python_missing"
[[ "$ARG1" == "-m" && "$ARG2" == "bp_telegram_auto_approver" ]] ||
  fail "auto_approver_program_arguments_invalid"

LAUNCH_STATE_BEFORE="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)" ||
  fail "auto_approver_service_not_running_before"
printf '%s\n' "$LAUNCH_STATE_BEFORE" |
  grep -E -q '(^|[[:space:]])"?BP_TELEGRAM_AUTO_APPROVE"?[[:space:]]*=>[[:space:]]*"?true"?([[:space:]]|$)' ||
  fail "auto_approver_launchd_not_live_enabled_before"
OLD_PID="$(
  printf '%s\n' "$LAUNCH_STATE_BEFORE" |
    awk -F'= ' '/^[[:space:]]*pid = / {gsub(/[^0-9]/, "", $2); print $2; exit}'
)"
[[ "$OLD_PID" =~ ^[0-9]+$ ]] || fail "auto_approver_old_pid_missing"
kill -0 "$OLD_PID" 2>/dev/null || fail "auto_approver_old_process_not_alive"

CHECK_OUTPUT="$(mktemp)"
cleanup() { rm -f "$CHECK_OUTPUT"; }
trap cleanup EXIT
(
  set -a
  # shellcheck disable=SC1090
  source "$AUTO_ENV"
  set +a
  PYTHONPATH="$PYTHONPATH_VALUE"     "$PYTHON_BIN" -m bp_telegram_auto_approver --check-config
) >"$CHECK_OUTPUT" 2>&1 || fail "auto_approver_config_check_failed"
grep -F -q '"event":"CONFIG_OK"' "$CHECK_OUTPUT" ||
  grep -F -q '"event": "CONFIG_OK"' "$CHECK_OUTPUT" ||
  fail "auto_approver_config_check_missing"
grep -F -q '"mode":"live-auto-approve"' "$CHECK_OUTPUT" ||
  grep -F -q '"mode": "live-auto-approve"' "$CHECK_OUTPUT" ||
  fail "auto_approver_config_not_live"

launchctl kickstart -k "$DOMAIN/$LABEL" ||
  fail "auto_approver_restart_failed"

NEW_PID=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  NEW_PID="$(
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null |
      awk -F'= ' '/^[[:space:]]*pid = / {gsub(/[^0-9]/, "", $2); print $2; exit}' ||
      true
  )"
  if [[ "$NEW_PID" =~ ^[0-9]+$ && "$NEW_PID" != "$OLD_PID" ]] &&
     kill -0 "$NEW_PID" 2>/dev/null; then
    break
  fi
done
[[ "$NEW_PID" =~ ^[0-9]+$ ]] || fail "auto_approver_new_pid_missing"
[[ "$NEW_PID" != "$OLD_PID" ]] || fail "auto_approver_pid_did_not_change"
kill -0 "$NEW_PID" 2>/dev/null || fail "auto_approver_new_process_not_alive"

sleep 3
STABLE_PID="$(
  launchctl print "$DOMAIN/$LABEL" 2>/dev/null |
    awk -F'= ' '/^[[:space:]]*pid = / {gsub(/[^0-9]/, "", $2); print $2; exit}' ||
    true
)"
[[ "$STABLE_PID" == "$NEW_PID" ]] ||
  fail "auto_approver_process_not_stable_after_restart"
kill -0 "$STABLE_PID" 2>/dev/null ||
  fail "auto_approver_stable_process_not_alive"
LAUNCH_STATE_AFTER="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null)" ||
  fail "auto_approver_service_not_running_after"
printf '%s\n' "$LAUNCH_STATE_AFTER" |
  grep -E -q '(^|[[:space:]])"?BP_TELEGRAM_AUTO_APPROVE"?[[:space:]]*=>[[:space:]]*"?true"?([[:space:]]|$)' ||
  fail "auto_approver_launchd_not_live_enabled_after"

python3 - "$STATE" "$EVIDENCE" "$HEAD" "$AUTO_STATUS" "$CONTRACT_SHA"   "$OLD_PID" "$NEW_PID" <<'PY'
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

state_path = Path(sys.argv[1])
evidence_path = Path(sys.argv[2])
payload = {
    "schema_version": 1,
    "purpose": (
        "phase15-v3-telegram-auto-approver-"
        "continuous-contract-upgrade-v1"
    ),
    "status": "UPGRADED_VERIFIED",
    "repository_main": sys.argv[3],
    "project_state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
    "source_truth_auto_approver_status": sys.argv[4],
    "approval_contract_git_blob_sha": sys.argv[5],
    "previous_service_pid": int(sys.argv[6]),
    "current_service_pid": int(sys.argv[7]),
    "service_restart_performed": True,
    "service_active_after": True,
    "service_pid_stable_after_seconds": 3,
    "live_auto_approve_runtime_effective_after": True,
    "launchd_live_auto_approve_environment_verified": True,
    "exact_candidate_prompt_auto_click_prepared_verified": True,
    "mutated_candidate_prompt_rejected_verified": True,
    "telegram_session_deleted": False,
    "telegram_credentials_replaced": False,
    "project_state_mutated": False,
    "live_trading_enabled": False,
    "real_order_submitted_by_upgrade": False,
    "recorder_or_executor_mutated": False,
    "observed_at": datetime.now(UTC).isoformat(),
}
encoded = json.dumps(
    payload,
    sort_keys=True,
    indent=2,
    ensure_ascii=True,
    allow_nan=False,
) + "\n"
fd = os.open(
    evidence_path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    0o600,
)
try:
    os.write(fd, encoded.encode("utf-8"))
    os.fsync(fd)
finally:
    os.close(fd)
PY

printf 'PHASE15_TELEGRAM_AUTO_APPROVER_CONTINUOUS_UPGRADE=PASS\n'
printf 'REPOSITORY_MAIN=%s\n' "$HEAD"
printf 'AUTO_APPROVER_SOURCE_TRUTH_STATUS=%s\n' "$AUTO_STATUS"
printf 'APPROVAL_CONTRACT_GIT_BLOB_SHA=%s\n' "$CONTRACT_SHA"
printf 'PREVIOUS_SERVICE_PID=%s\n' "$OLD_PID"
printf 'CURRENT_SERVICE_PID=%s\n' "$NEW_PID"
printf 'SERVICE_ACTIVE_AFTER=true\n'
printf 'AUTO_APPROVE_RUNTIME_EFFECTIVE_AFTER=true\n'
printf 'LAUNCHD_LIVE_AUTO_APPROVE_ENVIRONMENT_VERIFIED=true\n'
printf 'EXACT_CANDIDATE_PROMPT_AUTO_CLICK_PREPARED_VERIFIED=true\n'
printf 'MUTATED_CANDIDATE_PROMPT_REJECTED_VERIFIED=true\n'
printf 'EVIDENCE_PATH=%s\n' "$EVIDENCE"
printf 'PROJECT_STATE_MUTATED=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_ORDER_SUBMITTED_BY_UPGRADE=false\n'
printf 'RECORDER_OR_EXECUTOR_MUTATED=false\n'
