#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only preflight for PR #567's optimized V4 forward collector.
# Cannot deploy, switch runtimes, invoke the writer, or mutate production.
PROJECT=project-4397f2c0-7098-4c1c-abb
ZONE=us-east1-c
VM=bp-recorder
EXPECTED_DEPLOYED=a352c66ec0110925727bc40de767ee4ba981f965
OLD_RUNTIME_COMMIT=e7a21462374a1c19beae878c98ca319f13fb2d69
EXPECTED_OLD_TARGET=/var/lib/bp/runtime/v4-forward-$OLD_RUNTIME_COMMIT

fail() {
  printf 'PHASE14_V4_COVERAGE_OPTIMIZATION_READINESS=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repo_missing"
cd "$ROOT"
[[ "$(git branch --show-current)" == "main" ]] || fail "not_on_main"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_worktree_dirty"
LOCAL_HEAD="$(git rev-parse HEAD)"
[[ "$LOCAL_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "invalid_local_sha"
REMOTE_HEAD="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]] || fail "local_main_stale"
grep -q '^V4_FORWARD_MARKETS_PER_CYCLE = 1$' \
  src/bp_engine/features/v4_forward.py || fail "bounded_limit_missing"
grep -q 'V4_FORWARD_STAGE=' \
  src/bp_engine/features/v4_forward_cli.py || fail "stage_markers_missing"
grep -Fq 'from bp_engine.features.v4_coverage import build_v4_forward_coverage_summary' \
  src/bp_engine/features/v4_forward.py || fail "candidate_not_optimized"
grep -Fq 'def build_v4_forward_coverage_summary(' \
  src/bp_engine/features/v4_coverage.py || fail "summary_function_missing"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum_missing"
OLD_HASH="$(git show "$OLD_RUNTIME_COMMIT:src/bp_engine/features/v4_forward.py" | sha256sum | awk '{print $1}')"
[[ "$OLD_HASH" =~ ^[0-9a-f]{64}$ ]] || fail "old_source_hash_missing"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail
EXPECTED_DEPLOYED=__EXPECTED_DEPLOYED__
CANDIDATE_HEAD=__CANDIDATE_HEAD__
EXPECTED_OLD_TARGET=__EXPECTED_OLD_TARGET__
EXPECTED_OLD_HASH=__EXPECTED_OLD_HASH__

echo 'REMOTE_PROBE=read_only'
DEPLOYED="$(git -c safe.directory=/opt/bp -C /opt/bp rev-parse HEAD)"
[[ "$DEPLOYED" == "$EXPECTED_DEPLOYED" ]] || {
  echo "DEPLOYED_HEAD_CHANGED=$DEPLOYED" >&2
  exit 1
}
printf 'DEPLOYED_CHECKOUT_HEAD=%s\n' "$DEPLOYED"
printf 'BOUNDED_CANDIDATE_MAIN=%s\n' "$CANDIDATE_HEAD"

echo 'V4_FORWARD_RUNTIME'
LINK=/var/lib/bp/runtime/v4-forward-current
# /var/lib/bp is bp:bp 0750; the gcloud SSH user cannot traverse it.
# Use non-interactive sudo for these four read-only path/metadata operations.
sudo -n test -L "$LINK" || { echo 'FORWARD_CURRENT_NOT_SYMLINK' >&2; exit 1; }
TARGET="$(sudo -n readlink -f "$LINK")"
[[ "$TARGET" == "$EXPECTED_OLD_TARGET" ]] || {
  echo "OLD_V4_RUNTIME_CHANGED=$TARGET" >&2; exit 1;
}
sudo -n test -d "$TARGET" || { echo 'FORWARD_RUNTIME_MISSING' >&2; exit 1; }
sudo -n test -f "$TARGET/src/bp_engine/features/v4_forward.py" || {
  echo 'FORWARD_RUNTIME_CODE_MISSING' >&2; exit 1;
}
printf 'V4_FORWARD_CURRENT_TARGET=%s\n' "$TARGET"
ACTUAL_OLD_HASH="$(sudo -n sha256sum "$TARGET/src/bp_engine/features/v4_forward.py" | awk '{print $1}')"
printf 'V4_FORWARD_RUNTIME_CODE_SHA256=%s\n' "$ACTUAL_OLD_HASH"
[[ "$ACTUAL_OLD_HASH" == "$EXPECTED_OLD_HASH" ]] || {
  echo 'OLD_V4_RUNTIME_HASH_MISMATCH' >&2; exit 1;
}

echo 'V4_FORWARD_SERVICE'
systemctl show bp-v4-forward-coverage.service --no-pager \
  -p LoadState -p ActiveState -p SubState -p Result \
  -p TimeoutStartUSec -p ExecMainStatus -p FragmentPath
echo 'V4_FORWARD_TIMER'
systemctl show bp-v4-forward-coverage.timer --no-pager \
  -p ActiveState -p UnitFileState -p NextElapseUSecRealtime

echo 'FROZEN_CORE_SERVICES'
for unit in bp-postgres.service bp-recorder.service \
  bp-v3-frozen-predictor.service bp-v3-paper-execution.service; do
  state="$(systemctl is-active "$unit" || true)"
  printf '%s=%s\n' "$unit" "$state"
  [[ "$state" == active ]] || {
    echo "CORE_SERVICE_NOT_ACTIVE=$unit" >&2
    exit 1
  }
done

echo 'SAFETY_FILES'
sudo -n python3 - <<'PY'
import json
from pathlib import Path

expected = {
    "MODE": "research",
    "LIVE_TRADING_ENABLED": "false",
    "MAX_TRADE_SIZE_USD": "0",
    "MAX_DAILY_LOSS_USD": "0",
}
for name in ("/etc/bp/bp.env", "/etc/bp/bp-prospective-runtime-safety.env"):
    values = {}
    for raw in Path(name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            raise RuntimeError(f"safety mismatch {Path(name).name}:{key}")
    print(f"{Path(name).name}=PASS")

payload = json.loads(Path("/opt/bp/PROJECT_STATE.json").read_text())
promotions = []
def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "automatic_promotion":
                promotions.append(item)
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)
walk(payload)
assert promotions and all(v is False for v in promotions)
print("AUTOMATIC_PROMOTION=DISABLED")
PY

echo 'POSTGRES_AND_RECORDER_CONFIG'
# The V4 runtime is frozen at an older feature-collector SHA. Its Settings
# class predates the recorder priority-lane fields. Check the deployed
# recorder's actual configuration against the pinned /opt/bp checkout.
sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/bp/src /opt/bp/.venv/bin/python - <<'PY'
from sqlalchemy import create_engine, text
from bp_engine.config import Settings, TradingMode

s = Settings(_env_file="/etc/bp/bp.env")
assert s.mode is TradingMode.RESEARCH
assert s.live_trading_enabled is False
assert s.max_trade_size_usd == 0
assert s.max_daily_loss_usd == 0
assert s.max_total_exposure_usd == 0
assert s.recorder_batch_size == 100
assert s.recorder_writer_workers == 4
assert s.recorder_priority_batch_size == 20
engine = create_engine(s.database_url, connect_args={
    "options": "-c default_transaction_read_only=on "
               "-c statement_timeout=2000 "
               "-c application_name=bp-v4-bounded-forward-readiness"
})
try:
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"
        shared = conn.execute(text("SHOW shared_buffers")).scalar_one()
        print(f"POSTGRES_SHARED_BUFFERS={shared}")
        assert shared == "128MB"
finally:
    engine.dispose()
print("RECORDER_AND_DB_SAFETY=PASS")
PY

echo '=== V4 SCHEDULED CYCLE HEALTH (SINCE BOUNDED ROLLOUT) ==='
sudo -n journalctl -u bp-v4-forward-coverage.service \
  --since '2026-10-09 23:46:00 UTC' --no-pager -o cat |
awk '
/V4_FORWARD_STAGE=committed/ { commits++ }
/V4_FORWARD_STAGE=generation_start/ {
  if (match($0, /remaining=[0-9]+/)) {
    pending=substr($0,RSTART+10,RLENGTH-10)+0
    if (!observations++) first=pending
    last=pending
  }
}
/start operation timed out|Failed with result|Traceback/ { failures++ }
END {
  print "SCHEDULED_COMMITS=" commits+0
  print "SCHEDULED_FAILURE_EVENTS=" failures+0
  if (observations) {
    print "FIRST_PENDING_OBSERVED=" first
    print "LAST_PENDING_OBSERVED=" last
  }
  if (!commits || failures || !observations) exit 1
}
'
echo 'PHASE14_V4_COVERAGE_OPTIMIZATION_READINESS=PASS'
echo 'PRODUCTION_MUTATION=false'
echo 'DEPLOYMENT_EXECUTED=false'
echo 'ROLLOUT_AUTHORIZED=false'
REMOTE_SCRIPT

REMOTE="${REMOTE//__EXPECTED_DEPLOYED__/$EXPECTED_DEPLOYED}"
REMOTE="${REMOTE//__CANDIDATE_HEAD__/$LOCAL_HEAD}"
REMOTE="${REMOTE//__EXPECTED_OLD_TARGET__/$EXPECTED_OLD_TARGET}"
REMOTE="${REMOTE//__EXPECTED_OLD_HASH__/$OLD_HASH}"
printf 'OPTIMIZED_CANDIDATE_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_BOUNDED_RUNTIME=%s\n' "$EXPECTED_OLD_TARGET"
printf 'EXPECTED_BOUNDED_RUNTIME_HASH=%s\n' "$OLD_HASH"
printf 'READ_ONLY_HOST_PROBE=true\n'
gcloud compute ssh "$VM" --project="$PROJECT" --zone="$ZONE" \
  --quiet --command="$REMOTE"
