#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_TELEGRAM_AUTO_APPROVER_DEACTIVATE=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCEPT="${PHASE15_ACCEPT_TELEGRAM_AUTO_APPROVER_DEACTIVATION:-}"
[[ "$ACCEPT" == "I_ACCEPT_STOP_LEGACY_TELEGRAM_AUTO_APPROVER" ]] ||
  fail "explicit_auto_approver_deactivation_acceptance_required"

: "${BP_TELEGRAM_AUTO_APPROVER_DEACTIVATION_EVIDENCE:?BP_TELEGRAM_AUTO_APPROVER_DEACTIVATION_EVIDENCE is required}"

EVIDENCE="$BP_TELEGRAM_AUTO_APPROVER_DEACTIVATION_EVIDENCE"
[[ "$EVIDENCE" = /* ]] || fail "evidence_path_must_be_absolute"
[[ ! -e "$EVIDENCE" ]] || fail "evidence_path_already_exists"
mkdir -p "$(dirname "$EVIDENCE")"
chmod 0700 "$(dirname "$EVIDENCE")"
EVIDENCE_DIR="$(cd "$(dirname "$EVIDENCE")" && pwd)"
case "$EVIDENCE_DIR/" in
  "$ROOT/docs/evidence/"*) ;;
  *) fail "evidence_path_must_be_under_repo_docs_evidence" ;;
esac

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
[[ -f "$ROOT/PROJECT_STATE.json" && ! -L "$ROOT/PROJECT_STATE.json" ]] ||
  fail "project_state_invalid"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse origin/main)"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTO_STATUS AUTO_AUTHORIZED < <(
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
authorized = auto.get("live_auto_approve_authorized") is True
if not status.startswith("ACTIVE_") or not authorized:
    raise SystemExit("auto_approver_not_active_in_source_truth")
print(status, "true")
PY
) || fail "auto_approver_source_truth_not_active"

PLATFORM="$(uname -s)"
SERVICE_MANAGER=""
SERVICE_NAME=""
SERVICE_WAS_ACTIVE=false

case "$PLATFORM" in
  Darwin)
    command -v launchctl >/dev/null 2>&1 || fail "launchctl_missing"
    SERVICE_MANAGER="launchd"
    SERVICE_NAME="com.bp.telegram-auto-approver"
    DOMAIN="gui/$(id -u)"
    if launchctl print "$DOMAIN/$SERVICE_NAME" >/dev/null 2>&1; then
      SERVICE_WAS_ACTIVE=true
    fi
    launchctl disable "$DOMAIN/$SERVICE_NAME" ||
      fail "launchd_disable_failed"
    if [[ "$SERVICE_WAS_ACTIVE" == "true" ]]; then
      launchctl bootout "$DOMAIN/$SERVICE_NAME" ||
        fail "launchd_bootout_failed"
    fi
    if launchctl print "$DOMAIN/$SERVICE_NAME" >/dev/null 2>&1; then
      fail "launchd_service_still_loaded"
    fi
    launchctl print-disabled "$DOMAIN" |
      grep -F -q "\"$SERVICE_NAME\" => true" ||
      fail "launchd_service_not_disabled"
    ;;
  Linux)
    command -v systemctl >/dev/null 2>&1 || fail "systemctl_missing"
    SERVICE_MANAGER="systemd-user"
    SERVICE_NAME="bp-telegram-auto-approver.service"
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
      SERVICE_WAS_ACTIVE=true
    fi
    systemctl --user stop "$SERVICE_NAME" || fail "systemd_user_stop_failed"
    systemctl --user disable "$SERVICE_NAME" >/dev/null 2>&1 ||
      fail "systemd_user_disable_failed"
    systemctl --user is-active --quiet "$SERVICE_NAME" &&
      fail "systemd_user_service_still_active"
    systemctl --user is-enabled --quiet "$SERVICE_NAME" &&
      fail "systemd_user_service_still_enabled"
    ;;
  *)
    fail "unsupported_operator_platform"
    ;;
esac

# Verify no operator auto-approver Python process remains. This does not kill an
# unknown process; it fails closed so the operator can investigate it.
if pgrep -f '[b]p_telegram_auto_approver' >/dev/null 2>&1; then
  fail "auto_approver_process_still_running"
fi

python3 - "$ROOT/PROJECT_STATE.json" "$EVIDENCE" "$HEAD" "$PLATFORM"   "$SERVICE_MANAGER" "$SERVICE_NAME" "$SERVICE_WAS_ACTIVE"   "$AUTO_STATUS" "$AUTO_AUTHORIZED" <<'PY'
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

state_path = Path(sys.argv[1])
evidence_path = Path(sys.argv[2])
head = sys.argv[3]
platform = sys.argv[4]
manager = sys.argv[5]
service = sys.argv[6]
was_active = sys.argv[7] == "true"
source_status = sys.argv[8]
source_authorized = sys.argv[9] == "true"

raw = state_path.read_bytes()
payload = {
    "schema_version": 1,
    "purpose": "phase15-v3-telegram-auto-approver-deactivation-v1",
    "status": "DEACTIVATED_VERIFIED",
    "repository_main": head,
    "project_state_sha256": hashlib.sha256(raw).hexdigest(),
    "source_truth_auto_approver_status": source_status,
    "source_truth_live_auto_approve_authorized": source_authorized,
    "operator_platform": platform,
    "service_manager": manager,
    "service_name": service,
    "service_was_active": was_active,
    "service_active_after": False,
    "service_enabled_or_loaded_after": False,
    "matching_process_present_after": False,
    "live_auto_approve_runtime_effective_after": False,
    "source_truth_live_auto_approve_authorized_after": source_authorized,
    "telegram_session_deleted": False,
    "telegram_credentials_mutated": False,
    "project_state_mutated": False,
    "live_trading_enabled": False,
    "real_order_submitted": False,
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

printf 'PHASE15_TELEGRAM_AUTO_APPROVER_DEACTIVATE=PASS\n'
printf 'REPOSITORY_MAIN=%s\n' "$HEAD"
printf 'SERVICE_MANAGER=%s\n' "$SERVICE_MANAGER"
printf 'SERVICE_NAME=%s\n' "$SERVICE_NAME"
printf 'SERVICE_WAS_ACTIVE=%s\n' "$SERVICE_WAS_ACTIVE"
printf 'SERVICE_ACTIVE_AFTER=false\n'
printf 'SERVICE_ENABLED_OR_LOADED_AFTER=false\n'
printf 'MATCHING_PROCESS_PRESENT_AFTER=false\n'
printf 'LIVE_AUTO_APPROVE_RUNTIME_EFFECTIVE_AFTER=false\n'
printf 'SOURCE_TRUTH_MUTATED=false\n'
printf 'EVIDENCE_PATH=%s\n' "$EVIDENCE"
printf 'PROJECT_STATE_MUTATED=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
