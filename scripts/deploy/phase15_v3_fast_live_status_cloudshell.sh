#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_STATUS=FAIL:%s\n' "$1" >&2
  exit 1
}

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

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"

RECORDER_OUTPUT="$(
  gcloud compute ssh "$US_VM" \
    --project="$PROJECT" --zone="$US_ZONE" --quiet \
    --command="sudo bash -s" <<'REMOTE'
set -euo pipefail

source_active=no
telegram_active=no
systemctl is-active --quiet bp-phase15-fast-live-source.service &&
  source_active=yes || true
systemctl is-active --quiet bp-phase15-canary-telegram-approval.service &&
  telegram_active=yes || true
printf 'RECORDER_SOURCE_ACTIVE=%s\n' "$source_active"
printf 'TELEGRAM_APPROVAL_ACTIVE=%s\n' "$telegram_active"

python3 - <<'PY'
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

runtime_path = Path("/etc/bp-fast-live/authorization.json")
if not runtime_path.is_file():
    print("RUNTIME_AUTHORIZATION_PRESENT=false")
else:
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    expires_raw = str(payload.get("expires_at") or "")
    expires = datetime.fromisoformat(expires_raw).astimezone(UTC)
    print("RUNTIME_AUTHORIZATION_PRESENT=true")
    print(f"RUNTIME_AUTHORIZATION_ID={payload.get('authorization_id') or ''}")
    print(f"RUNTIME_RELEASE_MAIN={payload.get('release_main') or ''}")
    print(f"RUNTIME_EXPIRES_AT={expires.isoformat()}")
    print(
        "RUNTIME_EXPIRED="
        + ("true" if datetime.now(UTC) >= expires else "false")
    )
    print(
        "RUNTIME_CONTINUOUS_SESSION="
        + ("true" if payload.get("continuous_session") is True else "false")
    )

current_run = Path(
    "/var/lib/bp/phase15-fast-live/telegram-prepare/current-run"
)
print(
    "TELEGRAM_CURRENT_RUN_PRESENT="
    + ("true" if current_run.is_file() else "false")
)

root = Path("/var/lib/bp/phase15-fast-live/published")
results_root = root / "results"
settlements_root = root / "settlements"
publications = tuple(sorted(root.glob("*.json"))) if root.is_dir() else ()
results = tuple(sorted(results_root.glob("*.json"))) if results_root.is_dir() else ()
settlements = (
    tuple(sorted(settlements_root.glob("*.json")))
    if settlements_root.is_dir()
    else ()
)
unresolved_results = 0
unresolved_settlements = 0
for receipt in publications:
    if not (results_root / receipt.name).is_file():
        unresolved_results += 1
for result_path in results:
    try:
        receipt = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        unresolved_settlements += 1
        continue
    official = receipt.get("official_recorded")
    needs_settlement = (
        isinstance(official, dict)
        and official.get("settlement_reconciliation_required") is True
    )
    if needs_settlement and not (settlements_root / result_path.name).is_file():
        unresolved_settlements += 1

print(f"LIVE_PUBLICATION_COUNT={len(publications)}")
print(f"LIVE_RESULT_COUNT={len(results)}")
print(f"LIVE_SETTLEMENT_COUNT={len(settlements)}")
print(f"UNRESOLVED_RESULT_COUNT={unresolved_results}")
print(f"UNRESOLVED_SETTLEMENT_COUNT={unresolved_settlements}")
PY
REMOTE
)" || fail "recorder_status_failed"

EXECUTOR_OUTPUT="$(
  gcloud compute ssh "$EXEC_VM" \
    --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
    --command="sudo bash -s" <<'REMOTE'
set -euo pipefail

receiver_active=no
systemctl is-active --quiet bp-phase15-fast-live-receiver.service &&
  receiver_active=yes || true
printf 'EXECUTOR_RECEIVER_ACTIVE=%s\n' "$receiver_active"
if [[ -f /var/lib/bp-canary/fast-live/KILL ]]; then
  printf 'EXECUTOR_KILL_SWITCH_ENGAGED=true\n'
else
  printf 'EXECUTOR_KILL_SWITCH_ENGAGED=false\n'
fi

python3 - <<'PY'
from __future__ import annotations

import json
from pathlib import Path

root = Path("/var/lib/bp-canary/fast-live")
attempts_root = root / "attempts"
approval_root = root / "approval-decisions"

attempt_count = 0
result_count = 0
pending_cancellation = 0
pending_result_publish = 0
if attempts_root.is_dir():
    for intent_root in attempts_root.iterdir():
        if not intent_root.is_dir() or intent_root.is_symlink():
            continue
        if (intent_root / "attempt.json").is_file():
            attempt_count += 1
        result_path = intent_root / "result.json"
        if not result_path.is_file():
            continue
        result_count += 1
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending_result_publish += 1
            continue
        if result.get("cancellation_pending") is True:
            pending_cancellation += 1
        if result.get("recovery_result_publish_pending") is True:
            pending_result_publish += 1

approval_claim_count = 0
approval_result_count = 0
if approval_root.is_dir():
    for decision_root in approval_root.iterdir():
        if not decision_root.is_dir() or decision_root.is_symlink():
            continue
        if (decision_root / "claim.json").is_file():
            approval_claim_count += 1
        if (decision_root / "result.json").is_file():
            approval_result_count += 1

print(f"LIVE_ATTEMPT_COUNT={attempt_count}")
print(f"EXECUTION_RESULT_COUNT={result_count}")
print(f"PENDING_CANCELLATION_COUNT={pending_cancellation}")
print(f"PENDING_RECOVERY_RESULT_PUBLISH_COUNT={pending_result_publish}")
print(f"APPROVAL_DECISION_CLAIM_COUNT={approval_claim_count}")
print(f"APPROVAL_DECISION_RESULT_COUNT={approval_result_count}")
PY

if health="$(printf '%s' '{"action":"health"}' | /opt/bp-canary/executor.sh 2>/dev/null)"; then
  python3 - "$health" <<'PY'
import json
import sys
from decimal import Decimal

try:
    payload = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    print("OFFICIAL_ACCOUNT_HEALTH=invalid")
    raise SystemExit(0)

if payload.get("status") != "ok":
    print("OFFICIAL_ACCOUNT_HEALTH=not_ok")
    raise SystemExit(0)

geo = payload.get("geoblock") or {}
account = payload.get("account") or {}
print("OFFICIAL_ACCOUNT_HEALTH=ok")
print(f"GEO_BLOCKED={str(geo.get('blocked')).lower()}")
print(f"GEO_COUNTRY={geo.get('country') or ''}")
print(f"OFFICIAL_OPEN_ORDERS={int(account.get('open_order_count') or 0)}")
print(
    "OFFICIAL_COLLATERAL_USD="
    + format(Decimal(str(account.get("collateral_balance_usd") or "0")), "f")
)
print(
    "OFFICIAL_ACCOUNT_CLEAN="
    + ("true" if account.get("clean_for_canary") is True else "false")
)
PY
else
  printf 'OFFICIAL_ACCOUNT_HEALTH=unavailable\n'
fi
REMOTE
)" || fail "executor_status_failed"

printf 'PHASE15_FAST_LIVE_STATUS=PASS\n'
printf '%s\n' "$RECORDER_OUTPUT"
printf '%s\n' "$EXECUTOR_OUTPUT"
printf 'MUTATIONS_PERFORMED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
