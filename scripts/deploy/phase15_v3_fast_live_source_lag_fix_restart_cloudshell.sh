#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() {
  printf 'PHASE15_FAST_LIVE_SOURCE_LAG_FIX_RESTART=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_SOURCE_LAG_FIX_RESTART:-}"
[[ "$ACCEPT" == "I_ACCEPT_STAGE_AND_ACTIVATE_SOURCE_LAG_FIX" ]] ||
  fail "explicit_source_lag_fix_restart_acceptance_required"

: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"
: "${BP_FAST_LIVE_RELEASE_ARCHIVE:=$HOME/bp-fast-live-9824a0b16f64b5018739a33634dc5e4dea673be8.tar.gz}"

PROJECT="$BP_FAST_LIVE_GCP_PROJECT"
US_VM="$BP_FAST_LIVE_RECORDER_VM"
US_ZONE="$BP_FAST_LIVE_RECORDER_ZONE"
EXEC_VM="$BP_FAST_LIVE_EXEC_VM"
EXEC_ZONE="$BP_FAST_LIVE_EXEC_ZONE"
ARCHIVE="$BP_FAST_LIVE_RELEASE_ARCHIVE"

EXPECTED_RELEASE="9824a0b16f64b5018739a33634dc5e4dea673be8"
EXPECTED_ARCHIVE_SHA="857e28c5ba47d56697d5936dd844b54db18dc0b8e3c60513358ffc2232ca7de4"
OLD_AUTH_ID="phase15-v3-fast-live-auto-continuous-v2-12h-2302945a-20261001T124442Z"
OLD_RELEASE="2302945a0fd6fe7f04654a6c7915767bdde5ef7d"
OLD_RUNTIME_EXPIRES="2026-10-02T00:44:42+00:00"
NEW_AUTH_ID="phase15-v3-fast-live-auto-continuous-v2-12h-9824a0b1-20261001T201044Z"
NEW_RUNTIME_EXPIRES="2026-10-02T08:10:44+00:00"

for command_name in git gcloud python3 shasum; do
  command -v "$command_name" >/dev/null 2>&1 ||
    fail "missing_command:$command_name"
done

[[ -f "$ARCHIVE" && ! -L "$ARCHIVE" ]] || fail "release_archive_invalid"
ACTUAL_ARCHIVE_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
[[ "$ACTUAL_ARCHIVE_SHA" == "$EXPECTED_ARCHIVE_SHA" ]] ||
  fail "release_archive_sha_mismatch"

read -r ARCHIVE_HEAD MANIFEST_SAFE < <(
  python3 - "$ARCHIVE" <<'PY'
import json
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
with tarfile.open(archive, "r:gz") as tf:
    manifest = json.load(tf.extractfile("RELEASE-MANIFEST.json"))
    source = tf.extractfile(
        "src/bp_engine/execution/fast_live_prepare.py"
    ).read().decode("utf-8")
assert manifest["purpose"] == "phase15-v3-fast-live-release-v1"
assert manifest["contains_project_state"] is False
assert manifest["contains_authorization"] is False
assert manifest["contains_secret_files"] is False
assert manifest["production_mutation_performed"] is False
assert manifest["real_order_submitted"] is False
assert 'FAST_LIVE_MAX_POLYMARKET_SOURCE_AGE_SECONDS = Decimal("2")' in source
assert 'reason = "polymarket_source_lag"' in source
assert "condition_id=request.condition_id" in source
assert "token_id=request.token_id" in source
print(manifest["commit_sha"], "true")
PY
) || fail "release_archive_manifest_invalid"
[[ "$ARCHIVE_HEAD" == "$EXPECTED_RELEASE" ]] || fail "release_archive_head_mismatch"
[[ "$MANIFEST_SAFE" == "true" ]] || fail "release_archive_safety_invalid"

[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"
git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse origin/main)"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"
git -C "$ROOT" merge-base --is-ancestor "$EXPECTED_RELEASE" "$HEAD" ||
  fail "corrected_release_not_in_current_main_history"

