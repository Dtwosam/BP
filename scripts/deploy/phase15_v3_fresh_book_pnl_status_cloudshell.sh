#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_V3_PNL_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_V3_PNL_ZONE:-us-east1-c}"
VM="${PHASE15_V3_PNL_VM:-bp-recorder}"
ENV_FILE="${PHASE15_V3_PNL_ENV_FILE:-/etc/bp/bp.env}"

fail() {
  printf 'PHASE15_V3_FRESH_BOOK_PNL_STATUS=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"

[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
git fetch origin main --quiet || fail "fetch_main_failed"
git switch main >/dev/null || fail "switch_main_failed"
git pull --ff-only origin main >/dev/null || fail "pull_main_failed"

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "main_head_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

REPORT_SCRIPT="$ROOT/scripts/report_v3_fresh_book_pnl.py"
[[ -r "$REPORT_SCRIPT" ]] || fail "report_script_missing"
REPORT_B64="$(base64 < "$REPORT_SCRIPT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'REPORT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REPORT_B64' | base64 -d | sudo -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/bp/src /opt/bp/.venv/bin/python - --env-file '$ENV_FILE' --evidence-glob '/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl'"
