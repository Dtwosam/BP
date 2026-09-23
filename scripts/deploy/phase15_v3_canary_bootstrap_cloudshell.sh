#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_CANARY_ZONE:-africa-south1-a}"
VM="${PHASE15_CANARY_VM:-bp-v3-canary-exec}"

fail() {
  echo "PHASE15_V3_CANARY_BOOTSTRAP=FAIL" >&2
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

[[ "${PHASE15_ACCEPT_WALLET_SETUP:-no}" == "yes" ]] \
  || fail "wallet_setup_not_explicitly_accepted"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' \
  || fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = state.get("phase_15_v3_live_canary") or {}
assert state.get("source_of_truth_version") == "0.14.180"
assert gate.get("status") == "ENGINEERING_READY_HOST_PASS"
assert gate.get("max_trade_size_usd") == 10
assert gate.get("max_total_exposure_usd") == 10
assert gate.get("max_daily_loss_usd") == 10
assert gate.get("max_consecutive_losses") == 1
assert gate.get("max_accepted_orders") == 1
assert gate.get("live_trading_enabled") is False
assert gate.get("wallet_configuration_status") == "runtime_required_not_source_controlled"
assert gate.get("canary_order_submitted") is False
assert gate.get("automated_real_money_submission") is False
assert gate.get("manual_real_money_submission_required") is True
PY

INSTANCE_STATUS=$(gcloud compute instances describe "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --format='value(status)' 2>/dev/null || true)
[[ "$INSTANCE_STATUS" == "RUNNING" ]] || fail "candidate_vm_not_running"

GEOBLOCK_JSON=$(gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command='python3 - <<'"'"'PY'"'"'
import json
import urllib.request
url="https://polymarket.com/api/geoblock"
with urllib.request.urlopen(url, timeout=10) as response:
    payload=json.loads(response.read().decode("utf-8"))
print(json.dumps({"blocked":payload["blocked"],"country":payload["country"],"region":payload["region"]},sort_keys=True))
PY')
python3 - "$GEOBLOCK_JSON" <<'PY' || fail "candidate_geoblock_not_eligible"
import json
import sys
payload=json.loads(sys.argv[1])
assert payload["blocked"] is False
assert payload["country"] == "ZA"
PY

printf 'Polymarket private key (input hidden; never sent to ChatGPT): ' >&2
IFS= read -r -s PRIVATE_KEY
printf '\n' >&2
[[ -n "$PRIVATE_KEY" ]] || fail "private_key_empty"
printf 'Polymarket wallet/deposit address (optional; press Enter if not used): ' >&2
IFS= read -r WALLET_ADDRESS

TMP_DIR=$(mktemp -d)
cleanup() {
  rm -rf "$TMP_DIR"
  unset PRIVATE_KEY WALLET_ADDRESS
}
trap cleanup EXIT
umask 077
ENV_FILE="$TMP_DIR/live.env"
{
  printf 'POLYMARKET_PRIVATE_KEY=%q\n' "$PRIVATE_KEY"
  if [[ -n "$WALLET_ADDRESS" ]]; then
    printf 'POLYMARKET_WALLET_ADDRESS=%q\n' "$WALLET_ADDRESS"
  fi
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

EXECUTOR_B64=$(base64 -w0 "$ROOT/scripts/deploy/phase15_v3_canary_executor.py")

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s" <<REMOTE
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip >/dev/null
install -d -m 0755 /opt/bp-canary
if [[ ! -x /opt/bp-canary/.venv/bin/python ]]; then
  python3 -m venv /opt/bp-canary/.venv
fi
/opt/bp-canary/.venv/bin/pip install --disable-pip-version-check -q 'polymarket-client==0.7.1'
printf '%s' '$EXECUTOR_B64' | base64 -d > /opt/bp-canary/executor.py
chown root:root /opt/bp-canary/executor.py
chmod 0755 /opt/bp-canary/executor.py
cat > /opt/bp-canary/executor.sh <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
set -a
source /etc/bp-canary/live.env
set +a
exec /opt/bp-canary/.venv/bin/python /opt/bp-canary/executor.py
SH
chown root:root /opt/bp-canary/executor.sh
chmod 0755 /opt/bp-canary/executor.sh
install -d -m 0700 /etc/bp-canary
install -d -m 0700 /var/lib/bp-canary
rm -f /etc/bp-canary/activation.json
printf '%s\n' 'phase15-v3-live-canary bootstrap fail-closed' > /etc/bp-canary/KILL
chown root:root /etc/bp-canary/KILL
chmod 0600 /etc/bp-canary/KILL
REMOTE

REMOTE_UPLOAD="/tmp/bp-canary-live-env-$$.upload"
gcloud compute scp "$ENV_FILE" "$VM:$REMOTE_UPLOAD" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet >/dev/null

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo install -o root -g root -m 0600 '$REMOTE_UPLOAD' /etc/bp-canary/live.env && rm -f '$REMOTE_UPLOAD'"

HEALTH=$(printf '%s' '{"action":"health"}' | \
  gcloud compute ssh "$VM" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --quiet \
    --command='sudo /opt/bp-canary/executor.sh')

python3 - "$HEALTH" <<'PY' || fail "executor_health_failed"
import json
import sys
payload=json.loads(sys.argv[1])
assert payload["status"] == "ok"
assert payload["geoblock"]["blocked"] is False
assert payload["geoblock"]["country"] == "ZA"
assert payload["private_key_configured"] is True
assert payload["sdk_import_ok"] is True
assert payload["activation_valid"] is False
assert payload["kill_switch_engaged"] is True
assert payload["submission_ready"] is False
assert payload["live_order_submitted"] is False
PY

echo "$HEALTH"
echo "TRADING_ORDER_SUBMITTED=false"
echo "KILL_SWITCH_ENGAGED=true"
echo "ACTIVATION_VALID=false"
echo "MAX_TRADE_SIZE_USD=10"
echo "MAX_ACCEPTED_ORDERS=1"
echo "PHASE15_V3_CANARY_BOOTSTRAP=PASS"
