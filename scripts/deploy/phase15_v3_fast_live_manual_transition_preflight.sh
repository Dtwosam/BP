#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_MANUAL_TRANSITION_PREFLIGHT=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
[[ -f "$ROOT/PROJECT_STATE.json" && ! -L "$ROOT/PROJECT_STATE.json" ]] ||
  fail "project_state_invalid"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse FETCH_HEAD)"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTO_STATUS GLOBAL_LIVE PHASE_LIVE FAST_AUTH_PRESENT < <(
  python3 - "$ROOT/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
phase = state.get("phase_15_v3_live_canary")
if not isinstance(phase, dict):
    raise SystemExit("phase15_missing")
auto = phase.get("operator_telegram_auto_approver")
if not isinstance(auto, dict):
    raise SystemExit("auto_approver_source_truth_missing")
status = str(auto.get("status") or "")
if not status.startswith("ACTIVE_"):
    raise SystemExit("auto_approver_source_truth_not_active")
if auto.get("live_auto_approve_authorized") is not True:
    raise SystemExit("auto_approver_source_truth_not_authorized")
if state.get("live_trading_enabled") is not False:
    raise SystemExit("global_live_flag_not_false")
if phase.get("live_trading_enabled") is not False:
    raise SystemExit("phase_live_flag_not_false")
if phase.get("fast_live_preauthorization") is not None:
    raise SystemExit("continuous_fast_live_authorization_already_present")
print(status, "false", "false", "false")
PY
) || fail "source_truth_not_in_pretransition_state"

PLATFORM="$(uname -s)"
SERVICE_MANAGER=""
SERVICE_NAME=""
SERVICE_ACTIVE=false
SERVICE_ENABLED_OR_LOADED=false

case "$PLATFORM" in
  Darwin)
    command -v launchctl >/dev/null 2>&1 || fail "launchctl_missing"
    SERVICE_MANAGER="launchd"
    SERVICE_NAME="com.bp.telegram-auto-approver"
    DOMAIN="gui/$(id -u)"
    SERVICE_ENABLED_OR_LOADED=true
    if launchctl print "$DOMAIN/$SERVICE_NAME" >/dev/null 2>&1; then
      SERVICE_ACTIVE=true
    fi
    if launchctl print-disabled "$DOMAIN" |
      grep -F -q "\"$SERVICE_NAME\" => true"; then
      SERVICE_ENABLED_OR_LOADED=false
    fi
    ;;
  Linux)
    command -v systemctl >/dev/null 2>&1 || fail "systemctl_missing"
    SERVICE_MANAGER="systemd-user"
    SERVICE_NAME="bp-telegram-auto-approver.service"
    systemctl --user is-active --quiet "$SERVICE_NAME" &&
      SERVICE_ACTIVE=true || true
    systemctl --user is-enabled --quiet "$SERVICE_NAME" &&
      SERVICE_ENABLED_OR_LOADED=true || true
    ;;
  *)
    fail "unsupported_operator_platform"
    ;;
esac

PROCESS_PRESENT=false
if pgrep -f '[b]p_telegram_auto_approver' >/dev/null 2>&1; then
  PROCESS_PRESENT=true
fi

printf 'PHASE15_FAST_LIVE_MANUAL_TRANSITION_PREFLIGHT=PASS\n'
printf 'REPOSITORY_MAIN=%s\n' "$HEAD"
printf 'AUTO_APPROVER_SOURCE_TRUTH_STATUS=%s\n' "$AUTO_STATUS"
printf 'GLOBAL_LIVE_TRADING_ENABLED=%s\n' "$GLOBAL_LIVE"
printf 'PHASE_LIVE_TRADING_ENABLED=%s\n' "$PHASE_LIVE"
printf 'FAST_LIVE_PREAUTHORIZATION_PRESENT=%s\n' "$FAST_AUTH_PRESENT"
printf 'OPERATOR_PLATFORM=%s\n' "$PLATFORM"
printf 'SERVICE_MANAGER=%s\n' "$SERVICE_MANAGER"
printf 'SERVICE_NAME=%s\n' "$SERVICE_NAME"
printf 'SERVICE_ACTIVE=%s\n' "$SERVICE_ACTIVE"
printf 'SERVICE_ENABLED_OR_LOADED=%s\n' "$SERVICE_ENABLED_OR_LOADED"
printf 'MATCHING_PROCESS_PRESENT=%s\n' "$PROCESS_PRESENT"
printf 'MUTATIONS_PERFORMED=false\n'
printf 'SOURCE_TRUTH_MUTATED=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
