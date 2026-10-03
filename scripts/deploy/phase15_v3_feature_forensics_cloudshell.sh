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

for path in \
  scripts/report_v3_fresh_book_trades.py \
  scripts/report_v3_feature_forensics.py \
  src/bp_engine/features/v3_models.py \
  src/bp_engine/features/v3_service.py \
  src/bp_engine/v3_paper/service.py; do
  git cat-file -e "$LOCAL_HEAD:$path" || fail "source_path_missing:$path"
done

ARCHIVE="$(mktemp /tmp/bp-v3-feature-forensics.XXXXXX.tar.gz)"
cleanup() {
  rm -f "$ARCHIVE"
}
trap cleanup EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD"

if command -v sha256sum >/dev/null 2>&1; then
  ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
else
  ARCHIVE_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
fi
[[ "$ARCHIVE_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "archive_sha_invalid"

REMOTE_ARCHIVE="/tmp/bp-v3-feature-forensics-${LOCAL_HEAD}.tar.gz"
gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet >/dev/null || fail "archive_upload_failed"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'ARCHIVE_SHA256=%s\n' "$ARCHIVE_SHA"
printf 'REPORT_READ_ONLY=true\n'
printf 'V4_HOLDOUT_LABELS_READ=false\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="bash -s -- '$REMOTE_ARCHIVE' '$LOCAL_HEAD' '$ARCHIVE_SHA' '$ENV_FILE'" <<'REMOTE'
set -Eeuo pipefail

archive="$1"
head="$2"
expected_archive_sha="$3"
env_file="$4"
tmp=""

cleanup_remote() {
  local rc=$?
  trap - EXIT
  set +e
  [[ -n "$tmp" ]] && rm -rf "$tmp"
  rm -f "$archive"
  exit "$rc"
}
trap cleanup_remote EXIT

fail() {
  printf 'PHASE15_V3_FEATURE_FORENSICS=FAIL:%s\n' "$1" >&2
  exit 1
}

[[ "$head" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$expected_archive_sha" =~ ^[0-9a-f]{64}$ ]] ||
  fail "expected_archive_sha_invalid"
[[ "$env_file" == /* ]] || fail "env_file_not_absolute"
[[ -r "$archive" ]] || fail "archive_missing"
[[ -x /opt/bp/.venv/bin/python ]] || fail "production_python_missing"

actual_archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_archive_sha" == "$expected_archive_sha" ]] ||
  fail "archive_sha_mismatch"

tmp="$(mktemp -d /tmp/bp-v3-forensics.XXXXXX)"
repo="$tmp/repo"
mkdir -p "$repo"
tar -xzf "$archive" -C "$repo"
chmod -R a+rX "$repo"

for path in \
  scripts/report_v3_fresh_book_trades.py \
  scripts/report_v3_feature_forensics.py \
  src/bp_engine/features/v3_models.py \
  src/bp_engine/features/v3_service.py \
  src/bp_engine/v3_paper/service.py; do
  [[ -r "$repo/$path" ]] || fail "staged_source_missing:$path"
done

sudo -u bp env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$repo/src" \
  /opt/bp/.venv/bin/python "$repo/scripts/report_v3_feature_forensics.py" \
  --env-file "$env_file" \
  --evidence-glob '/var/lib/bp/evidence/v3-fresh-book-shadow-*.jsonl'
REMOTE
