#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'PHASE15_FAST_LIVE_ACTIVATE=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ACCEPT="${PHASE15_ACCEPT_FAST_LIVE_ACTIVATION:-}"
[[ "$ACCEPT" == "I_ACCEPT_CONTINUOUS_TELEGRAM_APPROVED_LIVE_SESSION" ]] ||
  fail "explicit_continuous_session_acceptance_required"

: "${BP_FAST_LIVE_GCP_PROJECT:?BP_FAST_LIVE_GCP_PROJECT is required}"
: "${BP_FAST_LIVE_RUNTIME_EXPIRES_AT:?BP_FAST_LIVE_RUNTIME_EXPIRES_AT is required}"
: "${BP_FAST_LIVE_RECORDER_VM:=bp-recorder}"
: "${BP_FAST_LIVE_RECORDER_ZONE:=us-east1-c}"
: "${BP_FAST_LIVE_EXEC_VM:=bp-v3-canary-exec}"
: "${BP_FAST_LIVE_EXEC_ZONE:=africa-south1-a}"

PROJECT="$BP_FAST_LIVE_GCP_PROJECT"
US_VM="$BP_FAST_LIVE_RECORDER_VM"
US_ZONE="$BP_FAST_LIVE_RECORDER_ZONE"
EXEC_VM="$BP_FAST_LIVE_EXEC_VM"
EXEC_ZONE="$BP_FAST_LIVE_EXEC_ZONE"
STATE="$ROOT/PROJECT_STATE.json"

[[ -f "$STATE" && ! -L "$STATE" ]] || fail "project_state_invalid"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_not_clean"

git -C "$ROOT" fetch origin main --quiet || fail "fetch_main_failed"
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
REMOTE_MAIN="$(git -C "$ROOT" rev-parse origin/main)"
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$HEAD" == "$REMOTE_MAIN" ]] || fail "checkout_is_not_current_main"