if ! PYTHONPATH="$ROOT/src" python3 - "$ROOT/PROJECT_STATE.json" \
  "$EXPECTED_RELEASE" "$NEW_AUTH_ID" "$NEW_RUNTIME_EXPIRES" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.fast_live import verify_source_authorization

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_release, expected_auth_id, expected_expiry = sys.argv[2:]
auth = state["phase_15_v3_live_canary"]["fast_live_preauthorization"]
assert state["source_of_truth_version"] == "0.14.186"
assert auth["authorization_id"] == expected_auth_id
assert auth["authorization_mode"] == "auto-telegram-continuous-v2"
assert auth["authorized_at_main"] == expected_release
assert auth["expires_at"] == expected_expiry
assert auth["max_consecutive_losses"] == 0
assert auth["target_notional_usd"] == 5
assert auth["max_trade_size_usd"] == 10
assert auth["max_total_exposure_usd"] == 10
assert auth["max_daily_loss_usd"] == 10
assert auth["min_edge"] == 0.075
assert auth["max_transit_seconds"] == 2
assert auth["requires_telegram_approval"] is True
assert auth["max_network_submission_attempts_per_intent"] == 1
assert auth["prediction_version"] == "v3-frozen-paper-v1"
assert auth["execution_version"] == "paper-execution-v3-frozen-v1"
assert auth["executor_country"] == "ZA"
verify_source_authorization(
    state,
    expected_main=expected_release,
    observed_at=datetime.now(UTC),
    requires_telegram_approval=True,
    continuous_session=True,
)
PY
then
  fail "new_source_authorization_invalid"
fi

TMP_DIR="$(mktemp -d)"
cleanup_local() {
  rm -rf "$TMP_DIR"
}
trap cleanup_local EXIT
OLD_RUNTIME="$TMP_DIR/old-authorization.json"

gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="sudo cat /etc/bp-fast-live/authorization.json"   >"$OLD_RUNTIME" || fail "old_runtime_authorization_read_failed"

if ! python3 - "$OLD_RUNTIME" "$OLD_AUTH_ID" "$OLD_RELEASE" "$OLD_RUNTIME_EXPIRES" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_id, expected_release, expected_expiry = sys.argv[2:]
assert payload["schema_version"] == 1
assert payload["purpose"] == "phase15-v3-fast-live-v1"
assert payload["authorized"] is True
assert payload["authorization_id"] == expected_id
assert payload["authorization_mode"] == "auto-telegram-continuous-v2"
assert payload["release_main"] == expected_release
assert payload["expires_at"] == expected_expiry
assert payload["continuous_session"] is True
assert payload["requires_telegram_approval"] is True
assert payload["max_network_submission_attempts_per_intent"] == 1
assert datetime.now(UTC) < datetime.fromisoformat(expected_expiry).astimezone(UTC)
PY
then
  fail "old_runtime_authorization_binding_invalid"
fi

echo "=== STOP OLD SESSION SAFELY ==="
gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="sudo systemctl stop bp-phase15-fast-live-source.service || exit 19;
             if sudo systemctl is-active --quiet bp-phase15-fast-live-source.service; then exit 20; fi" ||
  fail "source_stop_failed"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo sh -c 'umask 077; mkdir -p /var/lib/bp-canary/fast-live; echo source-lag-fix-restart > /var/lib/bp-canary/fast-live/KILL; chmod 0600 /var/lib/bp-canary/fast-live/KILL' || exit 22;
             sudo systemctl stop bp-phase15-fast-live-receiver.service || exit 23;
             if sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service; then exit 21; fi;
             sudo test -f /var/lib/bp-canary/fast-live/KILL" ||
  fail "receiver_stop_or_kill_failed"

