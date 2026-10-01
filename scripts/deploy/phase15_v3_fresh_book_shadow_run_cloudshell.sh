#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_V3_FRESH_BOOK_SHADOW_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_V3_FRESH_BOOK_SHADOW_ZONE:-us-east1-c}"
VM="${PHASE15_V3_FRESH_BOOK_SHADOW_VM:-bp-recorder}"
ENV_FILE="${PHASE15_V3_FRESH_BOOK_SHADOW_ENV_FILE:-/etc/bp/bp.env}"
RUN_SECONDS="${PHASE15_V3_FRESH_BOOK_SHADOW_RUN_SECONDS:-43200}"

fail() {
  printf 'PHASE15_V3_FRESH_BOOK_SHADOW_RUN=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"

[[ "$RUN_SECONDS" =~ ^[0-9]+$ ]] || fail "run_seconds_invalid"
(( RUN_SECONDS >= 300 && RUN_SECONDS <= 43200 )) || fail "run_seconds_out_of_authorized_range"

[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
git fetch origin main --quiet || fail "fetch_main_failed"
git switch main >/dev/null || fail "switch_main_failed"
git pull --ff-only origin main >/dev/null || fail "pull_main_failed"

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "main_head_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

ARCHIVE="$(mktemp /tmp/bp-v3-fresh-book-shadow.XXXXXX.tar.gz)"
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

REMOTE_ARCHIVE="/tmp/bp-v3-fresh-book-shadow-${LOCAL_HEAD}.tar.gz"
gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet >/dev/null || fail "archive_upload_failed"

OUTPUT="$(
gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s -- '$REMOTE_ARCHIVE' '$LOCAL_HEAD' '$ARCHIVE_SHA' '$ENV_FILE' '$RUN_SECONDS'" <<'REMOTE'
set -Eeuo pipefail

archive="$1"
head="$2"
expected_sha="$3"
env_file="$4"
run_seconds="$5"

fail() {
  printf 'PHASE15_V3_FRESH_BOOK_SHADOW_RUN=FAIL:%s\n' "$1" >&2
  exit 1
}

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
SOURCE_UNIT=bp-phase15-fast-live-source.service
RECORDER_UNIT=bp-recorder.service
POSTGRES_UNIT=bp-postgres.service
PREDICTOR_UNIT=bp-v3-frozen-predictor.service
RUNTIME_ROOT=/var/lib/bp/runtime
EVIDENCE_ROOT=/var/lib/bp/evidence
release="$RUNTIME_ROOT/v3-fresh-book-shadow-$head"
stage_tmp=""

cleanup_remote() {
  local rc=$?
  trap - EXIT
  set +e
  rm -f "$archive"
  [[ -n "$stage_tmp" ]] && rm -rf "$stage_tmp"
  exit "$rc"
}
trap cleanup_remote EXIT

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_zero_money_file() {
  local path=$1 mode live trade loss
  [[ -r "$path" ]] || fail "safety_file_missing:$path"
  mode="$(read_env "$path" MODE)"
  live="$(read_env "$path" LIVE_TRADING_ENABLED)"
  trade="$(read_env "$path" MAX_TRADE_SIZE_USD)"
  loss="$(read_env "$path" MAX_DAILY_LOSS_USD)"
  [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
  [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
  [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
  [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
}

[[ "$head" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fail "expected_sha_invalid"
[[ "$run_seconds" =~ ^[0-9]+$ ]] || fail "run_seconds_invalid"
(( run_seconds >= 300 && run_seconds <= 43200 )) || fail "run_seconds_out_of_authorized_range"
[[ "$env_file" == /* ]] || fail "env_file_not_absolute"
[[ -r "$archive" ]] || fail "archive_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"

actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha" == "$expected_sha" ]] || fail "archive_sha_mismatch"

systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_active"
systemctl is-enabled --quiet "$SOURCE_UNIT" && fail "fast_live_source_enabled"
if ps -eo comm=,args= | awk '
  $1 ~ /^python/ && $0 ~ /run_phase15_v3_fast_live_source[.]py/ { found=1 }
  END { exit found ? 0 : 1 }
'; then
  fail "fast_live_source_process_present"
fi

systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active"
systemctl is-active --quiet "$POSTGRES_UNIT" || fail "postgres_not_active"
systemctl is-active --quiet "$PREDICTOR_UNIT" || fail "v3_predictor_not_active"

require_zero_money_file "$env_file"
require_zero_money_file "$SAFETY_FILE"

if systemctl list-units --type=service --state=running --no-legend \
    'bp-v3-fresh-book-shadow-*' | grep -q .; then
  fail "fresh_book_shadow_already_running"
fi

install -d -o root -g bp -m 0750 "$RUNTIME_ROOT"
install -d -o bp -g bp -m 0750 "$EVIDENCE_ROOT"

if [[ -e "$release" ]]; then
  [[ -d "$release" && ! -L "$release" ]] || fail "existing_release_invalid"
  [[ -f "$release/.release-head" ]] || fail "existing_release_head_marker_missing"
  [[ -f "$release/.archive-sha256" ]] || fail "existing_release_sha_marker_missing"
  [[ "$(cat "$release/.release-head")" == "$head" ]] || fail "existing_release_head_mismatch"
  [[ "$(cat "$release/.archive-sha256")" == "$expected_sha" ]] ||
    fail "existing_release_sha_mismatch"
else
  stage_tmp="$(mktemp -d "$RUNTIME_ROOT/.v3-fresh-book-shadow-$head.XXXXXX")"
  tar -xzf "$archive" -C "$stage_tmp"
  printf '%s\n' "$head" > "$stage_tmp/.release-head"
  printf '%s\n' "$expected_sha" > "$stage_tmp/.archive-sha256"
  chown -hR root:bp "$stage_tmp"
  find "$stage_tmp" -type d -exec chmod 0750 {} +
  find "$stage_tmp" -type f -exec chmod 0640 {} +
  mv "$stage_tmp" "$release"
  stage_tmp=""
fi

runuser -u bp -- test -r "$release/scripts/run_v3_fresh_book_shadow.py"
runuser -u bp -- test -r "$release/src/bp_engine/v3_paper/fresh_book_shadow.py"
runuser -u bp -- test -r "$release/src/bp_engine/execution/fast_live_book.py"

runuser -u bp -- env \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$release/src" \
  "$REPO/.venv/bin/python" - <<'PY'
from bp_engine.execution.fast_live_book import StreamingBookCache
from bp_engine.v3_paper.fresh_book_shadow import V3_FRESH_BOOK_SHADOW_VERSION
assert V3_FRESH_BOOK_SHADOW_VERSION == "paper-execution-v3-fresh-book-shadow-v1"
assert StreamingBookCache is not None
PY

run_id="v3-fresh-book-shadow-$(date -u +%Y%m%dT%H%M%SZ)-${head:0:12}"
unit="bp-$run_id.service"
output="$EVIDENCE_ROOT/$run_id.jsonl"
install -o bp -g bp -m 0640 /dev/null "$output"

systemd-run \
  --quiet \
  --collect \
  --unit="$unit" \
  --uid=bp \
  --gid=bp \
  --property="RuntimeMaxSec=${run_seconds}s" \
  --property=NoNewPrivileges=true \
  --property=PrivateTmp=true \
  --property=ProtectHome=true \
  --property=ProtectSystem=full \
  --property=RestrictSUIDSGID=true \
  --property="StandardOutput=append:$output" \
  --property="StandardError=append:$output" \
  --setenv=MODE=research \
  --setenv=LIVE_TRADING_ENABLED=false \
  --setenv=MAX_TRADE_SIZE_USD=0 \
  --setenv=MAX_DAILY_LOSS_USD=0 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv="PYTHONPATH=$release/src" \
  "$REPO/.venv/bin/python" \
  "$release/scripts/run_v3_fresh_book_shadow.py" \
  --env-file "$env_file" \
  --run-seconds "$run_seconds" \
  --poll-seconds 0.10 \
  --quote-wait-seconds 1.0 \
  --quote-fresh-seconds 0.25 \
  --market-lookahead-seconds 600

startup_deadline=$((SECONDS + 60))
while ! grep -q '"event":"fresh_book_shadow_started"' "$output"; do
  if ! systemctl is-active --quiet "$unit"; then
    printf '%s\n' '=== SHADOW UNIT STATUS ===' >&2
    systemctl status "$unit" --no-pager >&2 || true
    printf '%s\n' '=== SHADOW OUTPUT ===' >&2
    cat "$output" >&2 || true
    fail "shadow_unit_exited_before_start_record"
  fi
  if (( SECONDS >= startup_deadline )); then
    printf '%s\n' '=== SHADOW UNIT STATUS ===' >&2
    systemctl status "$unit" --no-pager >&2 || true
    printf '%s\n' '=== SHADOW OUTPUT ===' >&2
    cat "$output" >&2 || true
    fail "shadow_start_record_timeout"
  fi
  sleep 1
done

systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_reactivated"
grep -q '"database_read_only":true' "$output" || {
  cat "$output" >&2 || true
  fail "shadow_read_only_record_missing"
}
grep -q '"order_submission_enabled":false' "$output" || {
  cat "$output" >&2 || true
  fail "shadow_money_disabled_record_missing"
}

printf 'PHASE15_V3_FRESH_BOOK_SHADOW_RUN=PASS\n'
printf 'CONTROL_MAIN=%s\n' "$head"
printf 'ARCHIVE_SHA256=%s\n' "$expected_sha"
printf 'RUNTIME=%s\n' "$release"
printf 'RUN_ID=%s\n' "$run_id"
printf 'UNIT=%s\n' "$unit"
printf 'OUTPUT=%s\n' "$output"
printf 'RUN_SECONDS=%s\n' "$run_seconds"
printf 'FAST_LIVE_SOURCE_ACTIVE=false\n'
printf 'FAST_LIVE_SOURCE_ENABLED=false\n'
printf 'DATABASE_ACCESS=read_only\n'
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_ENABLED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'WALLET_MATERIAL_LOADED=false\n'
REMOTE
)" || {
  printf '%s\n' "$OUTPUT" >&2
  fail "remote_stage_or_start_failed"
}

printf '%s\n' "$OUTPUT"