read -r AUTH_ID AUTH_EXPIRES STATE_SHA < <(
  PYTHONPATH="$ROOT/src" python3 - "$STATE" "$HEAD" <<'PY'
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.fast_live import verify_source_authorization

path = Path(sys.argv[1])
head = sys.argv[2]
raw = path.read_bytes()
state = json.loads(raw)
auth = verify_source_authorization(
    state,
    expected_main=head,
    observed_at=datetime.now(UTC),
    requires_telegram_approval=True,
    continuous_session=True,
)
print(
    str(auth["authorization_id"]),
    str(auth["expires_at"]),
    hashlib.sha256(
        json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest(),
)
PY
) || fail "source_truth_fast_live_authorization_invalid"

[[ -n "$AUTH_ID" ]] || fail "authorization_id_missing"
[[ "$STATE_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "project_state_hash_invalid"

TMP_DIR="$(mktemp -d)"
cleanup_local() {
  rm -rf "$TMP_DIR"
}
trap cleanup_local EXIT

RUNTIME_AUTH="$TMP_DIR/authorization.json"
python3 - "$STATE" "$HEAD" "$AUTH_ID" "$BP_FAST_LIVE_RUNTIME_EXPIRES_AT" "$RUNTIME_AUTH" <<'PY'
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

state_path = Path(sys.argv[1])
release_main = sys.argv[2]
authorization_id = sys.argv[3]
runtime_expires = datetime.fromisoformat(sys.argv[4]).astimezone(UTC)
output = Path(sys.argv[5])
state = json.loads(state_path.read_text(encoding="utf-8"))
source = state["phase_15_v3_live_canary"]["fast_live_preauthorization"]
source_expires = datetime.fromisoformat(str(source["expires_at"])).astimezone(UTC)
now = datetime.now(UTC)
if runtime_expires <= now:
    raise SystemExit("runtime_expired")
if runtime_expires > source_expires:
    raise SystemExit("runtime_exceeds_source_authorization")
state_hash = hashlib.sha256(
    json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
).hexdigest()
payload = {
    "schema_version": 1,
    "purpose": "phase15-v3-fast-live-v1",
    "authorized": True,
    "authorization_id": authorization_id,
    "release_main": release_main,
    "project_state_sha256": state_hash,
    "authorization_mode": "manual-telegram-continuous-v1",
    "continuous_session": True,
    "requires_telegram_approval": True,
    "max_network_submission_attempts_per_intent": 1,
    "target_notional_usd": 5,
    "issued_at": now.isoformat(),
    "expires_at": runtime_expires.isoformat(),
}
output.write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
os.chmod(output, 0o600)
PY

PYTHONPATH="$ROOT/src" python3 - "$STATE" "$RUNTIME_AUTH" "$HEAD" <<'PY' ||
  fail "runtime_authorization_validation_failed"
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.execution.fast_live import verify_runtime_authorization

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
runtime = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
verify_runtime_authorization(
    runtime,
    state=state,
    expected_main=sys.argv[3],
    observed_at=datetime.now(UTC),
    requires_telegram_approval=True,
    continuous_session=True,
)
PY

AUTH_SUFFIX="$(python3 - "$AUTH_ID" <<'PY'
import hashlib
import sys
print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12])
PY
)"
KEY_ID="fast-live-$AUTH_SUFFIX"
TRANSPORT_KEY="$TMP_DIR/transport.key"
python3 - "$TRANSPORT_KEY" <<'PY'
import base64
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
encoded = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
path.write_text(encoded + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

ORDER_TOPIC="bp-phase15-fast-live-orders-$AUTH_SUFFIX"
ORDER_SUB="bp-phase15-fast-live-orders-$AUTH_SUFFIX-jhb"
RESULT_TOPIC="bp-phase15-fast-live-results-$AUTH_SUFFIX"
RESULT_SUB="bp-phase15-fast-live-results-$AUTH_SUFFIX-us"

US_SA="$(gcloud compute instances describe "$US_VM"   --project="$PROJECT" --zone="$US_ZONE"   --format='value(serviceAccounts[0].email)')" ||
  fail "recorder_service_account_lookup_failed"
EXEC_SA="$(gcloud compute instances describe "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE"   --format='value(serviceAccounts[0].email)')" ||
  fail "executor_service_account_lookup_failed"
[[ "$US_SA" == *@*.gserviceaccount.com ]] ||
  fail "recorder_service_account_invalid"
[[ "$EXEC_SA" == *@*.gserviceaccount.com ]] ||
  fail "executor_service_account_invalid"

# The runtime must already be staged at exactly the release we are authorizing.
gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="sudo test \"\$(readlink -f /opt/bp-fast-live/current)\" = '/opt/bp-fast-live/releases/$HEAD' &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-source.service && exit 20 || true;
             sudo systemctl is-enabled --quiet bp-phase15-fast-live-source.service && exit 21 || true;
             sudo test ! -e /etc/bp-fast-live/transport.key" ||
  fail "recorder_stage_not_ready"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo test \"\$(readlink -f /opt/bp-fast-live/current)\" = '/opt/bp-fast-live/releases/$HEAD' &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service && exit 22 || true;
             sudo systemctl is-enabled --quiet bp-phase15-fast-live-receiver.service && exit 23 || true;
             sudo test ! -e /etc/bp-fast-live/transport.key;
             sudo test -f /var/lib/bp-canary/fast-live/KILL;
             sudo test ! -e /var/lib/bp-canary/fast-live/attempt.json;
             sudo test ! -e /var/lib/bp-canary/fast-live/result.json" ||
  fail "executor_stage_not_ready"

HEALTH="$TMP_DIR/health.json"
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="printf '%s' '{\"action\":\"health\"}' | sudo /opt/bp-canary/executor.sh"   >"$HEALTH" || fail "executor_health_failed"

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

ensure_topic() {
  local topic="$1"
  if ! gcloud pubsub topics describe "$topic" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud pubsub topics create "$topic" --project="$PROJECT" >/dev/null ||
      fail "topic_create_failed:$topic"
  fi
}
ensure_subscription() {
  local sub="$1"
  local topic="$2"
  if ! gcloud pubsub subscriptions describe "$sub" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$sub"       --project="$PROJECT" --topic="$topic" --ack-deadline=30 >/dev/null ||
      fail "subscription_create_failed:$sub"
  fi
  local actual_topic
  actual_topic="$(gcloud pubsub subscriptions describe "$sub"     --project="$PROJECT" --format='value(topic)')"
  [[ "$actual_topic" == */topics/"$topic" ]] ||
    fail "subscription_topic_mismatch:$sub"
}

ensure_topic "$ORDER_TOPIC"
ensure_topic "$RESULT_TOPIC"
ensure_subscription "$ORDER_SUB" "$ORDER_TOPIC"
ensure_subscription "$RESULT_SUB" "$RESULT_TOPIC"

gcloud pubsub topics add-iam-policy-binding "$ORDER_TOPIC"   --project="$PROJECT"   --member="serviceAccount:$US_SA"   --role="roles/pubsub.publisher" >/dev/null ||
  fail "order_topic_publisher_iam_failed"
gcloud pubsub subscriptions add-iam-policy-binding "$ORDER_SUB"   --project="$PROJECT"   --member="serviceAccount:$EXEC_SA"   --role="roles/pubsub.subscriber" >/dev/null ||
  fail "order_subscription_subscriber_iam_failed"
gcloud pubsub topics add-iam-policy-binding "$RESULT_TOPIC"   --project="$PROJECT"   --member="serviceAccount:$EXEC_SA"   --role="roles/pubsub.publisher" >/dev/null ||
  fail "result_topic_publisher_iam_failed"
gcloud pubsub subscriptions add-iam-policy-binding "$RESULT_SUB"   --project="$PROJECT"   --member="serviceAccount:$US_SA"   --role="roles/pubsub.subscriber" >/dev/null ||
  fail "result_subscription_subscriber_iam_failed"

SOURCE_ENV="$TMP_DIR/source.env"
RECEIVER_ENV="$TMP_DIR/receiver.env"
cat >"$SOURCE_ENV" <<EOF
BP_FAST_LIVE_TRANSPORT_KEY_ID=$KEY_ID
BP_FAST_LIVE_EXPECTED_MAIN=$HEAD
BP_FAST_LIVE_GCP_PROJECT=$PROJECT
BP_FAST_LIVE_TOPIC_ID=$ORDER_TOPIC
BP_FAST_LIVE_RESULT_SUBSCRIPTION_ID=$RESULT_SUB
BP_FAST_LIVE_OFFICIAL_OPEN_ORDER_COUNT=$OPEN_ORDERS
BP_FAST_LIVE_COLLATERAL_BALANCE_USD=$COLLATERAL
BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED=yes
BP_FAST_LIVE_CONTINUOUS_SESSION=yes
EOF
cat >"$RECEIVER_ENV" <<EOF
BP_FAST_LIVE_TRANSPORT_KEY_ID=$KEY_ID
BP_FAST_LIVE_EXPECTED_MAIN=$HEAD
BP_FAST_LIVE_GCP_PROJECT=$PROJECT
BP_FAST_LIVE_SUBSCRIPTION_ID=$ORDER_SUB
BP_FAST_LIVE_RESULT_TOPIC_ID=$RESULT_TOPIC
BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED=yes
BP_FAST_LIVE_CONTINUOUS_SESSION=yes
EOF
chmod 0600 "$SOURCE_ENV" "$RECEIVER_ENV"

for item in   "$STATE:project-state.json"   "$RUNTIME_AUTH:authorization.json"   "$TRANSPORT_KEY:transport.key"   "$SOURCE_ENV:source.env"
do
  src="${item%%:*}"
  name="${item##*:}"
  gcloud compute scp "$src" "$US_VM:/tmp/bp-fast-live-$name"     --project="$PROJECT" --zone="$US_ZONE" --quiet >/dev/null ||
    fail "recorder_upload_failed:$name"
done
for item in   "$STATE:project-state.json"   "$RUNTIME_AUTH:authorization.json"   "$TRANSPORT_KEY:transport.key"   "$RECEIVER_ENV:receiver.env"
do
  src="${item%%:*}"
  name="${item##*:}"
  gcloud compute scp "$src" "$EXEC_VM:/tmp/bp-fast-live-$name"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet >/dev/null ||
    fail "executor_upload_failed:$name"
done

gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="sudo install -o root -g bp -m 0640 /tmp/bp-fast-live-project-state.json /etc/bp-fast-live/PROJECT_STATE.json &&
             sudo install -o root -g bp -m 0640 /tmp/bp-fast-live-authorization.json /etc/bp-fast-live/authorization.json &&
             sudo install -o root -g bp -m 0640 /tmp/bp-fast-live-transport.key /etc/bp-fast-live/transport.key &&
             sudo install -o root -g bp -m 0640 /tmp/bp-fast-live-source.env /etc/bp/phase15-fast-live-source.env &&
             rm -f /tmp/bp-fast-live-project-state.json /tmp/bp-fast-live-authorization.json /tmp/bp-fast-live-transport.key /tmp/bp-fast-live-source.env" ||
  fail "recorder_runtime_install_failed"

gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo install -o root -g root -m 0600 /tmp/bp-fast-live-project-state.json /etc/bp-fast-live/PROJECT_STATE.json &&
             sudo install -o root -g root -m 0600 /tmp/bp-fast-live-authorization.json /etc/bp-fast-live/authorization.json &&
             sudo install -o root -g root -m 0600 /tmp/bp-fast-live-transport.key /etc/bp-fast-live/transport.key &&
             sudo install -o root -g root -m 0600 /tmp/bp-fast-live-receiver.env /etc/bp-fast-live/receiver.env &&
             rm -f /tmp/bp-fast-live-project-state.json /tmp/bp-fast-live-authorization.json /tmp/bp-fast-live-transport.key /tmp/bp-fast-live-receiver.env" ||
  fail "executor_runtime_install_failed"

# Validate exact state/auth bytes after installation.
LOCAL_STATE_SHA="$(sha256sum "$STATE" | awk '{print $1}')"
LOCAL_AUTH_SHA="$(sha256sum "$RUNTIME_AUTH" | awk '{print $1}')"
LOCAL_KEY_SHA="$(sha256sum "$TRANSPORT_KEY" | awk '{print $1}')"
for spec in "$US_VM:$US_ZONE" "$EXEC_VM:$EXEC_ZONE"; do
  vm="${spec%%:*}"
  zone="${spec##*:}"
  read -r remote_state remote_auth remote_key < <(
    gcloud compute ssh "$vm" --project="$PROJECT" --zone="$zone" --quiet       --command="sudo sha256sum /etc/bp-fast-live/PROJECT_STATE.json /etc/bp-fast-live/authorization.json /etc/bp-fast-live/transport.key | awk '{print \$1}' | xargs"
  ) || fail "runtime_hash_read_failed:$vm"
  [[ "$remote_state" == "$LOCAL_STATE_SHA" ]] ||
    fail "project_state_hash_mismatch:$vm"
  [[ "$remote_auth" == "$LOCAL_AUTH_SHA" ]] ||
    fail "runtime_authorization_hash_mismatch:$vm"
  [[ "$remote_key" == "$LOCAL_KEY_SHA" ]] ||
    fail "transport_key_hash_mismatch:$vm"
done

activated=false
safe_stop() {
  set +e
  gcloud compute ssh "$EXEC_VM"     --project="$PROJECT" --zone="$EXEC_ZONE" --quiet     --command="sudo sh -c 'umask 077; mkdir -p /var/lib/bp-canary/fast-live; echo fast-live-activation-safe-stop > /var/lib/bp-canary/fast-live/KILL'; sudo systemctl stop bp-phase15-fast-live-receiver.service"     >/dev/null 2>&1 || true
  gcloud compute ssh "$US_VM"     --project="$PROJECT" --zone="$US_ZONE" --quiet     --command="sudo systemctl stop bp-phase15-fast-live-source.service"     >/dev/null 2>&1 || true
}
trap 'if [[ "$activated" != "true" ]]; then safe_stop; fi; cleanup_local' EXIT

# Receiver starts while the execution kill switch is still engaged.
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo test -f /var/lib/bp-canary/fast-live/KILL &&
             sudo systemctl start bp-phase15-fast-live-receiver.service &&
             sleep 1 &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-receiver.service" ||
  fail "receiver_start_failed"

# The only unarmed interval begins here. Source is started immediately afterward.
gcloud compute ssh "$EXEC_VM"   --project="$PROJECT" --zone="$EXEC_ZONE" --quiet   --command="sudo rm -f /var/lib/bp-canary/fast-live/KILL &&
             sudo test ! -e /var/lib/bp-canary/fast-live/KILL" ||
  fail "kill_switch_release_failed"

gcloud compute ssh "$US_VM"   --project="$PROJECT" --zone="$US_ZONE" --quiet   --command="sudo systemctl start bp-phase15-fast-live-source.service &&
             sleep 1 &&
             sudo systemctl is-active --quiet bp-phase15-fast-live-source.service" ||
  fail "source_start_failed"

activated=true
trap cleanup_local EXIT

printf 'PHASE15_FAST_LIVE_ACTIVATE=PASS\n'
printf 'RELEASE_MAIN=%s\n' "$HEAD"
printf 'AUTHORIZATION_ID=%s\n' "$AUTH_ID"
printf 'RUNTIME_EXPIRES_AT=%s\n' "$BP_FAST_LIVE_RUNTIME_EXPIRES_AT"
printf 'ORDER_TOPIC=%s\n' "$ORDER_TOPIC"
printf 'ORDER_SUBSCRIPTION=%s\n' "$ORDER_SUB"
printf 'RESULT_TOPIC=%s\n' "$RESULT_TOPIC"
printf 'RESULT_SUBSCRIPTION=%s\n' "$RESULT_SUB"
printf 'RECEIVER_ACTIVE=true\n'
printf 'SOURCE_ACTIVE=true\n'
printf 'SERVICES_ENABLED=false\n'
printf 'KILL_SWITCH_ENGAGED=false\n'
printf 'CONTINUOUS_SESSION=true\n'
printf 'PER_INTENT_NETWORK_SUBMISSION_ATTEMPTS=1\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
printf 'NOTE=The kill switch remains the session emergency stop; every trade still requires a fresh Telegram approval.\n'
