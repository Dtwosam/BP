#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_DEDUPE_PK_REINDEX_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_DEDUPE_PK_REINDEX_ZONE:-us-east1-c}"
VM="${PHASE14_V4_DEDUPE_PK_REINDEX_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V4_DEDUPE_PK_REINDEX_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V4_DEDUPE_PK_REINDEX_DEPLOYED_HEAD:-52b4355d6f077373b873f7a6f42bc37a20ddbc7b}"
APPROVAL="${PHASE14_V4_DEDUPE_PK_REINDEX_APPROVAL:-}"

fail() {
  echo "PHASE14_V4_DEDUPE_PK_REINDEX_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "deployed_head_invalid"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail "remote_main_changed"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_DEDUPE_PK_REINDEX:$HELPER_HEAD:$DEPLOYED_HEAD"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

FILES=(
  scripts/run_v4_dedupe_pk_reindex_rollout.py
  scripts/reindex_v4_dedupe_primary_indexes.py
  scripts/report_v4_dedupe_reindex_readiness.py
  scripts/report_v4_dedupe_index_health.py
  scripts/report_v4_recorder_commit_lag.py
)
PAYLOAD=""
for path in "${FILES[@]}"; do
  [[ -r "$ROOT/$path" ]] || fail "required_script_missing:$path"
  encoded="$(base64 < "$ROOT/$path" | tr -d '\n')"
  PAYLOAD+="$path:$encoded"$'\n'
done
PAYLOAD_B64="$(printf '%s' "$PAYLOAD" | base64 | tr -d '\n')"

printf -v DEPLOYED_Q '%q' "$DEPLOYED_HEAD"
printf -v HELPER_Q '%q' "$HELPER_HEAD"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail
REPO=/opt/bp
tmp="$(mktemp -d /tmp/bp-v4-dedupe-pk-reindex.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
printf '%s' '__PAYLOAD_B64__' | base64 -d | while IFS=: read -r path encoded; do
  name="${path##*/}"
  printf '%s' "$encoded" | base64 -d > "$tmp/$name"
done
chmod 0644 "$tmp"/*.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src:$tmp" \
  "$REPO/.venv/bin/python" "$tmp/run_v4_dedupe_pk_reindex_rollout.py" \
  --repo "$REPO" \
  --env-file /etc/bp/bp.env \
  --safety-file /etc/bp/bp-prospective-runtime-safety.env \
  --expected-deployed-head __DEPLOYED_HEAD__ \
  --helper-head __HELPER_HEAD__ \
  --execute
REMOTE_SCRIPT

REMOTE="${REMOTE/__PAYLOAD_B64__/$PAYLOAD_B64}"
REMOTE="${REMOTE/__DEPLOYED_HEAD__/$DEPLOYED_Q}"
REMOTE="${REMOTE/__HELPER_HEAD__/$HELPER_Q}"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "OPERATION=REINDEX_INDEX_CONCURRENTLY_16_DEDUPE_PRIMARY_KEYS"
echo "RECORDER_REMAINS_ACTIVE=true"
echo "V3_REMAINS_ACTIVE=true"
echo "MAINTENANCE_TIMER_TEMPORARILY_QUIESCED=true"
echo "LIVE_TRADING_ENABLED=false"
echo "This helper mutates production PostgreSQL indexes. Exact fresh approval is required."

REMOTE_OUTPUT="$(mktemp)"
set +e
printf '%s' "$REMOTE" | \
  gcloud compute ssh "$VM" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --quiet \
    --command='REMOTE_SCRIPT_PATH="$(mktemp /tmp/bp-v4-dedupe-pk-reindex.XXXXXX.sh)" && cat > "$REMOTE_SCRIPT_PATH" && sudo bash "$REMOTE_SCRIPT_PATH"; rc=$?; rm -f "$REMOTE_SCRIPT_PATH"; exit "$rc"' \
    2>&1 | tee "$REMOTE_OUTPUT"
PIPE_RC=("${PIPESTATUS[@]}")
set -e

STREAM_RC="${PIPE_RC[0]}"
GCLOUD_RC="${PIPE_RC[1]}"
TEE_RC="${PIPE_RC[2]}"

TERMINAL_MARKER_PRESENT=false
if grep -Eq '^PHASE14_V4_DEDUPE_PK_REINDEX_GATE=(PASS|FAIL)$' "$REMOTE_OUTPUT"; then
  TERMINAL_MARKER_PRESENT=true
fi

if [[ "$TERMINAL_MARKER_PRESENT" != "true" ]]; then
  rm -f "$REMOTE_OUTPUT"
  fail "remote_terminal_marker_missing:stream_rc=$STREAM_RC:gcloud_rc=$GCLOUD_RC:tee_rc=$TEE_RC"
fi

rm -f "$REMOTE_OUTPUT"

if (( GCLOUD_RC != 0 )); then
  exit "$GCLOUD_RC"
fi
if (( STREAM_RC != 0 )); then
  fail "remote_script_stream_failed:rc=$STREAM_RC"
fi
if (( TEE_RC != 0 )); then
  fail "remote_output_capture_failed:rc=$TEE_RC"
fi