echo "=== CLEAN OLD ZERO-ATTEMPT SESSION ==="
PHASE15_ACCEPT_FAST_LIVE_SOURCE_LAG_RESTART="I_ACCEPT_ROTATE_ZERO_ATTEMPT_SOURCE_LAG_SESSION" BP_FAST_LIVE_GCP_PROJECT="$PROJECT" BP_FAST_LIVE_RECORDER_VM="$US_VM" BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" BP_FAST_LIVE_EXEC_VM="$EXEC_VM" BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" bash "$ROOT/scripts/deploy/phase15_v3_fast_live_cleanup_expired_cloudshell.sh" ||
  fail "old_session_cleanup_failed"

echo "=== STAGE CORRECTED RELEASE ==="
BP_FAST_LIVE_RELEASE_ARCHIVE="$ARCHIVE" BP_FAST_LIVE_GCP_PROJECT="$PROJECT" BP_FAST_LIVE_RECORDER_VM="$US_VM" BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" BP_FAST_LIVE_EXEC_VM="$EXEC_VM" BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" bash "$ROOT/scripts/deploy/phase15_v3_fast_live_stage_cloudshell.sh" ||
  fail "corrected_release_stage_failed"

echo "=== READ-ONLY PREFLIGHT ==="
BP_FAST_LIVE_GCP_PROJECT="$PROJECT" BP_FAST_LIVE_RECORDER_VM="$US_VM" BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" BP_FAST_LIVE_EXEC_VM="$EXEC_VM" BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" bash "$ROOT/scripts/deploy/phase15_v3_fast_live_preflight_cloudshell.sh" ||
  fail "corrected_release_preflight_failed"

echo "=== ACTIVATE CORRECTED SESSION ==="
BP_FAST_LIVE_GCP_PROJECT="$PROJECT" BP_FAST_LIVE_RECORDER_VM="$US_VM" BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE" BP_FAST_LIVE_EXEC_VM="$EXEC_VM" BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE" BP_FAST_LIVE_RUNTIME_EXPIRES_AT="$NEW_RUNTIME_EXPIRES" PHASE15_ACCEPT_FAST_LIVE_ACTIVATION="I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION" bash "$ROOT/scripts/deploy/phase15_v3_fast_live_activate_cloudshell.sh" ||
  fail "corrected_release_activation_failed"

echo "=== POST-ACTIVATION STATUS ==="
STATUS="$(
  BP_FAST_LIVE_GCP_PROJECT="$PROJECT"   BP_FAST_LIVE_RECORDER_VM="$US_VM"   BP_FAST_LIVE_RECORDER_ZONE="$US_ZONE"   BP_FAST_LIVE_EXEC_VM="$EXEC_VM"   BP_FAST_LIVE_EXEC_ZONE="$EXEC_ZONE"   bash "$ROOT/scripts/deploy/phase15_v3_fast_live_status_cloudshell.sh"
)" || fail "post_activation_status_failed"
printf '%s\n' "$STATUS"

printf 'PHASE15_FAST_LIVE_SOURCE_LAG_FIX_RESTART=PASS\n'
printf 'OLD_AUTHORIZATION_ID=%s\n' "$OLD_AUTH_ID"
printf 'OLD_RELEASE_MAIN=%s\n' "$OLD_RELEASE"
printf 'OLD_SESSION_NETWORK_ATTEMPTS=0\n'
printf 'OLD_SESSION_RUNTIME_REMOVED=true\n'
printf 'CORRECTED_RELEASE_MAIN=%s\n' "$EXPECTED_RELEASE"
printf 'CORRECTED_RELEASE_SHA256=%s\n' "$EXPECTED_ARCHIVE_SHA"
printf 'NEW_AUTHORIZATION_ID=%s\n' "$NEW_AUTH_ID"
printf 'NEW_RUNTIME_EXPIRES_AT=%s\n' "$NEW_RUNTIME_EXPIRES"
printf 'CORRECTED_RELEASE_STAGED=true\n'
printf 'PREFLIGHT_PASSED=true\n'
printf 'SESSION_ACTIVATED=true\n'
printf 'MANUAL_ORDER_SUBMISSION_PERFORMED=false\n'
printf 'DIRECT_ORDER_COMMAND_EXECUTED=false\n'
