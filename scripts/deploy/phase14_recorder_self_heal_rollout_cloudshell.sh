#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_RECORDER_SELF_HEAL_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_RECORDER_SELF_HEAL_ZONE:-us-east1-c}"
VM="${PHASE14_RECORDER_SELF_HEAL_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_RECORDER_SELF_HEAL_HELPER_HEAD:-}"
APPROVAL="${PHASE14_RECORDER_SELF_HEAL_APPROVAL:-}"
PREFLIGHT_ONLY="${PHASE14_RECORDER_SELF_HEAL_PREFLIGHT_ONLY:-false}"

FROM_HEAD='2bac3b4c20ae5d1fb6fb8caa80edaf1928674706'
CANDIDATE_BRANCH='ops/phase14-recorder-self-heal-candidate-20261008'
CANDIDATE_HEAD='a352c66ec0110925727bc40de767ee4ba981f965'
EXPECTED_SHADOW_RUN_ID='v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f'
EXPECTED_SHADOW_UNIT='bp-v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service'
EXPECTED_MODEL_SHA256='6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf'

SERVICE_PATH='src/bp_engine/recorder/service.py'
SERVICE_TEST_PATH='tests/recorder/test_recorder_service.py'

fail_local() {
  echo "PHASE14_RECORDER_SELF_HEAL_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] ||
  fail_local "helper_head_invalid"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"

[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] ||
  fail_local "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail_local "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] ||
  fail_local "remote_main_changed"

git fetch --quiet origin \
  "$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"
REMOTE_CANDIDATE="$(
  git rev-parse "refs/remotes/origin/$CANDIDATE_BRANCH"
)"
[[ "$REMOTE_CANDIDATE" == "$CANDIDATE_HEAD" ]] ||
  fail_local "candidate_branch_changed"
git merge-base --is-ancestor "$FROM_HEAD" "$CANDIDATE_HEAD" ||
  fail_local "candidate_not_descendant_of_deployed_head"

EXPECTED_DIFF="$(
  printf '%s\n' \
    "$SERVICE_PATH" \
    "$SERVICE_TEST_PATH" |
    sort
)"
ACTUAL_DIFF="$(
  git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD" |
    sort
)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] ||
  fail_local "candidate_scope_mismatch"

for path in "$SERVICE_PATH" "$SERVICE_TEST_PATH"; do
  [[ "$(git rev-parse "$CANDIDATE_HEAD:$path")" == \
      "$(git rev-parse "$HELPER_HEAD:$path")" ]] ||
    fail_local "candidate_blob_not_exact_main:$path"
done

EXPECTED_APPROVAL="I_APPROVE_PHASE14_RECORDER_SELF_HEAL_RESTART:${HELPER_HEAD}:${FROM_HEAD}:${CANDIDATE_HEAD}:${EXPECTED_SHADOW_RUN_ID}:${EXPECTED_MODEL_SHA256}"

case "$PREFLIGHT_ONLY" in
  true|false) ;;
  *) fail_local "preflight_only_invalid" ;;
esac

if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
  echo "PHASE14_RECORDER_SELF_HEAL_PREFLIGHT=PASS"
  echo "HELPER_HEAD=$HELPER_HEAD"
  echo "FROM_HEAD=$FROM_HEAD"
  echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
  echo "EXPECTED_SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
  echo "EXPECTED_MODEL_SHA256=$EXPECTED_MODEL_SHA256"
  echo "EXPECTED_APPROVAL=$EXPECTED_APPROVAL"
  echo "PRODUCTION_HOST_CONTACTED=false"
  echo "PRODUCTION_MUTATION_PERFORMED=false"
  echo "SERVICE_RESTART_PERFORMED=false"
  echo "DATABASE_WRITES_PERFORMED=false"
  echo "LIVE_TRADING_ENABLED=false"
  echo "REAL_MONEY_USD=0"
  exit 0
fi

[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] ||
  fail_local "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 ||
  fail_local "gcloud_missing"
gcloud auth list \
  --filter=status:ACTIVE \
  --format='value(account)' |
  grep -q . ||
  fail_local "gcloud_auth_missing"

