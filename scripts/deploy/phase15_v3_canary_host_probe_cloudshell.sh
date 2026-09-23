#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"
MACHINE_TYPE="${PHASE15_CANARY_MACHINE_TYPE:-e2-micro}"
GEOBLOCK_URL="https://polymarket.com/api/geoblock"

fail() {
  echo "PHASE15_V3_CANARY_HOST_PROBE=FAIL" >&2
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
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . \
  || fail "gcloud_auth_missing"

[[ "${PHASE15_ACCEPT_BILLABLE_VM:-no}" == "yes" ]] \
  || fail "billable_vm_not_explicitly_accepted"

python3 - "$ROOT/PROJECT_STATE.json" "$ZONE" "$VM" <<'PY' \
  || fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
zone = sys.argv[2]
vm = sys.argv[3]
gate = state.get("phase_15_v3_canary_readiness") or {}
candidate = gate.get("execution_host_candidate") or {}

assert state.get("source_of_truth_version") == "0.14.179"
assert gate.get("status") == (
    "PRODUCTION_READ_ONLY_PASS_STATISTICAL_GATES_PASS_EXECUTION_HOST_BLOCKED"
)
assert gate.get("phase15_candidate") is True
assert gate.get("live_trading_enabled") is False
assert gate.get("max_trade_size_usd") == 0
assert gate.get("max_daily_loss_usd") == 0
assert gate.get("real_money_mutation_performed") is False
assert candidate.get("zone") == zone
assert candidate.get("machine_type") == "e2-micro"
assert vm == "bp-v3-canary-exec"
PY

if gcloud compute instances describe "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --format='value(name)' >/dev/null 2>&1
then
  fail "candidate_instance_already_exists"
fi

gcloud compute networks describe default --project="$PROJECT" --format='value(name)' \
  >/dev/null 2>&1 || fail "default_network_missing"

CREATED=false
KEEP_CANDIDATE=false
cleanup_candidate() {
  status=$?
  if [[ "$status" -ne 0 && "$CREATED" == "true" && "$KEEP_CANDIDATE" != "true" ]]; then
    gcloud compute instances delete "$VM" \
      --project="$PROJECT" \
      --zone="$ZONE" \
      --quiet >/dev/null 2>&1 || true
  fi
}
trap cleanup_candidate EXIT

echo "Creating execution-only geoblock probe VM in $ZONE."
echo "This is a billable GCP resource. No trading software or wallet material will be installed."

gcloud compute instances create "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --network=default \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=10GB \
  --boot-disk-type=pd-standard \
  --no-service-account \
  --labels=bp-role=v3-canary-probe,bp-phase=15 \
  --quiet >/dev/null
CREATED=true

REMOTE_PY=$(cat <<'PY'
import json
import urllib.request

url = "https://polymarket.com/api/geoblock"
request = urllib.request.Request(
    url,
    headers={"User-Agent": "BP-phase15-geoblock-probe/1"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected_status:{response.status}")
    payload = json.loads(response.read().decode("utf-8"))

if type(payload.get("blocked")) is not bool:
    raise SystemExit("invalid_blocked")
if not isinstance(payload.get("country"), str):
    raise SystemExit("invalid_country")
if not isinstance(payload.get("region"), str):
    raise SystemExit("invalid_region")

print(json.dumps({
    "blocked": payload["blocked"],
    "country": payload["country"],
    "region": payload["region"],
    "direct_url": url,
}, sort_keys=True))
PY
)

GEOBLOCK_JSON=""
for attempt in 1 2 3 4 5; do
  set +e
  GEOBLOCK_JSON=$(printf '%s' "$REMOTE_PY" | \
    gcloud compute ssh "$VM" \
      --project="$PROJECT" \
      --zone="$ZONE" \
      --quiet \
      --command='python3 -' 2>/dev/null)
  rc=$?
  set -e
  if [[ $rc -eq 0 && -n "$GEOBLOCK_JSON" ]]; then
    break
  fi
  sleep 5
done

[[ -n "$GEOBLOCK_JSON" ]] || fail "geoblock_probe_unreachable"

PROBE_RESULT=$(python3 - "$GEOBLOCK_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert type(payload["blocked"]) is bool
assert isinstance(payload["country"], str) and payload["country"]
assert isinstance(payload["region"], str)
print("PASS" if payload["blocked"] is False else "BLOCKED")
PY
)

if [[ "$PROBE_RESULT" != "PASS" ]]; then
  echo "$GEOBLOCK_JSON"
  gcloud compute instances delete "$VM" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --quiet >/dev/null
  CREATED=false
  fail "candidate_execution_host_geoblocked"
fi

KEEP_CANDIDATE=true
trap - EXIT

echo "$GEOBLOCK_JSON"
echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "MACHINE_TYPE=$MACHINE_TYPE"
echo "TRADING_SOFTWARE_INSTALLED=false"
echo "WALLET_OR_SIGNING_MATERIAL_PRESENT=false"
echo "LIVE_TRADING_ENABLED=false"
echo "Candidate VM is intentionally left running for the next separately gated canary-deployment step."
echo "PHASE15_V3_CANARY_HOST_PROBE=PASS"
