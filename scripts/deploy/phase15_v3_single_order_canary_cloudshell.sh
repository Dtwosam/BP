#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"
EXEC_ZONE="${PHASE15_CANARY_EXEC_ZONE:-africa-south1-a}"
EXEC_VM="${PHASE15_CANARY_EXEC_VM:-bp-v3-canary-exec}"
EXPECTED_US_DEPLOYED_HEAD="52b4355d6f077373b873f7a6f42bc37a20ddbc7b"

fail() {
  echo "PHASE15_V3_SINGLE_ORDER_CANARY_DEPLOY=FAIL" >&2
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

[[ "${PHASE15_ACCEPT_REAL_MONEY_CANARY:-no}" == "yes" ]] \
  || fail "real_money_canary_not_explicitly_accepted"
[[ "${PHASE15_CANARY_MAX_LOSS_USD:-}" == "5" ]] \
  || fail "canary_max_loss_acknowledgement_must_equal_5"

python3 - "$ROOT/PROJECT_STATE.json" <<'PY' \
  || fail "source_truth_not_authorized"
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
master = state["phase_14_checkpoint"]["master_live_gate"]
canary = state["phase_15_v3_single_order_canary"]
assert state["source_of_truth_version"] == "0.15.0"
assert state["current_phase"] == 15
assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
assert state["phase_14_checkpoint"]["phase15_permitted"] is True
assert all(value == "pass" for value in master.values())
assert canary["status"] == "ENGINEERING_NOT_DEPLOYED"
assert canary["target_notional_usd"] == "5.00"
assert canary["max_external_submission_attempts"] == 1
assert canary["execution_host_geography"]["blocked"] is False
assert canary["user_geography"]["blocked"] is False
assert canary["deployment_performed"] is False
assert canary["live_order_attempted"] is False
PY

gcloud compute instances describe "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --format='value(name)' >/dev/null \
  || fail "us_production_vm_missing"
gcloud compute instances describe "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --format='value(name)' >/dev/null \
  || fail "execution_vm_missing"

EXEC_GEO=$(gcloud compute ssh "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="python3 - <<'PY'
import json
import urllib.request
u='https://polymarket.com/api/geoblock'
r=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BP-phase15-canary-deploy/1'}),timeout=10)
p=json.loads(r.read().decode())
print(json.dumps({'blocked':p.get('blocked'),'country':p.get('country'),'region':p.get('region')},sort_keys=True))
PY")
python3 - "$EXEC_GEO" <<'PY' || fail "execution_host_geoblock_not_pass"
import json,sys
p=json.loads(sys.argv[1])
assert p == {"blocked": False, "country": "ZA", "region": "GP"}
PY

US_INTERNAL=$(gcloud compute instances describe "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" \
  --format='value(networkInterfaces[0].networkIP)')
EXEC_INTERNAL=$(gcloud compute instances describe "$EXEC_VM" \
  --project="$PROJECT" --zone="$EXEC_ZONE" \
  --format='value(networkInterfaces[0].networkIP)')
[[ -n "$US_INTERNAL" && -n "$EXEC_INTERNAL" ]] || fail "internal_ip_missing"

US_PREFLIGHT=$(gcloud compute ssh "$US_VM" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo bash -s" <<'REMOTE'
set -Eeuo pipefail
[[ -d /opt/bp/.git ]]
[[ "$(git -C /opt/bp rev-parse HEAD)" == "52b4355d6f077373b873f7a6f42bc37a20ddbc7b" ]]
read_env() { awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' /etc/bp/bp.env; }
[[ "$(read_env MODE)" == "research" ]]
[[ "$(read_env LIVE_TRADING_ENABLED)" == "false" ]]
[[ "$(read_env MAX_TRADE_SIZE_USD)" == "0" ]]
[[ "$(read_env MAX_DAILY_LOSS_USD)" == "0" ]]
for unit in bp-postgres.service bp-recorder.service bp-v3-frozen-predictor.service bp-v3-paper-execution.service bp-v4-forward-coverage.timer; do
  systemctl is-active --quiet "$unit"
done
echo "US_PREFLIGHT=PASS"
REMOTE
)
grep -q '^US_PREFLIGHT=PASS$' <<<"$US_PREFLIGHT" || fail "us_preflight_failed"

ARCHIVE=$(mktemp /tmp/bp-v3-canary.XXXXXX.tar.gz)
SECRET_TMP=$(mktemp /tmp/bp-v3-canary-secret.XXXXXX)
MANIFEST_TMP=$(mktemp /tmp/bp-v3-canary-manifest.XXXXXX)
chmod 600 "$SECRET_TMP" "$MANIFEST_TMP"
SUCCESS=false

cleanup_local() {
  rm -f "$ARCHIVE" "$SECRET_TMP" "$MANIFEST_TMP"
}
rollback_remote() {
  if [[ "$SUCCESS" != "true" ]]; then
    gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
      --command='sudo systemctl stop bp-v3-live-canary.service >/dev/null 2>&1 || true; sudo install -d -o bp -g bp -m 0700 /var/lib/bp/live-canary; echo deployment_failed | sudo tee /var/lib/bp/live-canary/KILL >/dev/null; sudo chown bp:bp /var/lib/bp/live-canary/KILL; sudo chmod 600 /var/lib/bp/live-canary/KILL' \
      >/dev/null 2>&1 || true
    gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
      --command='sudo install -d -m 0700 /var/lib/bp/live-canary; echo deployment_failed | sudo tee /var/lib/bp/live-canary/KILL >/dev/null; sudo chmod 600 /var/lib/bp/live-canary/KILL' \
      >/dev/null 2>&1 || true
  fi
}
trap 'rollback_remote; cleanup_local' EXIT

git archive --format=tar HEAD | gzip -9 > "$ARCHIVE"
gcloud compute scp "$ARCHIVE" "$US_VM:/tmp/bp-v3-live-canary.tar.gz" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet
gcloud compute scp "$ARCHIVE" "$EXEC_VM:/tmp/bp-v3-live-canary.tar.gz" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet

gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo bash -s -- '$LOCAL_HEAD'" <<'REMOTE'
set -Eeuo pipefail
HEAD_SHA=$1
RUNTIME="/var/lib/bp/runtime/v3-live-canary-$HEAD_SHA"
install -d -o root -g bp -m 0750 "$RUNTIME"
tar -xzf /tmp/bp-v3-live-canary.tar.gz -C "$RUNTIME"
rm -f /tmp/bp-v3-live-canary.tar.gz
ln -sfn "$RUNTIME" /var/lib/bp/runtime/v3-live-canary-current
install -d -o bp -g bp -m 0700 /var/lib/bp/live-canary
install -d -o bp -g bp -m 0700 /var/lib/bp/live-canary/ssh
echo prepared | tee /var/lib/bp/live-canary/KILL >/dev/null
chown bp:bp /var/lib/bp/live-canary/KILL
chmod 600 /var/lib/bp/live-canary/KILL
if [[ ! -f /var/lib/bp/live-canary/ssh/id_ed25519 ]]; then
  sudo -u bp ssh-keygen -q -t ed25519 -N '' -f /var/lib/bp/live-canary/ssh/id_ed25519
fi
install -m 0644 "$RUNTIME/deploy/bp-v3-live-canary.service" /etc/systemd/system/bp-v3-live-canary.service
systemctl daemon-reload
systemctl stop bp-v3-live-canary.service >/dev/null 2>&1 || true
REMOTE

gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="sudo bash -s -- '$LOCAL_HEAD'" <<'REMOTE'
set -Eeuo pipefail
HEAD_SHA=$1
RELEASE="/opt/bp-canary/releases/$HEAD_SHA"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip ca-certificates >/dev/null
if ! id -u bp-exec >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/bp-exec --shell /bin/bash bp-exec
fi
install -d -o root -g root -m 0755 "$RELEASE"
tar -xzf /tmp/bp-v3-live-canary.tar.gz -C "$RELEASE"
rm -f /tmp/bp-v3-live-canary.tar.gz
install -d -o root -g root -m 0755 /opt/bp-canary
ln -sfn "$RELEASE" /opt/bp-canary/current
if [[ ! -x /opt/bp-canary/venv/bin/python ]]; then
  python3 -m venv /opt/bp-canary/venv
fi
PIP_NO_CACHE_DIR=1 /opt/bp-canary/venv/bin/pip install --disable-pip-version-check \
  'pydantic>=2.9,<3' 'pydantic-settings>=2.5,<3' 'httpx>=0.27,<1' \
  'sqlalchemy>=2.0,<3' 'polymarket-client==0.7.1' >/dev/null
install -m 0755 "$RELEASE/deploy/bp-v3-canary-executor" /usr/local/bin/bp-v3-canary-executor
install -d -o root -g root -m 0755 /etc/bp
install -d -o bp-exec -g bp-exec -m 0700 /var/lib/bp/live-canary
install -d -o bp-exec -g bp-exec -m 0700 /home/bp-exec/.ssh
echo prepared | tee /var/lib/bp/live-canary/KILL >/dev/null
chown bp-exec:bp-exec /var/lib/bp/live-canary/KILL
chmod 600 /var/lib/bp/live-canary/KILL
REMOTE

PUBKEY=$(gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command='sudo cat /var/lib/bp/live-canary/ssh/id_ed25519.pub')
[[ "$PUBKEY" == ssh-ed25519* ]] || fail "canary_ssh_public_key_invalid"
AUTH_LINE=$(printf 'from="%s",restrict,command="/usr/local/bin/bp-v3-canary-executor" %s\n' \
  "$US_INTERNAL" "$PUBKEY")
AUTH_B64=$(printf '%s' "$AUTH_LINE" | base64 -w0)
gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command="printf '%s' '$AUTH_B64' | base64 -d | sudo tee /home/bp-exec/.ssh/authorized_keys >/dev/null && sudo chown bp-exec:bp-exec /home/bp-exec/.ssh/authorized_keys && sudo chmod 600 /home/bp-exec/.ssh/authorized_keys"

gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo -u bp ssh-keyscan -T 10 -H '$EXEC_INTERNAL' | sudo -u bp tee /var/lib/bp/live-canary/ssh/known_hosts >/dev/null && sudo chmod 600 /var/lib/bp/live-canary/ssh/known_hosts"
KNOWN_COUNT=$(gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command='sudo -u bp wc -l < /var/lib/bp/live-canary/ssh/known_hosts')
[[ "$KNOWN_COUNT" -ge 1 ]] || fail "execution_host_key_scan_failed"

printf 'Polymarket private key (not echoed; never paste this into chat): ' >&2
IFS= read -r -s POLYMARKET_PRIVATE_KEY
printf '\n' >&2
[[ "$POLYMARKET_PRIVATE_KEY" =~ ^(0x)?[0-9A-Fa-f]{64}$ ]] \
  || fail "private_key_format_invalid"
printf 'Polymarket wallet/funder address (optional; press Enter to use signer wallet): ' >&2
IFS= read -r POLYMARKET_WALLET_ADDRESS
if [[ -n "$POLYMARKET_WALLET_ADDRESS" ]]; then
  [[ "$POLYMARKET_WALLET_ADDRESS" =~ ^0x[0-9A-Fa-f]{40}$ ]] \
    || fail "wallet_address_format_invalid"
fi

cat > "$SECRET_TMP" <<EOF
MODE=live
LIVE_TRADING_ENABLED=true
MAX_TRADE_SIZE_USD=5
MAX_TOTAL_EXPOSURE_USD=5
MAX_DAILY_LOSS_USD=5
MAX_CONSECUTIVE_LOSSES=1
LIVE_MIN_EDGE=0.075
LIVE_MIN_PROBABILITY=0
LIVE_MIN_LIQUIDITY_USD=1
LIVE_MAX_SPREAD=0.05
LIVE_MAX_PREDICTION_AGE_SECONDS=10
LIVE_MIN_TIME_TO_EXPIRY_SECONDS=45
LIVE_COOLDOWN_SECONDS=300
LIVE_ACTIVATION_MANIFEST_PATH=/var/lib/bp/live-canary/activation.json
LIVE_KILL_SWITCH_PATH=/var/lib/bp/live-canary/KILL
POLYMARKET_PRIVATE_KEY=$POLYMARKET_PRIVATE_KEY
POLYMARKET_WALLET_ADDRESS=$POLYMARKET_WALLET_ADDRESS
BP_CANARY_EXPECTED_GIT_SHA=$LOCAL_HEAD
BP_CANARY_ATTEMPTED_PATH=/var/lib/bp/live-canary/ATTEMPTED.json
BP_CANARY_EXECUTOR_LOCK_PATH=/var/lib/bp/live-canary/executor.lock
EOF
unset POLYMARKET_PRIVATE_KEY
unset POLYMARKET_WALLET_ADDRESS

gcloud compute scp "$SECRET_TMP" "$EXEC_VM:/tmp/bp-v3-canary-executor.env" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet
: > "$SECRET_TMP"
gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command='sudo install -o root -g bp-exec -m 0640 /tmp/bp-v3-canary-executor.env /etc/bp/bp-v3-canary-executor.env && rm -f /tmp/bp-v3-canary-executor.env'

ISSUED_AT=$(python3 - <<'PY'
from datetime import UTC, datetime
print(datetime.now(UTC).isoformat())
PY
)
EXPIRES_AT=$(python3 - "$ISSUED_AT" <<'PY'
import sys
from datetime import datetime, timedelta
print((datetime.fromisoformat(sys.argv[1]) + timedelta(hours=2)).isoformat())
PY
)
AUTHORIZATION_ID="phase15-v3-single-order-canary-${LOCAL_HEAD:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
python3 - "$MANIFEST_TMP" "$LOCAL_HEAD" "$AUTHORIZATION_ID" "$ISSUED_AT" "$EXPIRES_AT" <<'PY'
import json,sys
from pathlib import Path
path,sha,auth,issued,expires=sys.argv[1:]
Path(path).write_text(json.dumps({
    "authorized": True,
    "git_sha": sha,
    "authorization_id": auth,
    "issued_at": issued,
    "expires_at": expires,
}, sort_keys=True, separators=(",",":"))+"\n", encoding="utf-8")
PY

gcloud compute scp "$MANIFEST_TMP" "$US_VM:/tmp/bp-v3-live-canary-activation.json" \
  --project="$PROJECT" --zone="$US_ZONE" --quiet
gcloud compute scp "$MANIFEST_TMP" "$EXEC_VM:/tmp/bp-v3-live-canary-activation.json" \
  --project="$PROJECT" --zone="$EXEC_ZONE" --quiet

gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo bash -s -- '$LOCAL_HEAD' '$EXEC_INTERNAL'" <<'REMOTE'
set -Eeuo pipefail
HEAD_SHA=$1
EXEC_INTERNAL=$2
DB_URL=$(awk -F= '$1 == "DATABASE_URL" {sub(/^[^=]*=/, ""); print; exit}' /etc/bp/bp.env)
[[ -n "$DB_URL" ]]
cat > /etc/bp/bp-v3-live-canary.env <<EOF
MODE=live
LIVE_TRADING_ENABLED=true
DATABASE_URL=$DB_URL
MAX_TRADE_SIZE_USD=5
MAX_TOTAL_EXPOSURE_USD=5
MAX_DAILY_LOSS_USD=5
MAX_CONSECUTIVE_LOSSES=1
LIVE_MIN_EDGE=0.075
LIVE_MIN_PROBABILITY=0
LIVE_MIN_LIQUIDITY_USD=1
LIVE_MAX_SPREAD=0.05
LIVE_MAX_PREDICTION_AGE_SECONDS=10
LIVE_MIN_TIME_TO_EXPIRY_SECONDS=45
LIVE_COOLDOWN_SECONDS=300
LIVE_ACTIVATION_MANIFEST_PATH=/var/lib/bp/live-canary/activation.json
LIVE_KILL_SWITCH_PATH=/var/lib/bp/live-canary/KILL
BP_CANARY_EXPECTED_GIT_SHA=$HEAD_SHA
BP_CANARY_REMOTE_HOST=$EXEC_INTERNAL
BP_CANARY_REMOTE_USER=bp-exec
BP_CANARY_REMOTE_KEY_PATH=/var/lib/bp/live-canary/ssh/id_ed25519
BP_CANARY_REMOTE_KNOWN_HOSTS_PATH=/var/lib/bp/live-canary/ssh/known_hosts
EOF
chown root:bp /etc/bp/bp-v3-live-canary.env
chmod 640 /etc/bp/bp-v3-live-canary.env
install -o root -g bp -m 0640 /tmp/bp-v3-live-canary-activation.json /var/lib/bp/live-canary/activation.json
rm -f /tmp/bp-v3-live-canary-activation.json
REMOTE

gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command='sudo install -o root -g bp-exec -m 0640 /tmp/bp-v3-live-canary-activation.json /var/lib/bp/live-canary/activation.json && rm -f /tmp/bp-v3-live-canary-activation.json'

LEDGER=$(gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="sudo -u bp env BP_ENV_FILE=/etc/bp/bp-v3-live-canary.env PYTHONPATH=/var/lib/bp/runtime/v3-live-canary-current/src /opt/bp/.venv/bin/python - <<'PY'
import json
from sqlalchemy import create_engine, func, select
from bp_engine.config import get_settings
from bp_engine.storage import schema
s=get_settings()
e=create_engine(s.database_url,pool_pre_ping=True)
with e.connect() as c:
    out={}
    for name,table in [
        ('live_order_intents',schema.live_order_intents),
        ('live_order_events',schema.live_order_events),
    ]:
        out[name]=int(c.execute(select(func.count()).select_from(table)).scalar_one())
print(json.dumps(out,sort_keys=True))
e.dispose()
PY")
python3 - "$LEDGER" <<'PY' || fail "live_ledger_not_empty"
import json,sys
p=json.loads(sys.argv[1])
assert p["live_order_intents"] == 0
assert p["live_order_events"] == 0
PY

REMOTE_HEALTH=$(gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command="printf '%s\n' '{"operation":"health"}' | sudo -u bp ssh -i /var/lib/bp/live-canary/ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/bp/live-canary/ssh/known_hosts bp-exec@$EXEC_INTERNAL")
python3 - "$REMOTE_HEALTH" <<'PY' || fail "remote_executor_health_failed"
import json,sys
p=json.loads(sys.argv[1])
assert p["blocked"] is False
assert p["country"] == "ZA"
assert p["region"] == "GP"
assert p["private_key_configured"] is True
assert p["sdk_client_ready"] is True
assert p["canary_attempted"] is False
assert p["kill_switch_engaged"] is True
PY

# Final activation sequence. The remote host is unlocked first while the local
# worker remains stopped and locally killed, so no order path exists yet.
gcloud compute ssh "$EXEC_VM" --project="$PROJECT" --zone="$EXEC_ZONE" --quiet \
  --command='sudo -u bp-exec rm -f /var/lib/bp/live-canary/KILL'
gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command='sudo -u bp rm -f /var/lib/bp/live-canary/KILL && sudo systemctl start bp-v3-live-canary.service'

sleep 1
UNIT_STATE=$(gcloud compute ssh "$US_VM" --project="$PROJECT" --zone="$US_ZONE" --quiet \
  --command='systemctl show bp-v3-live-canary.service --property=ActiveState --property=SubState --property=Result --no-pager')
if grep -q '^Result=failed$' <<<"$UNIT_STATE"; then
  fail "canary_service_failed_on_start"
fi

SUCCESS=true
echo "PHASE15_V3_SINGLE_ORDER_CANARY_DEPLOY=PASS"
echo "GIT_SHA=$LOCAL_HEAD"
echo "AUTHORIZATION_ID=$AUTHORIZATION_ID"
echo "ACTIVATION_EXPIRES_AT=$EXPIRES_AT"
echo "EXECUTION_HOST=$EXEC_VM"
echo "EXECUTION_ZONE=$EXEC_ZONE"
echo "TARGET_NOTIONAL_USD=5.00"
echo "MAX_EXTERNAL_SUBMISSION_ATTEMPTS=1"
echo "US_PRODUCTION_SERVICES_UNCHANGED=true"
echo "TRADING_SECRET_ON_US_HOST=false"
echo "UNIT_STATE_BEGIN"
echo "$UNIT_STATE"
echo "UNIT_STATE_END"
echo "Monitor with:"
echo "  gcloud compute ssh $US_VM --project=$PROJECT --zone=$US_ZONE --command='sudo journalctl -u bp-v3-live-canary.service -n 50 --no-pager'"
