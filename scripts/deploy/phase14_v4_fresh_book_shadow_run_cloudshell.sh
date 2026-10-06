#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_FRESH_BOOK_SHADOW_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_FRESH_BOOK_SHADOW_ZONE:-us-east1-c}"
VM="${PHASE14_V4_FRESH_BOOK_SHADOW_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V4_FRESH_BOOK_SHADOW_ENV_FILE:-/etc/bp/bp.env}"
RUN_SECONDS="${PHASE14_V4_FRESH_BOOK_SHADOW_RUN_SECONDS:-86400}"
EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
EXPECTED_MODEL_SIZE_BYTES=230132
EXPECTED_SKLEARN_VERSION="1.9.1"
EXPECTED_XGBOOST_VERSION="3.4.1"
EXPECTED_JOBLIB_VERSION="1.5.3"
PREFLIGHT_ONLY="${PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT_ONLY:-false}"

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_RUN=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"

[[ "$RUN_SECONDS" =~ ^[0-9]+$ ]] || fail "run_seconds_invalid"
(( RUN_SECONDS >= 300 && RUN_SECONDS <= 86400 )) ||
  fail "run_seconds_out_of_authorized_range"

[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"

CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "main" ]] || fail "local_branch_not_main"

git fetch origin main --quiet || fail "fetch_main_failed"

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
if [[ "$LOCAL_HEAD" != "$REMOTE_MAIN" ]]; then
  printf 'LOCAL_HEAD=%s\n' "$LOCAL_HEAD" >&2
  printf 'REMOTE_MAIN=%s\n' "$REMOTE_MAIN" >&2
  fail "local_main_stale_update_before_run"
fi

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW:${LOCAL_HEAD}:${EXPECTED_MODEL_SHA256}:${EXPECTED_SKLEARN_VERSION}:${EXPECTED_XGBOOST_VERSION}:${EXPECTED_JOBLIB_VERSION}"
case "$PREFLIGHT_ONLY" in
  true)
    printf 'PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT=PASS\n'
    printf 'CANDIDATE_MAIN=%s\n' "$LOCAL_HEAD"
    printf 'MODEL_SHA256=%s\n' "$EXPECTED_MODEL_SHA256"
    printf 'SCIKIT_LEARN_VERSION=%s\n' "$EXPECTED_SKLEARN_VERSION"
    printf 'XGBOOST_VERSION=%s\n' "$EXPECTED_XGBOOST_VERSION"
    printf 'JOBLIB_VERSION=%s\n' "$EXPECTED_JOBLIB_VERSION"
    printf 'EXPECTED_APPROVAL=%s\n' "$EXPECTED_APPROVAL"
    printf 'PRODUCTION_HOST_CONTACTED=false\n'
    printf 'PRODUCTION_MUTATION_PERFORMED=false\n'
    printf 'PAPER_ACTIVATION_PERFORMED=false\n'
    printf 'LIVE_TRADING_ENABLED=false\n'
    printf 'REAL_MONEY_USD=0\n'
    exit 0
    ;;
  false)
    ;;
  *)
    fail "preflight_only_invalid"
    ;;
esac

[[ "${PHASE14_V4_FRESH_BOOK_SHADOW_APPROVAL:-}" == "$EXPECTED_APPROVAL" ]] ||
  fail "explicit_zero_money_paper_shadow_approval_missing_or_mismatched"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

ARCHIVE="$(mktemp /tmp/bp-v4-fresh-book-shadow.XXXXXX.tar.gz)"
cleanup() {
  rm -f "$ARCHIVE"
}
trap cleanup EXIT

git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD"

if command -v sha256sum >/dev/null 2>&1; then
  ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
else