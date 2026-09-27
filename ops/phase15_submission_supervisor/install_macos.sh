#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ACCEPT="${PHASE15_ACCEPT_CONTROLLED_SUBMISSION_SUPERVISOR:-}"
[[ "$ACCEPT" == "yes" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=explicit_supervisor_install_authorization_required" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=local_repository_missing" >&2
  exit 1
}
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=local_working_tree_dirty" >&2
  exit 1
}

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=local_main_not_current" >&2
  exit 1
}

GCLOUD=$(command -v gcloud || true)
[[ -n "$GCLOUD" && -x "$GCLOUD" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=gcloud_missing" >&2
  exit 1
}
"$GCLOUD" auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=gcloud_auth_missing" >&2
  exit 1
}
PYTHON=$(command -v python3)
[[ -n "$PYTHON" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=python3_missing" >&2
  exit 1
}

AUTO_ENV="$HOME/.config/bp/telegram-auto-approver.env"
[[ -r "$AUTO_ENV" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=auto_approver_env_missing" >&2
  exit 1
}
grep -qx 'BP_TELEGRAM_AUTO_APPROVE=true' "$AUTO_ENV" || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=auto_approver_not_live_enabled" >&2
  exit 1
}
launchctl print "gui/$(id -u)/com.bp.telegram-auto-approver" >/dev/null 2>&1 || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=auto_approver_service_not_running" >&2
  exit 1
}

INSTALLER_BLOB=$(git hash-object "$ROOT/ops/phase15_submission_supervisor/install_macos.sh")

python3 - "$ROOT/PROJECT_STATE.json" "$INSTALLER_BLOB" <<'PY' || {
import json
import sys
from pathlib import Path

state=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate=state["phase_15_v3_live_canary"]
second=gate["second_live_canary_authorization"]
controlled=gate["controlled_auto_approved_canary_authorization"]
auto=gate["operator_telegram_auto_approver"]
supervisor=gate["controlled_submission_supervisor"]

assert state["live_trading_enabled"] is False
assert gate["live_trading_enabled"] is False
assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
assert second["max_network_submission_attempts"] == 1
assert controlled["consumed"] is False
assert auto["status"] == "ACTIVE_LIVE_AUTO_APPROVE"
assert auto["mode"] == "live-auto-approve"
assert supervisor["authorized"] is True
assert supervisor["completed"] is False
assert supervisor["target_notional_usd"] == 5
assert supervisor["max_network_submission_attempts"] == 1
assert supervisor["installer_git_blob_sha"] == sys.argv[2]
if supervisor["status"] == "AUTHORIZED_NOT_DEPLOYED":
    assert supervisor["deployment_performed"] is False
    assert supervisor["activation_performed"] is False
else:
    assert supervisor["status"] == "ACTIVE_WAITING_FOR_REAL_SUBMISSION"
    assert supervisor["deployment_performed"] is True
    assert supervisor["activation_performed"] is True
    assert supervisor["runtime_health_status"] == "DEGRADED_GCLOUD_NOT_FOUND"
    assert supervisor["runtime_repair_authorized"] is True
    assert supervisor["runtime_repair_completed"] is False
    assert supervisor["runtime_issue"] == "launchagent_gcloud_not_found"
PY
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=source_truth_supervisor_authorization_invalid" >&2
  exit 1
}

BASE="$HOME/.local/share/bp-phase15-submission-supervisor"
MANAGED_REPO="$BASE/repo"
STATE_ROOT="$HOME/.local/state/bp-phase15-submission-supervisor"
LOG_ROOT="$STATE_ROOT/logs"
PLIST="$HOME/Library/LaunchAgents/com.bp.phase15-submission-supervisor.plist"
ORIGIN=$(git remote get-url origin)

mkdir -p "$BASE" "$STATE_ROOT" "$LOG_ROOT" "$HOME/Library/LaunchAgents"
chmod 700 "$BASE" "$STATE_ROOT" "$LOG_ROOT"

if [[ ! -d "$MANAGED_REPO/.git" ]]; then
  rm -rf "$MANAGED_REPO"
  git clone --quiet "$ORIGIN" "$MANAGED_REPO"
fi
[[ -z "$(git -C "$MANAGED_REPO" status --porcelain --untracked-files=all)" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=managed_repository_dirty" >&2
  exit 1
}

git -C "$MANAGED_REPO" fetch --quiet origin refs/heads/main
git -C "$MANAGED_REPO" switch --detach --quiet FETCH_HEAD
MANAGED_HEAD=$(git -C "$MANAGED_REPO" rev-parse HEAD)
[[ "$MANAGED_HEAD" == "$REMOTE_MAIN" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=managed_repository_not_current_main" >&2
  exit 1
}

SUPERVISOR="$MANAGED_REPO/ops/phase15_submission_supervisor/run.py"
[[ -r "$SUPERVISOR" ]] || {
  echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=FAIL" >&2
  echo "REASON=supervisor_script_missing" >&2
  exit 1
}

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.bp.phase15-submission-supervisor</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SUPERVISOR</string>
    <string>--repo</string>
    <string>$MANAGED_REPO</string>
    <string>--state-root</string>
    <string>$STATE_ROOT</string>
    <string>--gcloud-bin</string>
    <string>$GCLOUD</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$MANAGED_REPO</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>$LOG_ROOT/stdout.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_ROOT/stderr.log</string>
</dict>
</plist>
EOF
chmod 600 "$PLIST"

launchctl bootout "gui/$(id -u)/com.bp.phase15-submission-supervisor" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.bp.phase15-submission-supervisor"

sleep 2
launchctl print "gui/$(id -u)/com.bp.phase15-submission-supervisor" >/dev/null

echo "MANAGED_REPO=$MANAGED_REPO"
echo "STATE_ROOT=$STATE_ROOT"
echo "SUPERVISOR_MAIN=$MANAGED_HEAD"
echo "GCLOUD_BIN=$GCLOUD"
echo "AUTO_APPROVER_REQUIRED=true"
echo "MAX_NETWORK_SUBMISSION_ATTEMPTS=1"
echo "TARGET_NOTIONAL_USD=5"
echo "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=PASS"
