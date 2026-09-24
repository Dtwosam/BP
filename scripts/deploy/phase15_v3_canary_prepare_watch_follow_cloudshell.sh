#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"

fail() {
  echo "PHASE15_V3_CANARY_PERSISTENT_PREPARE_FOLLOW=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"

gcloud compute ssh "$US_VM" \
  --project="$PROJECT" \
  --zone="$US_ZONE" \
  --quiet \
  --command='sudo bash -s' <<'REMOTE' || fail "remote_follow_failed"
set -Eeuo pipefail
STATE_ROOT=/var/lib/bp/phase15-canary-prepare-watch
CURRENT_RUN=$STATE_ROOT/current-run
[[ -r "$CURRENT_RUN" ]] || { echo "CURRENT_RUN_MISSING"; exit 1; }
RUN_DIR=$(cat "$CURRENT_RUN")
STATUS_FILE=$RUN_DIR/status.json
LAST=''
while true; do
  if [[ ! -r "$STATUS_FILE" ]]; then
    echo "STATUS_WAITING_FOR_FILE"
    sleep 1
    continue
  fi
  CURRENT=$(sha256sum "$STATUS_FILE" | awk '{print $1}')
  if [[ "$CURRENT" != "$LAST" ]]; then
    cat "$STATUS_FILE"
    LAST=$CURRENT
  fi
  STATE=$(/opt/bp/.venv/bin/python - "$STATUS_FILE" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["status"])
PY
)
  if [[ "$STATE" != "running" && "$STATE" != "prepared_intent_persisted" ]]; then
    break
  fi
  if [[ "$STATE" == "prepared_intent_persisted" ]] && ! systemctl is-active --quiet bp-phase15-canary-prepare-watch.service; then
    break
  fi
  sleep 2
done
REMOTE

exec bash "$ROOT/scripts/deploy/phase15_v3_canary_prepare_watch_status_cloudshell.sh"
