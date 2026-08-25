#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE6_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE6_ZONE:-us-east1-c}"
VM="${PHASE6_VM:-bp-recorder}"
BRANCH="${PHASE6_BRANCH:-build/phase-6-feature-engine}"
EXPECTED_HEAD="${PHASE6_HEAD:-}"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "PHASE6_HEAD must be set to the verified candidate SHA" >&2
  exit 2
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run this helper from Google Cloud Shell" >&2
  exit 2
fi

if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

gcloud config set project "$PROJECT" >/dev/null

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PHASE6_HEAD=$EXPECTED_HEAD"

REMOTE_SCRIPT=$(cat <<REMOTE
set -Eeuo pipefail
SHA='$EXPECTED_HEAD'
BRANCH='$BRANCH'
WT="/var/tmp/bp-phase6-\${SHA:0:12}-\$\$"
LOG=/var/lib/bp/evidence/phase6-host-acceptance-latest.log

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/\$BRANCH:refs/remotes/origin/\$BRANCH"
FETCHED=\$(git -C /opt/bp rev-parse "refs/remotes/origin/\$BRANCH")
if [[ "\$FETCHED" != "\$SHA" ]]; then
  echo "PHASE6_HOST_ACCEPTANCE=FAIL"
  echo "REASON=candidate_head_changed"
  echo "EXPECTED_HEAD=\$SHA"
  echo "FETCHED_HEAD=\$FETCHED"
  exit 2
fi

git -C /opt/bp worktree prune
git -C /opt/bp worktree add --detach "\$WT" "\$SHA"
cleanup() {
  git -C /opt/bp worktree remove --force "\$WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

set +e
BP_REPO="\$WT" bash "\$WT/scripts/deploy/phase6_host_acceptance.sh" "\$SHA" \
  2>&1 | tee "\$LOG"
RC=\${PIPESTATUS[0]}
set -e

if [[ "\$RC" -ne 0 ]]; then
  echo "PHASE6_HOST_ACCEPTANCE=FAIL"
  echo "RC=\$RC"
  echo "LOG=\$LOG"
  exit "\$RC"
fi

echo "PHASE6_HOST_ACCEPTANCE=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "Connecting to $VM for Phase 6 host acceptance..."
gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