gcloud config set project "$PROJECT" >/dev/null

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

FROM_HEAD='2bac3b4c20ae5d1fb6fb8caa80edaf1928674706'
CANDIDATE_BRANCH='ops/phase14-recorder-self-heal-candidate-20261008'
CANDIDATE_HEAD='a352c66ec0110925727bc40de767ee4ba981f965'
EXPECTED_SHADOW_RUN_ID='v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f'
EXPECTED_SHADOW_UNIT='bp-v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service'
EXPECTED_MODEL_SHA256='6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf'

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EVIDENCE_DIR=/var/lib/bp/evidence
RECORDER_UNIT=bp-recorder.service
FAST_LIVE_SOURCE=bp-phase15-fast-live-source.service
EXPECTED_EVIDENCE="$EVIDENCE_DIR/$EXPECTED_SHADOW_RUN_ID.jsonl"

MUTATION_STARTED=0
ROLLBACK_ARMED=0
BACKUP_DIR=''
ACCEPT_TMP=''
RECORDER_PID_BEFORE=''
RECORDER_RESTARTS_BEFORE=''
RECORDER_STARTED_BEFORE=''
SHADOW_PID=''
SHADOW_RESTARTS=''

fail() {
  echo "PHASE14_RECORDER_SELF_HEAL_ROLLOUT_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" \
    '$1 == key {sub(/^[^=]*=/, ""); print; exit}' \
    "$path"
}

validate_deployed_checkout() {
  local entry code path
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json) ;;
        *) fail "unexpected_deployed_checkout_change:$path" ;;
      esac
    fi
  done < <(
    git -C "$REPO" status --porcelain --untracked-files=all
  )
}

require_research_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode="$(read_env "$path" MODE)"
    live="$(read_env "$path" LIVE_TRADING_ENABLED)"
    trade="$(read_env "$path" MAX_TRADE_SIZE_USD)"
    loss="$(read_env "$path" MAX_DAILY_LOSS_USD)"
    [[ "$mode" == "research" ]] ||
      fail "mode_not_research:$path"
    [[ "$live" == "false" ]] ||
      fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] ||
      fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] ||
      fail "max_daily_loss_nonzero:$path"
  done
}

require_recorder_contract() {
  sudo -u bp env \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$REPO/src" \
    "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
import sys
from bp_engine.config import Settings

settings = Settings(_env_file=sys.argv[1])
expected = {
    "recorder_batch_size": 100,
    "recorder_priority_batch_size": 20,
    "recorder_writer_workers": 4,
}
for key, value in expected.items():
    actual = getattr(settings, key)
    if actual != value:
        raise SystemExit(f"{key} expected {value}, got {actual}")
print("RECORDER_CONTRACT=PASS")
PY
}

validate_shadow_contract() {
  systemctl is-active --quiet "$EXPECTED_SHADOW_UNIT" ||
    fail "current_shadow_not_active"
  systemctl is-active --quiet "$FAST_LIVE_SOURCE" &&
    fail "fast_live_source_active"
  systemctl is-enabled --quiet "$FAST_LIVE_SOURCE" &&
    fail "fast_live_source_enabled"
  [[ -f "$EXPECTED_EVIDENCE" && ! -L "$EXPECTED_EVIDENCE" ]] ||
    fail "shadow_evidence_missing_or_invalid"

  "$REPO/.venv/bin/python" - \
    "$EXPECTED_EVIDENCE" \
    "$EXPECTED_MODEL_SHA256" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_model = sys.argv[2]
starts = []
for raw in path.read_text(encoding="utf-8").splitlines():
    if not raw.startswith("{"):
        continue
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if (
        isinstance(row, dict)
        and row.get("event") == "v4_fresh_book_shadow_started"
    ):
        starts.append(row)
if len(starts) != 1:
    raise SystemExit(
        f"expected exactly one shadow start record, got {len(starts)}"
    )
start = starts[0]
if start.get("model_sha256") != expected_model:
    raise SystemExit("shadow model digest mismatch")
if start.get("database_read_only") is not True:
    raise SystemExit("shadow database_read_only mismatch")
if start.get("order_submission_enabled") is not False:
    raise SystemExit("shadow order submission unexpectedly enabled")
if start.get("holdout_labels_read") is not False:
    raise SystemExit("shadow holdout label contract mismatch")
PY
}

