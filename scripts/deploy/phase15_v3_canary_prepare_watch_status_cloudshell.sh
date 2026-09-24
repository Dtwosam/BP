#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
PREPARED_FILE="${PHASE15_CANARY_PREPARED_FILE:-/tmp/bp-phase15-v3-canary-prepared.json}"

fail() {
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"

REMOTE=$(gcloud compute ssh "$US_VM" \
  --project="$PROJECT" \
  --zone="$US_ZONE" \
  --quiet \
  --command='sudo bash -s' <<'REMOTE'
set -Eeuo pipefail
STATE_ROOT=/var/lib/bp/phase15-canary-prepare-watch
CURRENT_RUN=$STATE_ROOT/current-run
SERVICE=bp-phase15-canary-prepare-watch.service
[[ -r "$CURRENT_RUN" ]] || { echo '{"error":"current_run_missing"}'; exit 0; }
RUN_DIR=$(cat "$CURRENT_RUN")
[[ "$RUN_DIR" == "$STATE_ROOT"/runs/* ]] || { echo '{"error":"current_run_invalid"}'; exit 0; }
[[ -r "$RUN_DIR/status.json" ]] || { echo '{"error":"status_missing"}'; exit 0; }
ACTIVE=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
STATUS=$(cat "$RUN_DIR/status.json")
PREPARED_B64=''
if [[ -r "$RUN_DIR/prepared.json" ]]; then
  PREPARED_B64=$(base64 -w0 "$RUN_DIR/prepared.json")
fi
/opt/bp/.venv/bin/python - "$ACTIVE" "$RUN_DIR" "$STATUS" "$PREPARED_B64" <<'PY'
import json
import sys
active, run_dir, status_raw, prepared_b64 = sys.argv[1:]
payload={
    "service_active": active,
    "run_dir": run_dir,
    "status": json.loads(status_raw),
    "prepared_b64": prepared_b64 or None,
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
REMOTE
) || fail "remote_status_command_failed"

python3 - "$REMOTE" <<'PY' || fail "remote_status_invalid"
import json
import sys
payload=json.loads(sys.argv[1])
if payload.get("error"):
    raise SystemExit(payload["error"])
status=payload.get("status")
if not isinstance(status, dict):
    raise SystemExit("status missing")
print(json.dumps(
    {
        "service_active": payload.get("service_active"),
        "run_dir": payload.get("run_dir"),
        **status,
    },
    indent=2,
    sort_keys=True,
))
PY

STATUS=$(python3 - "$REMOTE" <<'PY'
import json,sys
print(json.loads(sys.argv[1])["status"]["status"])
PY
)

if [[ "$STATUS" == "running" ]]; then
  echo "PREPARE_WATCH_ACTIVE=true"
  echo "NO_REAL_ORDER_SUBMITTED=true"
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=RUNNING"
  exit 0
fi

if [[ "$STATUS" == "prepared_intent_persisted" ]]; then
  ACTIVE=$(python3 - "$REMOTE" <<'PY'
import json,sys
print(json.loads(sys.argv[1])["service_active"])
PY
)
  if [[ "$ACTIVE" == "active" ]]; then
    echo "PREPARED_PAYLOAD_WRITE_IN_PROGRESS=true"
    echo "Run this status helper again immediately."
    exit 0
  fi
  echo "ARMABLE_NOW=false"
  echo "REQUIRES_CLOSED_BEFORE_SUBMISSION_RECONCILIATION=true"
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=FAILED_AFTER_INTENT"
  exit 1
fi

if [[ "$STATUS" == "failed_after_intent_persisted" ]]; then
  echo "ARMABLE_NOW=false"
  echo "REQUIRES_CLOSED_BEFORE_SUBMISSION_RECONCILIATION=true"
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=FAILED_AFTER_INTENT"
  exit 1
fi

if [[ "$STATUS" != "prepared" ]]; then
  echo "ARMABLE_NOW=false"
  echo "NO_REAL_ORDER_SUBMITTED=true"
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=$STATUS"
  exit 0
fi

read -r HELPER_HEAD INTENT_ID MARKET_END_AT < <(
python3 - "$REMOTE" <<'PY'
import json,sys
status=json.loads(sys.argv[1])["status"]
print(status["helper_head"], status["intent_id"], status["market_end_at"])
PY
)

if [[ "$HELPER_HEAD" != "$LOCAL_HEAD" ]]; then
  echo "ARMABLE_NOW=false"
  echo "REASON=watcher_helper_head_not_current_main"
  echo "REQUIRES_CLOSED_BEFORE_SUBMISSION_RECONCILIATION=true"
  exit 1
fi

REMAINING=$(python3 - "$MARKET_END_AT" <<'PY'
import sys
from datetime import UTC, datetime
end=datetime.fromisoformat(sys.argv[1]).astimezone(UTC)
print((end-datetime.now(UTC)).total_seconds())
PY
)
if ! python3 - "$REMAINING" <<'PY'
import sys
assert float(sys.argv[1]) >= 20
PY
then
  echo "ARMABLE_NOW=false"
  echo "INTENT_ID=$INTENT_ID"
  echo "SECONDS_TO_MARKET_END=$REMAINING"
  echo "REQUIRES_CLOSED_BEFORE_SUBMISSION_RECONCILIATION=true"
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=PREPARED_BUT_STALE"
  exit 1
fi

PREPARED_B64=$(python3 - "$REMOTE" <<'PY'
import json,sys
value=json.loads(sys.argv[1]).get("prepared_b64")
if not value:
    raise SystemExit("prepared payload missing")
print(value)
PY
) || fail "prepared_payload_missing"

umask 077
printf '%s' "$PREPARED_B64" | base64 -d > "$PREPARED_FILE"
chmod 0600 "$PREPARED_FILE"

python3 - "$PREPARED_FILE" "$INTENT_ID" <<'PY' || fail "prepared_payload_binding_invalid"
import json
import sys
from decimal import Decimal
from pathlib import Path
payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["status"] == "prepared"
assert payload["action"] == "submit"
assert payload["intent_id"] == sys.argv[2]
assert payload["policy"]["policy_version"] == "v3-live-canary-v1"
assert Decimal(str(payload["request"]["target_notional_usd"])) == Decimal("5")
assert int(payload["policy"]["max_submission_attempts"]) == 1
PY

echo "PREPARED_FILE=$PREPARED_FILE"
echo "INTENT_ID=$INTENT_ID"
echo "SECONDS_TO_MARKET_END=$REMAINING"
echo "ARMABLE_NOW=true"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_STATUS=PASS"
