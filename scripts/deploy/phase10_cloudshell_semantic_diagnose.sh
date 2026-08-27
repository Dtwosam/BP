#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE10_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE10_ZONE:-us-east1-c}"
VM="${PHASE10_VM:-bp-recorder}"
PHASE_BRANCH="build/phase-10-live-prediction-engine"
DIAG_BRANCH="diag/phase10-semantic-hash-provenance-final"
EXPECTED_HEAD="${PHASE10_HEAD:-694d351ce3d210f67d1653c0e709835565fa9b89}"
DIAG_SHA="8f12bf50c109107d29fb2a691d6213e54a0e4ae1"
SOURCE_5M="phase9-300-c9f0e00eb7836af08008c66909f8f179"
SOURCE_15M="phase9-900-15c234f25588b23cce73a12f87a2e2ea"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run from Google Cloud Shell" >&2
  exit 2
fi
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

gcloud config set project "$PROJECT" >/dev/null
printf 'PROJECT=%s\nVM=%s\nZONE=%s\nPHASE10_HEAD=%s\nDIAG_SHA=%s\n' \
  "$PROJECT" "$VM" "$ZONE" "$EXPECTED_HEAD" "$DIAG_SHA"

REMOTE_SCRIPT=$(cat <<REMOTE
set -Eeuo pipefail
SHA='$EXPECTED_HEAD'
DIAG_SHA='$DIAG_SHA'
PHASE_BRANCH='$PHASE_BRANCH'
DIAG_BRANCH='$DIAG_BRANCH'
SOURCE_5M='$SOURCE_5M'
SOURCE_15M='$SOURCE_15M'
WT="/var/tmp/bp-phase10-diag-\${SHA:0:12}-\$\$"
DIAG_PY="/var/tmp/bp-phase10-semantic-diagnostic-\$\$.py"
cleanup() {
  rm -f "\$DIAG_PY" >/dev/null 2>&1 || true
  git -C /opt/bp worktree remove --force "\$WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/\$PHASE_BRANCH:refs/remotes/origin/\$PHASE_BRANCH" \
  "refs/heads/\$DIAG_BRANCH:refs/remotes/origin/\$DIAG_BRANCH"
FETCHED=\$(git -C /opt/bp rev-parse "refs/remotes/origin/\$PHASE_BRANCH")
if [[ "\$FETCHED" != "\$SHA" ]]; then
  echo "DIAGNOSTIC_STATUS=FAIL"
  echo "REASON=phase_candidate_head_changed"
  echo "EXPECTED_HEAD=\$SHA"
  echo "FETCHED_HEAD=\$FETCHED"
  exit 2
fi
if ! git -C /opt/bp merge-base --is-ancestor "\$DIAG_SHA" "refs/remotes/origin/\$DIAG_BRANCH"; then
  echo "DIAGNOSTIC_STATUS=FAIL"
  echo "REASON=diagnostic_commit_not_on_branch"
  exit 2
fi

git -C /opt/bp worktree prune
git -C /opt/bp worktree add --detach "\$WT" "\$SHA" >/dev/null
WORKTREE_HEAD=\$(git -C "\$WT" rev-parse HEAD)
if [[ "\$WORKTREE_HEAD" != "\$SHA" ]]; then
  echo "DIAGNOSTIC_STATUS=FAIL"
  echo "REASON=worktree_head_mismatch"
  exit 2
fi

git -C /opt/bp show \
  "\$DIAG_SHA:scripts/deploy/phase10_semantic_hash_diagnostic.py" \
  > "\$DIAG_PY"
chmod 644 "\$DIAG_PY"

sudo -u bp env PYTHONPATH="\$WT/src" /opt/bp/.venv/bin/python "\$DIAG_PY" \
  --env-file /etc/bp/bp.env \
  --source-calibration-run-id "\$SOURCE_5M" \
  --source-calibration-run-id "\$SOURCE_15M" \
  --expected-head "\$SHA"
echo "DIAGNOSTIC_STATUS=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
