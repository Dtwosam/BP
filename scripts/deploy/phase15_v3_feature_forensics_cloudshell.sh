#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_V3_FORENSICS_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_V3_FORENSICS_ZONE:-us-east1-c}"
VM="${PHASE15_V3_FORENSICS_VM:-bp-recorder}"
ENV_FILE="${PHASE15_V3_FORENSICS_ENV_FILE:-/etc/bp/bp.env}"

fail() {
  printf 'PHASE15_V3_FEATURE_FORENSICS=FAIL:%s\n' "$1" >&2
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

LEDGER_SCRIPT="$ROOT/scripts/report_v3_fresh_book_trades.py"
FORENSICS_SCRIPT="$ROOT/scripts/report_v3_feature_forensics.py"
[[ -r "$LEDGER_SCRIPT" ]] || fail "trade_ledger_script_missing"
[[ -r "$FORENSICS_SCRIPT" ]] || fail "forensics_script_missing"

LEDGER_B64="$(base64 < "$LEDGER_SCRIPT" | tr -d '\n')"
FORENSICS_B64="$(base64 < "$FORENSICS_SCRIPT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'REPORT_READ_ONLY=true\n'
printf 'V4_HOLDOUT_LABELS_READ=false\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="set -Eeuo pipefail
tmp=\$(mktemp -d /tmp/bp-v3-forensics.XXXXXX)
trap 'rm -rf \"\$tmp\"' EXIT
chmod 0755 \"\$tmp\"
printf '%s' '$LEDGER_B64' | base64 -d > \"\$tmp/report_v3_fresh_book_trades.py\"
printf '%s' '$FORENSICS_B64' | base64 -d > \"\$tmp/report_v3_feature_forensics.py\"
chmod 0644 \"\$tmp/\"*.py
cd \"\$tmp\"
sudo -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/bp/src \
  /opt/bp/.venv/bin/python report_v3_feature_forensics.py \
  --env-file '$ENV_FILE' \
  --evidence-glob '/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl'"
