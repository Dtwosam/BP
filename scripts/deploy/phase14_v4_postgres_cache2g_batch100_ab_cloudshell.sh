#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT=project-4397f2c0-7098-4c1c-abb
ZONE=us-east1-c
VM=bp-recorder
HELPER_HEAD="$PHASE14_V4_PG_CACHE2G_BATCH100_AB_HELPER_HEAD"
DEPLOYED_HEAD="$PHASE14_V4_PG_CACHE2G_BATCH100_AB_DEPLOYED_HEAD"
APPROVAL="$PHASE14_V4_PG_CACHE2G_BATCH100_AB_APPROVAL"
EXPECTED_DEPLOYED_HEAD="${PHASE14_V4_PG_CACHE2G_BATCH100_AB_DEPLOYED_HEAD:-}"
PREFLIGHT_ONLY="${PHASE14_V4_PG_CACHE2G_BATCH100_AB_PREFLIGHT_ONLY:-true}"
EXPECTED_BATCH_SIZE=100
BASELINE_SHARED_BUFFERS=128MB
CANDIDATE_SHARED_BUFFERS=2GB

fail() {
  echo "PHASE14_V4_PG_CACHE2G_BATCH100_AB_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "deployed_head_invalid"
[[ "$DEPLOYED_HEAD" == "$EXPECTED_DEPLOYED_HEAD" ]] || fail "deployed_head_drift"
[[ "$PREFLIGHT_ONLY" == "true" || "$PREFLIGHT_ONLY" == "false" ]] ||
  fail "preflight_only_invalid"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail "remote_main_changed"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_PG_CACHE2G_BATCH100_AB:$HELPER_HEAD:$DEPLOYED_HEAD:$EXPECTED_BATCH_SIZE:$BASELINE_SHARED_BUFFERS:$CANDIDATE_SHARED_BUFFERS"
if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
  printf 'PHASE14_V4_PG_CACHE2G_BATCH100_AB_PREFLIGHT=PASS\n'
  printf 'LOCAL_HELPER_HEAD=%s\n' "$HELPER_HEAD"
  printf 'EXPECTED_DEPLOYED_HEAD=%s\n' "$DEPLOYED_HEAD"
  printf 'EXPECTED_APPROVAL=%s\n' "$EXPECTED_APPROVAL"
  printf 'PRODUCTION_HOST_CONTACTED=false\n'
  printf 'PRODUCTION_MUTATION=false\n'
  exit 0
fi
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

FILES=(
  scripts/run_v4_postgres_cache2g_batch100_ab.py
  scripts/report_v4_recorder_commit_lag.py
  scripts/report_v4_cache_bulk_lane_evidence.py
  docker-compose.prod.yml
)
PAYLOAD=""
for path in "${FILES[@]}"; do
  [[ -r "$ROOT/$path" ]] || fail "required_script_missing:$path"
  encoded="$(base64 < "$ROOT/$path" | tr -d '\n')"
  PAYLOAD="$PAYLOAD$path:$encoded"$'\n'
done
PAYLOAD_B64="$(printf '%s' "$PAYLOAD" | base64 | tr -d '\n')"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail
REPO=/opt/bp
tmp="$(mktemp -d /tmp/bp-v4-cache2g-batch100-ab.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

printf '%s' '__PAYLOAD_B64__' | base64 -d |
while IFS=: read -r path encoded; do
  [[ -n "$path" ]] || continue
  name="$(basename "$path")"
  printf '%s' "$encoded" | base64 -d > "$tmp/$name"
done
chmod 0644 "$tmp"/*.py

set +e
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src:$tmp"   "$REPO/.venv/bin/python" "$tmp/run_v4_postgres_cache2g_batch100_ab.py"   --repo "$REPO"   --env-file /etc/bp/bp.env   --safety-file /etc/bp/bp-prospective-runtime-safety.env   --expected-deployed-head __DEPLOYED_HEAD__   --helper-head __HELPER_HEAD__   --candidate-compose "$tmp/docker-compose.prod.yml"   --execute
rc=$?
set -e

if (( rc != 0 )); then
  exit "$rc"
fi
REMOTE_SCRIPT

REMOTE="$(printf '%s' "$REMOTE" | sed "s|__PAYLOAD_B64__|$PAYLOAD_B64|g")"
REMOTE="$(printf '%s' "$REMOTE" | sed "s|__DEPLOYED_HEAD__|$DEPLOYED_HEAD|g")"
REMOTE="$(printf '%s' "$REMOTE" | sed "s|__HELPER_HEAD__|$HELPER_HEAD|g")"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "EXPECTED_BATCH_SIZE=$EXPECTED_BATCH_SIZE"
echo "BASELINE_SHARED_BUFFERS=$BASELINE_SHARED_BUFFERS"
echo "CANDIDATE_SHARED_BUFFERS=$CANDIDATE_SHARED_BUFFERS"
echo "EXPERIMENT=2GB_SHARED_BUFFERS_AT_BATCH100_AB"
echo "ALWAYS_RESTORE_BASELINE=true"
echo "LIVE_TRADING_ENABLED=false"
echo "This experiment RESTARTS production PostgreSQL and the recorder/V3 chain TWICE; rollback cannot be guaranteed after catastrophic termination."
echo "It always restores the original environment and 128MB shared_buffers."

REMOTE_OUTPUT="$(mktemp)"
trap 'rm -f "$REMOTE_OUTPUT"' EXIT

set +e
printf '%s' "$REMOTE" |   gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='REMOTE_SCRIPT_PATH="$(mktemp /tmp/bp-v4-cache2g-batch100-ab.XXXXXX.sh)" && cat > "$REMOTE_SCRIPT_PATH" && sudo bash "$REMOTE_SCRIPT_PATH"; rc=$?; rm -f "$REMOTE_SCRIPT_PATH"; exit "$rc"'     2>&1 | tee "$REMOTE_OUTPUT"
rc=$?
set -e

if ! grep -Eq '^PHASE14_V4_PG_CACHE2G_BATCH100_AB_GATE=(PASS|FAIL)$' "$REMOTE_OUTPUT"; then
  fail "remote_terminal_marker_missing:rc=$rc"
fi

if (( rc != 0 )); then
  exit "$rc"
fi

grep -q '^PHASE14_V4_PG_CACHE2G_BATCH100_AB_GATE=PASS$' "$REMOTE_OUTPUT" ||
  fail "remote_experiment_did_not_pass"
