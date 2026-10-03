#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_PNL_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_PNL_ZONE:-us-east1-c}"
VM="${PHASE14_V4_PNL_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V4_PNL_ENV_FILE:-/etc/bp/bp.env}"

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_PNL_STATUS=FAIL:%s\n' "$1" >&2
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

REPORT="$ROOT/scripts/report_v4_fresh_book_pnl.py"
[[ -r "$REPORT" ]] || fail "report_script_missing"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'REPORT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="set -Eeuo pipefail
latest=\$(ls -1t /var/lib/bp/evidence/v4-fresh-book-shadow-*.jsonl 2>/dev/null | head -n1)
[[ -n \"\$latest\" ]] || { echo PHASE14_V4_FRESH_BOOK_PNL_STATUS=FAIL:evidence_missing >&2; exit 1; }
run_name=\$(basename \"\$latest\" .jsonl)
head_short=\${run_name##*-}
mapfile -t releases < <(find /var/lib/bp/runtime -maxdepth 1 -mindepth 1 -type d -name \"v4-source-time-fresh-book-shadow-\${head_short}*\" -print | sort)
(( \${#releases[@]} == 1 )) || { echo PHASE14_V4_FRESH_BOOK_PNL_STATUS=FAIL:runtime_source_ambiguous >&2; exit 1; }
release=\${releases[0]}
[[ -d \"\$release/src/bp_engine\" ]] || { echo PHASE14_V4_FRESH_BOOK_PNL_STATUS=FAIL:runtime_source_missing >&2; exit 1; }
tmp=\$(mktemp -d /tmp/bp-v4-pnl.XXXXXX)
trap 'rm -rf \"\$tmp\"' EXIT
chmod 0755 \"\$tmp\"
printf '%s' '$REPORT_B64' | base64 -d > \"\$tmp/report_v4_fresh_book_pnl.py\"
chmod 0644 \"\$tmp/report_v4_fresh_book_pnl.py\"
printf 'EVIDENCE_FILE=%s\\n' \"\$latest\"
printf 'RUNTIME_SOURCE=%s\\n' \"\$release\"
sudo -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=\"\$release/src\" \
  /opt/bp/.venv/bin/python \"\$tmp/report_v4_fresh_book_pnl.py\" \
  --env-file '$ENV_FILE' \
  --evidence-glob \"\$latest\""
