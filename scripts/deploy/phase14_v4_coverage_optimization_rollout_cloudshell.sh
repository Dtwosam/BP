#!/usr/bin/env bash
set -Eeuo pipefail

# Separate controller for a separately authorized V4 coverage optimization rollout.
# Existing 2026-10-09 bounded rollout controller stays immutable for forensics.
# Default mode is local preflight: no VM connection and NO production mutation.
PROJECT=project-4397f2c0-7098-4c1c-abb
ZONE=us-east1-c
VM=bp-recorder
EXPECTED_DEPLOYED=a352c66ec0110925727bc40de767ee4ba981f965
OLD_RUNTIME_COMMIT=e7a21462374a1c19beae878c98ca319f13fb2d69
EXPECTED_OLD_TARGET=/var/lib/bp/runtime/v4-forward-$OLD_RUNTIME_COMMIT
EXPECTED_OLD_CODE_SHA256=''
HEAD_SHA="${PHASE14_V4_COVERAGE_ROLLOUT_HEAD:-}"
APPROVAL="${PHASE14_V4_COVERAGE_ROLLOUT_APPROVAL:-}"
PREFLIGHT_ONLY="${PHASE14_V4_COVERAGE_ROLLOUT_PREFLIGHT_ONLY:-true}"

fail() {
  echo "PHASE14_V4_COVERAGE_ROLLOUT_GATE=FAIL:$1" >&2
  exit 1
}
[[ "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "exact_head_required"
[[ "$PREFLIGHT_ONLY" == true || "$PREFLIGHT_ONLY" == false ]] ||
  fail "invalid_preflight_setting"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "repository_missing"
cd "$ROOT"
[[ "$(git branch --show-current)" == main ]] || fail "not_on_main"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "working_tree_dirty"
[[ "$(git rev-parse HEAD)" == "$HEAD_SHA" ]] || fail "local_head_changed"
[[ "$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')" == "$HEAD_SHA" ]] || fail "remote_main_changed"
grep -Fxq 'V4_FORWARD_MARKETS_PER_CYCLE = 1' \
  src/bp_engine/features/v4_forward.py || fail "candidate_is_not_bounded"
grep -Fq 'V4_FORWARD_STAGE=' \
  src/bp_engine/features/v4_forward_cli.py || fail "candidate_stage_logs_missing"
grep -Fq 'from bp_engine.features.v4_coverage import build_v4_forward_coverage_summary' \
  src/bp_engine/features/v4_forward.py || fail "optimized_summary_not_used"
grep -Fq 'def build_v4_forward_coverage_summary(' \
  src/bp_engine/features/v4_coverage.py || fail "optimized_summary_missing"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum_missing"
EXPECTED_OLD_CODE_SHA256="$(git show "$OLD_RUNTIME_COMMIT:src/bp_engine/features/v4_forward.py" | sha256sum | awk '{print $1}')"
[[ "$EXPECTED_OLD_CODE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "old_runtime_source_hash_invalid"
HOST_PATH=scripts/deploy/phase14_v4_coverage_optimization_rollout_host.sh
[[ -f "$HOST_PATH" ]] || fail "host_script_missing"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_COVERAGE_OPTIMIZATION:$HEAD_SHA:$EXPECTED_DEPLOYED:$EXPECTED_OLD_CODE_SHA256"
if [[ "$PREFLIGHT_ONLY" == true ]]; then
  echo 'PHASE14_V4_COVERAGE_ROLLOUT_PREFLIGHT=PASS'
  echo 'PRODUCTION_ROLLOUT_APPROVED=false'
  echo "CANDIDATE_MAIN=$HEAD_SHA"
  echo "EXPECTED_DEPLOYED_CHECKOUT=$EXPECTED_DEPLOYED"
  echo "EXPECTED_CURRENT_V4_RUNTIME=$EXPECTED_OLD_TARGET"
  echo "EXPECTED_APPROVAL=$EXPECTED_APPROVAL"
  echo 'PRODUCTION_HOST_CONTACTED=false'
  echo 'PRODUCTION_MUTATION=false'
  echo 'DATABASE_WRITES=false'
  echo 'ROLLOUT_AUTHORIZED=false'
  exit 0
fi
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "approval_mismatch"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"

WORKDIR="$(mktemp -d /tmp/bp-v4-controller.XXXXXXXX)"
REMOTE_DIR=''
cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "$REMOTE_DIR" ]]; then
    # Remove ONLY this exact ephemeral transfer directory; never remove a runtime.
    printf -v remote_q '%q' "$REMOTE_DIR"
    gcloud compute ssh "$VM" --quiet --project="$PROJECT" --zone="$ZONE" \
      --command="rm -f $remote_q/candidate.tar.gz $remote_q/host.sh && rmdir $remote_q" \
      >/dev/null 2>&1 || true
  fi
  rm -rf "$WORKDIR"
  exit "$status"
}
trap cleanup EXIT

# Local archive supplies commit bytes without fetching/mutating /opt/bp.
git archive --format=tar "$HEAD_SHA" | gzip -n >"$WORKDIR/candidate.tar.gz"
cp "$HOST_PATH" "$WORKDIR/host.sh"
ARCHIVE_HASH="$(sha256sum "$WORKDIR/candidate.tar.gz" | awk '{print $1}')"
HOST_HASH="$(sha256sum "$WORKDIR/host.sh" | awk '{print $1}')"

REMOTE_DIR="$(gcloud compute ssh "$VM" --quiet \
  --project="$PROJECT" --zone="$ZONE" \
  --command="mktemp -d /tmp/bp-v4-bounded-XXXXXXXX" | tail -n 1)"
[[ "$REMOTE_DIR" =~ ^/tmp/bp-v4-bounded-[A-Za-z0-9]+$ ]] ||
  fail "remote_transfer_dir_invalid"
gcloud compute scp --quiet \
  --project="$PROJECT" --zone="$ZONE" \
  "$WORKDIR/candidate.tar.gz" "$WORKDIR/host.sh" \
  "$VM:$REMOTE_DIR/"

printf -v host_q '%q' "$REMOTE_DIR/host.sh"
printf -v archive_q '%q' "$REMOTE_DIR/candidate.tar.gz"
printf -v head_q '%q' "$HEAD_SHA"
printf -v deployed_q '%q' "$EXPECTED_DEPLOYED"
printf -v old_q '%q' "$EXPECTED_OLD_TARGET"
printf -v oldhash_q '%q' "$EXPECTED_OLD_CODE_SHA256"
printf -v archivehash_q '%q' "$ARCHIVE_HASH"
printf -v hosthash_q '%q' "$HOST_HASH"
printf -v approval_q '%q' "$APPROVAL"

gcloud compute ssh "$VM" --quiet \
  --project="$PROJECT" --zone="$ZONE" \
  --command="sudo -n bash $host_q $archive_q $head_q $deployed_q $old_q $oldhash_q $archivehash_q $hosthash_q $approval_q"
