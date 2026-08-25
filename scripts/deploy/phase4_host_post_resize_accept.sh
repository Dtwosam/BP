#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
BRANCH="${PHASE4_BRANCH:-build/phase-4-historical-backfill}"
HOST_REPO=/opt/bp
PY="$HOST_REPO/.venv/bin/python"
ENV_FILE=/etc/bp/bp.env
LOG=/var/lib/bp/evidence/phase4-host-acceptance-latest.log

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi

if [[ ! -x "$PY" ]]; then
  echo "missing host Python: $PY" >&2
  exit 2
fi

ROOT_DEV=$(findmnt -n -o SOURCE /)
ROOT_FS=$(findmnt -n -o FSTYPE /)
PARENT_NAME=$(lsblk -no PKNAME "$ROOT_DEV" | head -n 1 | tr -d '[:space:]')
PART_NUM=$(lsblk -no PARTN "$ROOT_DEV" | head -n 1 | tr -d '[:space:]')

if [[ -z "$PARENT_NAME" || -z "$PART_NUM" ]]; then
  echo "unable to determine root disk/partition for $ROOT_DEV" >&2
  exit 8
fi

PARENT_DEV="/dev/$PARENT_NAME"
echo "ROOT_DEVICE=$ROOT_DEV"
echo "ROOT_FILESYSTEM=$ROOT_FS"
echo "ROOT_PARENT=$PARENT_DEV"

if command -v growpart >/dev/null 2>&1; then
  growpart "$PARENT_DEV" "$PART_NUM" || true
fi

case "$ROOT_FS" in
  ext2|ext3|ext4)
    resize2fs "$ROOT_DEV"
    ;;
  xfs)
    xfs_growfs /
    ;;
  *)
    echo "unsupported root filesystem for automatic growth: $ROOT_FS" >&2
    exit 8
    ;;
esac

df -h /

REPORT=$(sudo -u bp "$PY" "$HOST_REPO/scripts/storage_maintenance.py" report --env-file "$ENV_FILE")
printf '%s\n' "$REPORT" > /var/lib/bp/evidence/phase4-storage-after-disk-resize.json
read -r DISK_STATUS DISK_FREE < <(
  printf '%s' "$REPORT" |
    "$PY" -c 'import json,sys; d=json.load(sys.stdin)["disk"]; print(d["status"], d["free_bytes"])'
)

echo "DISK_STATUS_AFTER_RESIZE=$DISK_STATUS"
echo "DISK_FREE_BYTES_AFTER_RESIZE=$DISK_FREE"
if [[ "$DISK_STATUS" != "ok" ]]; then
  echo "PHASE4_HOST_ACCEPTANCE=FAIL"
  echo "REASON=disk_not_ok_after_resize"
  exit 8
fi

git -C "$HOST_REPO" fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED=$(git -C "$HOST_REPO" rev-parse "refs/remotes/origin/$BRANCH")
if [[ "$FETCHED" != "$EXPECTED_HEAD" ]]; then
  echo "PHASE4_HOST_ACCEPTANCE=FAIL"
  echo "EXPECTED_HEAD=$EXPECTED_HEAD"
  echo "FETCHED_HEAD=$FETCHED"
  exit 2
fi

WT="/var/tmp/bp-phase4-${EXPECTED_HEAD:0:12}-$$"
git -C "$HOST_REPO" worktree prune
git -C "$HOST_REPO" worktree add --detach "$WT" "$EXPECTED_HEAD"
cleanup() {
  git -C "$HOST_REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

set +e
BP_REPO="$WT" bash "$WT/scripts/deploy/phase4_host_acceptance.sh" "$EXPECTED_HEAD" >"$LOG" 2>&1
RC=$?
set -e

if [[ "$RC" -ne 0 ]]; then
  echo "PHASE4_HOST_ACCEPTANCE=FAIL"
  echo "RC=$RC"
  echo "LOG=$LOG"
  tail -n 120 "$LOG"
  exit "$RC"
fi

SUMMARY=$(
  find /var/lib/bp/evidence/phase4-historical-backfill \
    -type f -name 'final-summary.txt' -printf '%T@ %p\n' |
    sort -nr | head -n 1 | cut -d' ' -f2-
)

echo "PHASE4_HOST_ACCEPTANCE=PASS"
cat "$SUMMARY"
