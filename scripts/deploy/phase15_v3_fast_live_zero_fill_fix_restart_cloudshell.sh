#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() {
  printf 'PHASE15_FAST_LIVE_ZERO_FILL_FIX_RESTART=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_ZERO_FILL_FIX_RESTART:-}"
[[ "$ACCEPT" == "I_ACCEPT_DEPLOY_FAST_LIVE_ZERO_FILL_FIX_AND_RESTART_SESSION" ]] ||
  fail "explicit_zero_fill_fix_restart_acceptance_required"

: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"

PROJECT="$BP_FAST_LIVE_GCP_PROJECT"
US_VM="$BP_FAST_LIVE_RECORDER_VM"
US_ZONE="$BP_FAST_LIVE_RECORDER_ZONE"
EXEC_VM="$BP_FAST_LIVE_EXEC_VM"
EXEC_ZONE="$BP_FAST_LIVE_EXEC_ZONE"
EXPECTED_AUTH_ID="phase15-v3-fast-live-auto-continuous-12h-a6525318-20260930"
EXPECTED_OLD_RELEASE="afbf078a1be8bb29bb26ad7b99b2a35f10501473"
EXPECTED_RUNTIME_EXPIRES="2026-09-30T11:58:23.648915+00:00"
EXPECTED_FIX_BLOB="ed1f55975958f23baee65f0c4a334b34009dda78"

for command_name in git gcloud python3; do
  command -v "$command_name" >/dev/null 2>&1 ||
    fail "missing_command:$command_name"
done
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse origin/main)"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"
[[ "$HEAD" != "$EXPECTED_OLD_RELEASE" ]] || fail "zero_fill_fix_not_present_in_release"
[[ "$(git -C "$ROOT" hash-object src/bp_engine/execution/fast_live_prepare.py)" == "$EXPECTED_FIX_BLOB" ]] ||
  fail "zero_fill_fix_blob_mismatch"

TMP_DIR="$(mktemp -d)"
ARCHIVE="$TMP_DIR/fast-live-release.tar.gz"
SESSION_AUTH="$TMP_DIR/session-authorization.json"
cleanup_local() {
  rm -rf "$TMP_DIR"
}
trap cleanup_local EXIT

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo cat /etc/bp-fast-live/authorization.json" \
  >"$SESSION_AUTH" || fail "existing_runtime_authorization_read_failed"

read -r AUTH_ID OLD_RELEASE RUNTIME_EXPIRES RUNTIME_EXPIRED < <(
  python3 - "$SESSION_AUTH" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("schema_version") != 1:
    raise SystemExit("schema_invalid")
if payload.get("purpose") != "phase15-v3-fast-live-v1":
    raise SystemExit("purpose_invalid")
if payload.get("authorized") is not True:
    raise SystemExit("not_authorized")
if payload.get("continuous_session") is not True:
    raise SystemExit("not_continuous")
if payload.get("requires_telegram_approval") is not True:
    raise SystemExit("telegram_not_required")
if payload.get("authorization_mode") != "auto-telegram-continuous-v1":
    raise SystemExit("mode_invalid")
if payload.get("max_network_submission_attempts_per_intent") != 1:
    raise SystemExit("attempt_limit_invalid")
auth_id = str(payload.get("authorization_id") or "")
release_main = str(payload.get("release_main") or "")
expires = datetime.fromisoformat(str(payload.get("expires_at") or "")).astimezone(UTC)
print(
    auth_id,
    release_main,
    expires.isoformat(),
    "true" if datetime.now(UTC) >= expires else "false",
)
PY
) || fail "existing_runtime_authorization_invalid"

[[ "$AUTH_ID" == "$EXPECTED_AUTH_ID" ]] || fail "authorization_id_mismatch"
[[ "$OLD_RELEASE" == "$EXPECTED_OLD_RELEASE" ]] || fail "old_release_mismatch"
[[ "$RUNTIME_EXPIRES" == "$EXPECTED_RUNTIME_EXPIRES" ]] ||
  fail "runtime_expiry_mismatch"
[[ "$RUNTIME_EXPIRED" == "false" ]] || fail "runtime_already_expired"