probe_v4_sources() {
  local mode=$1 destination=$2
  sudo -u bp env \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$REPO/src" \
    "$REPO/.venv/bin/python" - \
    "$ENV_FILE" "$mode" > "$destination" <<'PY'
import json
import sys
import time
from datetime import UTC, datetime
from sqlalchemy import create_engine, text
from bp_engine.config import Settings

env_file, mode = sys.argv[1:3]
settings = Settings(_env_file=env_file)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={
        "options": (
            "-c default_transaction_read_only=on "
            "-c statement_timeout=3000 "
            "-c application_name=bp-recorder-self-heal-acceptance"
        )
    },
)

specs = (
    ("coinbase", "spot", "BTC-USD"),
    ("bybit", "spot", "BTCUSDT"),
    ("bybit", "linear", "BTCUSDT"),
)

def snapshot():
    now = datetime.now(UTC)
    rows = []
    with engine.connect() as connection:
        readonly = connection.execute(
            text("SHOW default_transaction_read_only")
        ).scalar_one()
        if str(readonly).lower() != "on":
            raise SystemExit("database read-only guard failed")
        for source, stream, instrument in specs:
            row = connection.execute(
                text(
                    """
                    SELECT
                        tableoid::regclass::text AS partition_name,
                        event_type,
                        source_timestamp,
                        received_at
                    FROM raw_market_events
                    WHERE source = :source
                      AND stream = :stream
                      AND instrument = :instrument
                      AND source_timestamp IS NOT NULL
                      AND (
                            (:source = 'coinbase'
                             AND (
                                  event_type LIKE 'ticker_%'
                                  OR event_type LIKE 'market_trades_%'
                             ))
                         OR (:source = 'bybit'
                             AND event_type IN ('ticker', 'trade'))
                      )
                    ORDER BY received_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {
                    "source": source,
                    "stream": stream,
                    "instrument": instrument,
                },
            ).mappings().first()
            payload = {
                "source": source,
                "stream": stream,
                "instrument": instrument,
                "latest": None,
                "age_seconds": None,
            }
            if row is not None:
                received = row["received_at"].astimezone(UTC)
                payload["latest"] = {
                    key: str(value)
                    for key, value in dict(row).items()
                }
                payload["age_seconds"] = (
                    now - received
                ).total_seconds()
            rows.append(payload)
    return now, rows

try:
    if mode == "stale":
        now, rows = snapshot()
        if not all(
            item["age_seconds"] is not None
            and float(item["age_seconds"]) >= 60.0
            for item in rows
        ):
            raise SystemExit(
                "recorder sources are not all stale; refusing restart"
            )
        payload = {
            "status": "stale_confirmed",
            "observed_at": now.isoformat(),
            "sources": rows,
        }
    elif mode == "fresh":
        deadline = time.monotonic() + 90.0
        payload = None
        while time.monotonic() < deadline:
            now, rows = snapshot()
            if all(
                item["age_seconds"] is not None
                and 0.0 <= float(item["age_seconds"]) <= 5.0
                for item in rows
            ):
                payload = {
                    "status": "fresh_confirmed",
                    "observed_at": now.isoformat(),
                    "sources": rows,
                }
                break
            time.sleep(1.0)
        if payload is None:
            raise SystemExit(
                "recorder sources did not all become fresh within 90s"
            )
    else:
        raise SystemExit(f"unknown probe mode: {mode}")
finally:
    engine.dispose()

print(json.dumps(payload, indent=2, sort_keys=True))
PY
}

restore_generated_files() {
  [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || return 0
  if [[ -f "$BACKUP_DIR/next-env.d.ts" ]]; then
    cp -a \
      "$BACKUP_DIR/next-env.d.ts" \
      "$REPO/apps/dashboard/next-env.d.ts"
  fi
  if [[ -f "$BACKUP_DIR/tsconfig.json" ]]; then
    cp -a \
      "$BACKUP_DIR/tsconfig.json" \
      "$REPO/apps/dashboard/tsconfig.json"
  fi
}

rollback() {
  set +e
  echo "PHASE14_RECORDER_SELF_HEAL_ROLLBACK=START" >&2
  git -C "$REPO" checkout \
    --detach \
    --force \
    "$FROM_HEAD" \
    >/dev/null 2>&1 || true
  restore_generated_files
  systemctl restart "$RECORDER_UNIT" >/dev/null 2>&1 || true
  sleep 3
  echo "DEPLOYED_HEAD=$(
    git -C "$REPO" rev-parse HEAD 2>/dev/null || true
  )" >&2
  echo "RECORDER_ACTIVE=$(
    systemctl is-active "$RECORDER_UNIT" 2>/dev/null || true
  )" >&2
  echo "SHADOW_ACTIVE=$(
    systemctl is-active "$EXPECTED_SHADOW_UNIT" 2>/dev/null || true
  )" >&2
  echo "PHASE14_RECORDER_SELF_HEAL_ROLLBACK=COMPLETE" >&2
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && MUTATION_STARTED == 1 && ROLLBACK_ARMED == 1 )); then
    rollback
  fi
  rm -f "$ACCEPT_TMP"
  [[ -n "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] ||
  fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] ||
  fail "python_runtime_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$FROM_HEAD" ]] ||
  fail "unexpected_deployed_head"

validate_deployed_checkout
require_research_zero_money
require_recorder_contract
validate_shadow_contract
systemctl is-active --quiet "$RECORDER_UNIT" ||
  fail "recorder_not_active"

RECORDER_PID_BEFORE="$(
  systemctl show -p MainPID --value "$RECORDER_UNIT"
)"
RECORDER_RESTARTS_BEFORE="$(
  systemctl show -p NRestarts --value "$RECORDER_UNIT"
)"
RECORDER_STARTED_BEFORE="$(
  systemctl show -p ExecMainStartTimestamp --value "$RECORDER_UNIT"
)"
SHADOW_PID="$(
  systemctl show -p MainPID --value "$EXPECTED_SHADOW_UNIT"
)"
SHADOW_RESTARTS="$(
  systemctl show -p NRestarts --value "$EXPECTED_SHADOW_UNIT"
)"

STALE_TMP="$(mktemp /var/tmp/bp-recorder-self-heal-stale.XXXXXX.json)"
if ! probe_v4_sources stale "$STALE_TMP"; then
  cat "$STALE_TMP" >&2 || true
  rm -f "$STALE_TMP"
  fail "pre_restart_staleness_not_confirmed"
fi
cat "$STALE_TMP"
rm -f "$STALE_TMP"

git -C "$REPO" fetch \
  --quiet \
  origin \
  "$CANDIDATE_BRANCH:refs/remotes/origin/$CANDIDATE_BRANCH"

[[ "$(
  git -C "$REPO" rev-parse \
    "refs/remotes/origin/$CANDIDATE_BRANCH"
)" == "$CANDIDATE_HEAD" ]] ||
  fail "remote_candidate_changed"

EXPECTED_DIFF="$(
  printf '%s\n' \
    src/bp_engine/recorder/service.py \
    tests/recorder/test_recorder_service.py |
    sort
)"
ACTUAL_DIFF="$(
  git -C "$REPO" diff \
    --name-only \
    "$FROM_HEAD" \
    "$CANDIDATE_HEAD" |
    sort
)"
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] ||
  fail "candidate_scope_mismatch"

BACKUP_DIR="$(mktemp -d /var/tmp/bp-recorder-self-heal-backup.XXXXXX)"
if ! git -C "$REPO" diff \
  --quiet \
  HEAD \
  -- apps/dashboard/next-env.d.ts
then
  cp -a \
    "$REPO/apps/dashboard/next-env.d.ts" \
    "$BACKUP_DIR/next-env.d.ts"
fi
if ! git -C "$REPO" diff \
  --quiet \
  HEAD \
  -- apps/dashboard/tsconfig.json
then
  cp -a \
    "$REPO/apps/dashboard/tsconfig.json" \
    "$BACKUP_DIR/tsconfig.json"
fi

MUTATION_STARTED=1
ROLLBACK_ARMED=1

git -C "$REPO" checkout \
  --detach \
  --force \
  "$CANDIDATE_HEAD"
restore_generated_files

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$CANDIDATE_HEAD" ]] ||
  fail "candidate_checkout_failed"
validate_deployed_checkout

grep -Fq \
  'task.cancel()' \
  "$REPO/src/bp_engine/recorder/service.py" ||
  fail "candidate_self_heal_contract_missing"

systemctl restart "$RECORDER_UNIT"
sleep 2
systemctl is-active --quiet "$RECORDER_UNIT" ||
  fail "recorder_not_active_after_restart"

RECORDER_PID_AFTER="$(
  systemctl show -p MainPID --value "$RECORDER_UNIT"
)"
RECORDER_STARTED_AFTER="$(
  systemctl show -p ExecMainStartTimestamp --value "$RECORDER_UNIT"
)"
[[ "$RECORDER_PID_AFTER" != "$RECORDER_PID_BEFORE" ]] ||
  fail "recorder_pid_did_not_change"
[[ "$RECORDER_STARTED_AFTER" != "$RECORDER_STARTED_BEFORE" ]] ||
  fail "recorder_start_timestamp_did_not_change"

ACCEPT_TMP="$(mktemp /var/tmp/bp-recorder-self-heal-accept.XXXXXX.json)"
if ! probe_v4_sources fresh "$ACCEPT_TMP"; then
  cat "$ACCEPT_TMP" >&2 || true
  fail "post_restart_freshness_failed"
fi

require_research_zero_money
require_recorder_contract
validate_shadow_contract

[[ "$(
  systemctl show -p MainPID --value "$EXPECTED_SHADOW_UNIT"
)" == "$SHADOW_PID" ]] ||
  fail "shadow_pid_changed"
[[ "$(
  systemctl show -p NRestarts --value "$EXPECTED_SHADOW_UNIT"
)" == "$SHADOW_RESTARTS" ]] ||
  fail "shadow_restarted"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_PATH="$EVIDENCE_DIR/phase14-recorder-self-heal-$STAMP.json"
install -o bp -g bp -m 0640 \
  "$ACCEPT_TMP" \
  "$EVIDENCE_PATH"
sync -f "$EVIDENCE_PATH"

ROLLBACK_ARMED=0

cat "$ACCEPT_TMP"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
echo "PHASE14_RECORDER_SELF_HEAL_ROLLOUT_GATE=PASS"
echo "FROM_HEAD=$FROM_HEAD"
echo "DEPLOYED_HEAD=$(git -C "$REPO" rev-parse HEAD)"
echo "RECORDER_PID_BEFORE=$RECORDER_PID_BEFORE"
echo "RECORDER_PID_AFTER=$RECORDER_PID_AFTER"
echo "RECORDER_RESTARTS_BEFORE=$RECORDER_RESTARTS_BEFORE"
echo "RECORDER_RESTARTS_AFTER=$(
  systemctl show -p NRestarts --value "$RECORDER_UNIT"
)"
echo "RECORDER_ACTIVE=true"
echo "SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
echo "SHADOW_UNIT_ACTIVE=true"
echo "SHADOW_RESTARTED=false"
echo "DATABASE_WRITES_PERFORMED_BY_HELPER=false"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
REMOTE

REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "FROM_HEAD=$FROM_HEAD"
echo "CANDIDATE_HEAD=$CANDIDATE_HEAD"
echo "EXPECTED_SHADOW_RUN_ID=$EXPECTED_SHADOW_RUN_ID"
echo "This helper restarts only bp-recorder.service after checking out"
echo "the narrow recorder self-heal candidate. The V4 shadow is not restarted."
echo "It first requires all three V4 sources to still be stale and then"
echo "requires all three to become fresh within 90 seconds."
echo "Failure restores the prior checkout and restarts the recorder there."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
