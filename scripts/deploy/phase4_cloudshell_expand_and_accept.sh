#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE4_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE4_ZONE:-us-east1-c}"
VM="${PHASE4_VM:-bp-recorder}"
TARGET_GB="${PHASE4_TARGET_GB:-200}"
BRANCH="${PHASE4_BRANCH:-build/phase-4-historical-backfill}"
EXPECTED_HEAD="${PHASE4_HEAD:-}"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "PHASE4_HEAD must be set to the verified candidate SHA" >&2
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

DISK_URI=$(
  gcloud compute instances describe "$VM" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --format='get(disks[0].source)'
)
if [[ -z "$DISK_URI" ]]; then
  echo "unable to resolve boot disk for $VM" >&2
  exit 2
fi
DISK="${DISK_URI##*/}"
CURRENT_GB=$(
  gcloud compute disks describe "$DISK" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --format='get(sizeGb)'
)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "BOOT_DISK=$DISK"
echo "CURRENT_DISK_GB=$CURRENT_GB"
echo "TARGET_DISK_GB=$TARGET_GB"
echo "PHASE4_HEAD=$EXPECTED_HEAD"

if (( CURRENT_GB < TARGET_GB )); then
  SNAP="${VM}-pre-phase4-resize-$(date -u +%Y%m%dt%H%M%Sz)"
  echo "Creating safety snapshot: $SNAP"
  gcloud compute disks snapshot "$DISK" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --snapshot-names="$SNAP" \
    --quiet

  echo "Resizing $DISK to ${TARGET_GB}GB"
  gcloud compute disks resize "$DISK" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --size="${TARGET_GB}GB" \
    --quiet
else
  echo "Boot disk already at least ${TARGET_GB}GB; resize skipped."
fi

REMOTE_SCRIPT=$(cat <<REMOTE
set -Eeuo pipefail
git -C /opt/bp fetch --no-tags origin 'refs/heads/$BRANCH:refs/remotes/origin/$BRANCH'
FETCHED=\$(git -C /opt/bp rev-parse 'refs/remotes/origin/$BRANCH')
if [[ "\$FETCHED" != '$EXPECTED_HEAD' ]]; then
  echo 'PHASE4_HOST_ACCEPTANCE=FAIL'
  echo 'REASON=candidate_head_changed'
  echo "EXPECTED_HEAD=$EXPECTED_HEAD"
  echo "FETCHED_HEAD=\$FETCHED"
  exit 2
fi
git -C /opt/bp show '$EXPECTED_HEAD:scripts/deploy/phase4_host_post_resize_accept.sh' | env PHASE4_BRANCH='$BRANCH' bash -s -- '$EXPECTED_HEAD'
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "Connecting to $VM for filesystem growth and Phase 4 acceptance..."
gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