# Stop admission first. The receiver is then stopped with the execution kill
# switch already engaged. No runtime/session material is removed until the
# zero-activity cleanup helper re-verifies the stopped state.
gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo systemctl stop bp-phase15-fast-live-source.service || exit 19;
             if sudo systemctl is-active --quiet bp-phase15-fast-live-source.service; then exit 20; fi" ||
  fail "source_stop_failed"

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo sh -c 'umask 077; mkdir -p /var/lib/bp-canary/fast-live; echo zero-fill-fix-restart > /var/lib/bp-canary/fast-live/KILL; chmod 0600 /var/lib/bp-canary/fast-live/KILL' || exit 22;
             sudo systemctl stop bp-phase15-fast-live-receiver.service || exit 23;
             if sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service; then exit 21; fi;
             sudo test -f /var/lib/bp-canary/fast-live/KILL" ||
  fail "receiver_stop_or_kill_failed"

PHASE15_ACCEPT_FAST_LIVE_ZERO_ACTIVITY_RESTART="$ACCEPT" \
BP_FAST_LIVE_GCP_PROJECT="$PROJECT" \
BP_FAST_LIVE_RECORDER_VM="$US_VM" \
BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" \
BP_FAST_LIVE_EXEC_VM="$EXEC_VM" \
BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" \
bash "$ROOT/scripts/deploy/phase15_v3_fast_live_cleanup_expired_cloudshell.sh" ||
  fail "zero_activity_restart_cleanup_failed"

python3 "$ROOT/scripts/deploy/phase15_v3_fast_live_build_release.py" \
  --output "$ARCHIVE" >/dev/null ||
  fail "release_build_failed"

BP_FAST_LIVE_RELEASE_ARCHIVE="$ARCHIVE" \
BP_FAST_LIVE_GCP_PROJECT="$PROJECT" \
BP_FAST_LIVE_RECORDER_VM="$US_VM" \
BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" \
BP_FAST_LIVE_EXEC_VM="$EXEC_VM" \
BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" \
bash "$ROOT/scripts/deploy/phase15_v3_fast_live_stage_cloudshell.sh" ||
  fail "fixed_release_stage_failed"

BP_FAST_LIVE_GCP_PROJECT="$PROJECT" \
BP_FAST_LIVE_RECORDER_VM="$US_VM" \
BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" \
BP_FAST_LIVE_EXEC_VM="$EXEC_VM" \
BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" \
bash "$ROOT/scripts/deploy/phase15_v3_fast_live_preflight_cloudshell.sh" ||
  fail "fixed_release_preflight_failed"

BP_FAST_LIVE_GCP_PROJECT="$PROJECT" \
BP_FAST_LIVE_RECORDER_VM="$US_VM" \
BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" \
BP_FAST_LIVE_EXEC_VM="$EXEC_VM" \
BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" \
BP_FAST_LIVE_RUNTIME_EXPIRES_AT="$RUNTIME_EXPIRES" \
PHASE15_ACCEPT_FAST_LIVE_ACTIVATION="I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION" \
bash "$ROOT/scripts/deploy/phase15_v3_fast_live_activate_cloudshell.sh" ||
  fail "fixed_release_activation_failed"

STATUS="$(
  BP_FAST_LIVE_GCP_PROJECT="$PROJECT" \
  BP_FAST_LIVE_RECORDER_VM="$US_VM" \
  BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" \
  BP_FAST_LIVE_EXEC_VM="$EXEC_VM" \
  BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" \
  bash "$ROOT/scripts/deploy/phase15_v3_fast_live_status_cloudshell.sh"
)" || fail "post_restart_status_failed"

printf '%s\n' "$STATUS"
printf 'PHASE15_FAST_LIVE_ZERO_FILL_FIX_RESTART=PASS\n'
printf 'RELEASE_MAIN=%s\n' "$HEAD"
printf 'AUTHORIZATION_ID=%s\n' "$AUTH_ID"
printf 'RUNTIME_EXPIRES_AT=%s\n' "$RUNTIME_EXPIRES"
printf 'ZERO_ACTIVITY_OLD_SESSION_VERIFIED=true\n'
printf 'OLD_SESSION_RUNTIME_REMOVED=true\n'
printf 'FIXED_RELEASE_STAGED=true\n'
printf 'PREFLIGHT_PASSED=true\n'
printf 'SESSION_REACTIVATED=true\n'
printf 'RUNTIME_EXPIRY_EXTENDED=false\n'
printf 'REAL_ORDER_SUBMITTED_BY_RESTART=false\n'
