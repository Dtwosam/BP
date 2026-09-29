#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_PREFLIGHT=FAIL:%s\n' "$1" >&2
  exit 1
}

: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="$ROOT/PROJECT_STATE.json"
PROJECT="$BP_FAST_LIVE_GCP_PROJECT"
US_VM="$BP_FAST_LIVE_RECORDER_VM"
US_ZONE="$BP_FAST_LIVE_RECORDER_ZONE"
EXEC_VM="$BP_FAST_LIVE_EXEC_VM"
EXEC_ZONE="$BP_FAST_LIVE_EXEC_ZONE"

command -v git >/dev/null 2>&1 || fail "git_missing"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
[[ -f "$STATE" && ! -L "$STATE" ]] || fail "project_state_invalid"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] || fail "working_tree_not_clean"

HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTH_ID AUTH_EXPIRES < <(
  PYTHONPATH="$ROOT/src" python3 - "$STATE" "$HEAD" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.fast_live import verify_source_authorization

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
auth = verify_source_authorization(
    state,
    expected_main=sys.argv[2],
    observed_at=datetime.now(UTC),
    requires_telegram_approval=True,
    continuous_session=True,
)
print(str(auth["authorization_id"]), str(auth["expires_at"]))
PY
) || fail "source_truth_fast_live_authorization_invalid"

[[ -n "$AUTH_ID" ]] || fail "authorization_id_missing"
[[ -n "$AUTH_EXPIRES" ]] || fail "authorization_expiry_missing"

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo test \"\$(readlink -f /opt/bp-fast-live/current)\" = '/opt/bp-fast-live/releases/$HEAD' &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-source.service && exit 20 || true;
             sudo systemctl is-enabled --quiet bp-phase15-fast-live-source.service && exit 21 || true;
             sudo test ! -e /etc/bp-fast-live/transport.key &&
             sudo test -d '/opt/bp-phase15-telegram-approval/releases/$HEAD' &&
             sudo test -f '/opt/bp-phase15-telegram-approval/releases/$HEAD/scripts/run_phase15_v3_canary_telegram_approval.py' &&
             sudo test -f '/opt/bp-phase15-telegram-approval/releases/$HEAD/deploy/bp-phase15-canary-telegram-approval.service' &&
             sudo test -f /etc/bp/telegram-approval.env &&
             sudo test ! -e /etc/bp/telegram-approval-handoff.env &&
             sudo test ! -e /var/lib/bp/phase15-fast-live/telegram-prepare/current-run &&
             sudo test ! -e /var/lib/bp/phase15-fast-live/RESULT_INTEGRITY_FAULT.json &&
             sudo systemctl is-enabled --quiet bp-phase15-canary-telegram-approval.service" ||
  fail "recorder_stage_not_ready"

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo sh -c 'root=/var/lib/bp/phase15-fast-live/published;
    if [ -d \"\$root\" ]; then
      for receipt in \"\$root\"/*.json; do
        [ -e \"\$receipt\" ] || continue;
        base=\${receipt##*/};
        [ -f \"\$root/results/\$base\" ] || exit 24;
      done;
      if [ -d \"\$root/results\" ]; then
        for result in \"\$root/results\"/*.json; do
          [ -e \"\$result\" ] || continue;
          if grep -F -q \"\\\"settlement_reconciliation_required\\\":true\" \"\$result\"; then
            base=\${result##*/};
            [ -f \"\$root/settlements/\$base\" ] || exit 25;
          fi;
        done;
      fi;
    fi'" ||
  fail "recorder_prior_live_recovery_pending"

gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo test \"\$(readlink -f /opt/bp-fast-live/current)\" = '/opt/bp-fast-live/releases/$HEAD' &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service && exit 22 || true;
             sudo systemctl is-enabled --quiet bp-phase15-fast-live-receiver.service && exit 23 || true;
             sudo test ! -e /etc/bp-fast-live/transport.key;
             sudo test -f /var/lib/bp-canary/fast-live/KILL;
             sudo test ! -e /var/lib/bp-canary/fast-live/attempt.json;
             sudo test ! -e /var/lib/bp-canary/fast-live/result.json;
             sudo sh -c '! grep -R -F -q cancellation_pending\":true /var/lib/bp-canary/fast-live/attempts 2>/dev/null && ! grep -R -F -q recovery_result_publish_pending\":true /var/lib/bp-canary/fast-live/attempts 2>/dev/null'" ||
  fail "executor_stage_not_ready"

HEALTH="$(mktemp)"
cleanup() { rm -f "$HEALTH"; }
trap cleanup EXIT
gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh" \
  >"$HEALTH" || fail "executor_health_failed"

read -r OPEN_ORDERS COLLATERAL < <(
  python3 - "$HEALTH" <<'PY'
import json
import sys
from decimal import Decimal
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("health_not_ok")
geo = payload.get("geoblock") or {}
if geo.get("blocked") is not False or geo.get("country") != "ZA":
    raise SystemExit("geoblock_not_allowed")
account = payload.get("account") or {}
if account.get("clean_for_canary") is not True:
    raise SystemExit("account_not_clean")
open_orders = int(account.get("open_order_count", -1))
collateral = Decimal(str(account.get("collateral_balance_usd", "0")))
if open_orders != 0:
    raise SystemExit("open_orders_present")
if collateral < Decimal("5"):
    raise SystemExit("insufficient_collateral")
print(open_orders, format(collateral, "f"))
PY
) || fail "executor_health_not_safe"

printf 'PHASE15_FAST_LIVE_PREFLIGHT=PASS\n'
printf 'RELEASE_MAIN=%s\n' "$HEAD"
printf 'AUTHORIZATION_ID=%s\n' "$AUTH_ID"
printf 'AUTHORIZATION_EXPIRES_AT=%s\n' "$AUTH_EXPIRES"
printf 'RECORDER_STAGE_READY=true\n'
printf 'TELEGRAM_APPROVAL_STAGE_READY=true\n'
printf 'EXECUTOR_STAGE_READY=true\n'
printf 'EXECUTOR_KILL_SWITCH_ENGAGED=true\n'
printf 'OFFICIAL_OPEN_ORDERS=%s\n' "$OPEN_ORDERS"
printf 'COLLATERAL_BALANCE_USD=%s\n' "$COLLATERAL"
printf 'MUTATIONS_PERFORMED=false\n'
printf 'PUBSUB_RESOURCES_MUTATED=false\n'
printf 'RUNTIME_AUTHORIZATION_CREATED=false\n'
printf 'SERVICES_STARTED=false\n'
printf 'KILL_SWITCH_REMOVED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
