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
  ARCHIVE_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
fi
[[ "$ARCHIVE_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "archive_sha_invalid"

REMOTE_ARCHIVE="/tmp/bp-v4-fresh-book-shadow-${LOCAL_HEAD}.tar.gz"
gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet >/dev/null || fail "archive_upload_failed"

OUTPUT="$(
gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s -- '$REMOTE_ARCHIVE' '$LOCAL_HEAD' '$ARCHIVE_SHA' '$ENV_FILE' '$RUN_SECONDS' '$EXPECTED_MODEL_SHA256' '$EXPECTED_MODEL_SIZE_BYTES' '$EXPECTED_SKLEARN_VERSION' '$EXPECTED_XGBOOST_VERSION' '$EXPECTED_JOBLIB_VERSION'" <<'REMOTE'
set -Eeuo pipefail

archive="$1"
head="$2"
expected_archive_sha="$3"
env_file="$4"
run_seconds="$5"
expected_model_sha="$6"
expected_model_size="$7"
expected_sklearn_version="$8"
expected_xgboost_version="$9"
expected_joblib_version="${10}"
runtime_max_seconds=$((run_seconds + 60))

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_RUN=FAIL:%s\n' "$1" >&2
  exit 1
}

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
SOURCE_UNIT=bp-phase15-fast-live-source.service
RECORDER_UNIT=bp-recorder.service
POSTGRES_UNIT=bp-postgres.service
RUNTIME_ROOT=/var/lib/bp/runtime
EVIDENCE_ROOT=/var/lib/bp/evidence
release="$RUNTIME_ROOT/v4-source-time-fresh-book-shadow-$head"
model_target="$release/frozen-v4-model.joblib"
runtime_requirements="$release/deploy/phase14-v4-paper-runtime-requirements.txt"
venv="$RUNTIME_ROOT/v4-paper-venv-$head"
stage_tmp=""
venv_created=false
unit=""

cleanup_remote() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 )) && [[ -n "$unit" ]]; then
    systemctl stop "$unit" >/dev/null 2>&1 || true
  fi
  rm -f "$archive"
  [[ -n "$stage_tmp" ]] && rm -rf "$stage_tmp"
  if (( rc != 0 )) && [[ "$venv_created" == "true" ]]; then
    rm -rf "$venv"
  fi
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

find_frozen_model() {
  local root path size digest
  for root in /var/lib/bp/evidence /var/lib/bp/runtime /opt/bp; do
    [[ -d "$root" ]] || continue
    while IFS= read -r -d '' path; do
      [[ "$path" == "$model_target" ]] && continue
      size="$(stat -c '%s' "$path" 2>/dev/null || true)"
      [[ "$size" == "$expected_model_size" ]] || continue
      digest="$(sha256sum "$path" | awk '{print $1}')"
      if [[ "$digest" == "$expected_model_sha" ]]; then
        printf '%s\n' "$path"
        return 0
      fi
    done < <(
      find "$root" -xdev -type f -size "${expected_model_size}c" -print0 2>/dev/null |
        sort -z
    )
  done
  return 1
}

[[ "$head" =~ ^[0-9a-f]{40}$ ]] || fail "head_invalid"
[[ "$expected_archive_sha" =~ ^[0-9a-f]{64}$ ]] ||
  fail "expected_archive_sha_invalid"
[[ "$expected_model_sha" =~ ^[0-9a-f]{64}$ ]] ||
  fail "expected_model_sha_invalid"
[[ "$expected_model_size" =~ ^[0-9]+$ ]] || fail "expected_model_size_invalid"
[[ "$expected_sklearn_version" == "1.9.1" ]] || fail "expected_sklearn_version_invalid"
[[ "$expected_xgboost_version" == "3.4.1" ]] || fail "expected_xgboost_version_invalid"
[[ "$expected_joblib_version" == "1.5.3" ]] || fail "expected_joblib_version_invalid"
[[ "$run_seconds" =~ ^[0-9]+$ ]] || fail "run_seconds_invalid"
(( run_seconds >= 300 && run_seconds <= 86400 )) ||
  fail "run_seconds_out_of_authorized_range"
[[ "$env_file" == /* ]] || fail "env_file_not_absolute"
[[ -r "$archive" ]] || fail "archive_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"

actual_archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_archive_sha" == "$expected_archive_sha" ]] ||
  fail "archive_sha_mismatch"

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

require_zero_money_file "$env_file"
require_zero_money_file "$SAFETY_FILE"

if systemctl list-units --type=service --state=running --no-legend \
    'bp-v3-fresh-book-shadow-*' | grep -q .; then
  fail "v3_fresh_book_shadow_already_running"
fi
if systemctl list-units --type=service --state=running --no-legend \
    'bp-v4-fresh-book-shadow-*' | grep -q .; then
  fail "v4_fresh_book_shadow_already_running"
fi

source_model="$(find_frozen_model)" || fail "frozen_v4_model_artifact_not_found"
[[ -r "$source_model" ]] || fail "frozen_v4_model_artifact_unreadable"
[[ "$(stat -c '%s' "$source_model")" == "$expected_model_size" ]] ||
  fail "frozen_v4_model_size_mismatch"
[[ "$(sha256sum "$source_model" | awk '{print $1}')" == "$expected_model_sha" ]] ||
  fail "frozen_v4_model_sha_mismatch"

install -d -o root -g bp -m 0750 "$RUNTIME_ROOT"
install -d -o bp -g bp -m 0750 "$EVIDENCE_ROOT"

if [[ -e "$release" ]]; then
  [[ -d "$release" && ! -L "$release" ]] || fail "existing_release_invalid"
  [[ -f "$release/.release-head" ]] || fail "existing_release_head_marker_missing"
  [[ -f "$release/.archive-sha256" ]] ||
    fail "existing_release_sha_marker_missing"
  [[ "$(cat "$release/.release-head")" == "$head" ]] ||
    fail "existing_release_head_mismatch"
  [[ "$(cat "$release/.archive-sha256")" == "$expected_archive_sha" ]] ||
    fail "existing_release_sha_mismatch"
  [[ -f "$model_target" ]] || fail "existing_release_model_missing"
else
  stage_tmp="$(mktemp -d "$RUNTIME_ROOT/.v4-fresh-book-shadow-$head.XXXXXX")"
  tar -xzf "$archive" -C "$stage_tmp"
  printf '%s\n' "$head" > "$stage_tmp/.release-head"
  printf '%s\n' "$expected_archive_sha" > "$stage_tmp/.archive-sha256"
  install -o root -g bp -m 0640 "$source_model" "$stage_tmp/frozen-v4-model.joblib"
  chown -hR root:bp "$stage_tmp"
  find "$stage_tmp" -type d -exec chmod 0750 {} +
  find "$stage_tmp" -type f ! -name frozen-v4-model.joblib -exec chmod 0640 {} +
  mv "$stage_tmp" "$release"
  stage_tmp=""
fi

[[ "$(stat -c '%s' "$model_target")" == "$expected_model_size" ]] ||
  fail "staged_model_size_mismatch"
[[ "$(sha256sum "$model_target" | awk '{print $1}')" == "$expected_model_sha" ]] ||
  fail "staged_model_sha_mismatch"

[[ -r "$runtime_requirements" ]] || fail "paper_runtime_requirements_missing"
grep -Fxq "scikit-learn==$expected_sklearn_version" "$runtime_requirements" ||
  fail "paper_runtime_sklearn_pin_missing"
grep -Fxq "xgboost-cpu==$expected_xgboost_version" "$runtime_requirements" ||
  fail "paper_runtime_xgboost_pin_missing"
grep -Fxq "joblib==$expected_joblib_version" "$runtime_requirements" ||
  fail "paper_runtime_joblib_pin_missing"

if [[ -e "$venv" ]]; then
  [[ -d "$venv" && ! -L "$venv" && -x "$venv/bin/python" ]] ||
    fail "existing_paper_runtime_invalid"
  [[ -f "$venv/.ready" ]] || fail "existing_paper_runtime_not_ready"
  [[ -f "$venv/.release-head" && "$(cat "$venv/.release-head")" == "$head" ]] ||
    fail "existing_paper_runtime_head_mismatch"
  [[ -f "$venv/.sklearn-version" &&
      "$(cat "$venv/.sklearn-version")" == "$expected_sklearn_version" ]] ||
    fail "existing_paper_runtime_sklearn_mismatch"
  [[ -f "$venv/.xgboost-version" &&
      "$(cat "$venv/.xgboost-version")" == "$expected_xgboost_version" ]] ||
    fail "existing_paper_runtime_xgboost_mismatch"
  [[ -f "$venv/.joblib-version" &&
      "$(cat "$venv/.joblib-version")" == "$expected_joblib_version" ]] ||
    fail "existing_paper_runtime_joblib_mismatch"
else
  python3 -m venv "$venv" || fail "paper_runtime_venv_create_failed"
  venv_created=true
  "$venv/bin/python" -m pip install --disable-pip-version-check --no-input \
    -r "$runtime_requirements" || fail "paper_runtime_ml_install_failed"
  "$venv/bin/python" -m pip install --disable-pip-version-check --no-input \
    --constraint "$runtime_requirements" "$release" ||
    fail "paper_runtime_project_install_failed"
fi

"$venv/bin/python" -m pip check >/dev/null || fail "paper_runtime_pip_check_failed"

if ! runuser -u bp -- env \
  PYTHONNOUSERSITE=1 \
  "$venv/bin/python" - "$expected_sklearn_version" "$expected_xgboost_version" "$expected_joblib_version" <<'PY'
from importlib.metadata import version
import sys

import joblib
import sklearn
import xgboost

expected = {
    "scikit-learn": sys.argv[1],
    "xgboost": sys.argv[2],
    "joblib": sys.argv[3],
}
module_versions = {
    "scikit-learn": sklearn.__version__,
    "xgboost": xgboost.__version__,
    "joblib": joblib.__version__,
}
metadata_versions = {
    "scikit-learn": version("scikit-learn"),
    "xgboost": version("xgboost-cpu"),
    "joblib": version("joblib"),
}
if module_versions != expected:
    raise SystemExit(
        f"paper runtime module version mismatch: expected={expected} actual={module_versions}"
    )
if metadata_versions != expected:
    raise SystemExit(
        f"paper runtime metadata version mismatch: expected={expected} actual={metadata_versions}"
    )
PY
then
  fail "paper_runtime_version_validation_failed"
fi

runuser -u bp -- env \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$release/src" \
  "$venv/bin/python" - <<PY
from bp_engine.v4_paper.inference import (
    FROZEN_V4_MODEL_SHA256,
    load_frozen_v4_bundle,
)
from bp_engine.v4_paper.source_time_features import (
    MAX_SOURCE_AGE_SECONDS,
    V4_CORE_SOURCE_REQUIRED_FLAGS,
    V4_SOURCE_TIME_FEATURE_VERSION,
)
bundle = load_frozen_v4_bundle("$model_target")
assert FROZEN_V4_MODEL_SHA256 == "$expected_model_sha"
assert bundle["candidate"] == "full_v4_xgboost"
assert bundle["offset_seconds"] == 240
assert bundle["selected_min_edge"] == 0.05
assert V4_SOURCE_TIME_FEATURE_VERSION == "v4-source-time-features-v2"
assert len(V4_CORE_SOURCE_REQUIRED_FLAGS) == 12
assert MAX_SOURCE_AGE_SECONDS == 2.0
PY

if [[ "$venv_created" == "true" ]]; then
  printf '%s\n' "$head" > "$venv/.release-head"
  printf '%s\n' "$expected_sklearn_version" > "$venv/.sklearn-version"
  printf '%s\n' "$expected_xgboost_version" > "$venv/.xgboost-version"
  printf '%s\n' "$expected_joblib_version" > "$venv/.joblib-version"
  touch "$venv/.ready"
  venv_created=false
fi

runuser -u bp -- test -r "$release/scripts/run_v4_fresh_book_shadow.py"
runuser -u bp -- test -r "$release/src/bp_engine/v4_paper/inference.py"
runuser -u bp -- test -r "$release/src/bp_engine/v4_paper/source_time_features.py"
runuser -u bp -- test -r "$release/src/bp_engine/v4_paper/fresh_book_shadow.py"
runuser -u bp -- test -r "$release/src/bp_engine/execution/fast_live_book.py"
runuser -u bp -- test -r "$model_target"


run_id="v4-fresh-book-shadow-$(date -u +%Y%m%dT%H%M%SZ)-${head:0:12}"
unit="bp-$run_id.service"
output="$EVIDENCE_ROOT/$run_id.jsonl"
install -o bp -g bp -m 0640 /dev/null "$output"

systemd-run \
  --quiet \
  --collect \
  --unit="$unit" \
  --uid=bp \
  --gid=bp \
  --property="RuntimeMaxSec=${runtime_max_seconds}s" \
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
  --setenv=PYTHONNOUSERSITE=1 \
  --setenv="PYTHONPATH=$release/src" \
  "$venv/bin/python" \
  "$release/scripts/run_v4_fresh_book_shadow.py" \
  --model-path "$model_target" \
  --env-file "$env_file" \
  --run-seconds "$run_seconds" \
  --poll-seconds 0.10 \
  --quote-wait-seconds 1.0 \
  --quote-fresh-seconds 0.25 \
  --market-lookahead-seconds 600 \
  --max-decision-lag-seconds 2.0

startup_deadline=$((SECONDS + 60))
while ! grep -q '"event":"v4_fresh_book_shadow_started"' "$output"; do
  if ! systemctl is-active --quiet "$unit"; then
    printf '%s\n' '=== V4 SHADOW UNIT STATUS ===' >&2
    systemctl status "$unit" --no-pager >&2 || true
    printf '%s\n' '=== V4 SHADOW OUTPUT ===' >&2
    cat "$output" >&2 || true
    fail "v4_shadow_unit_exited_before_start_record"
  fi
  if (( SECONDS >= startup_deadline )); then
    printf '%s\n' '=== V4 SHADOW UNIT STATUS ===' >&2
    systemctl status "$unit" --no-pager >&2 || true
    printf '%s\n' '=== V4 SHADOW OUTPUT ===' >&2
    cat "$output" >&2 || true
    fail "v4_shadow_start_record_timeout"
  fi
  sleep 1
done

systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_reactivated"
grep -q '"database_read_only":true' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_read_only_record_missing"
}
grep -q '"order_submission_enabled":false' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_money_disabled_record_missing"
}
grep -q '"holdout_labels_read":false' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_holdout_safety_record_missing"
}
grep -q '"model_refit_performed":false' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_model_freeze_record_missing"
}
grep -q '"threshold_tuning_performed":false' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_threshold_freeze_record_missing"
}
grep -q '"core_source_policy":"require_market_start_and_current_all_venues"' "$output" || {
  cat "$output" >&2 || true
  fail "v4_shadow_core_source_policy_record_missing"
}

printf 'PHASE14_V4_FRESH_BOOK_SHADOW_RUN=PASS\n'
printf 'CONTROL_MAIN=%s\n' "$head"
printf 'ARCHIVE_SHA256=%s\n' "$expected_archive_sha"
printf 'RUNTIME=%s\n' "$release"
printf 'RUN_ID=%s\n' "$run_id"
printf 'UNIT=%s\n' "$unit"
printf 'OUTPUT=%s\n' "$output"
printf 'RUN_SECONDS=%s\n' "$run_seconds"
printf 'RUNTIME_MAX_SECONDS=%s\n' "$runtime_max_seconds"
printf 'SOURCE_MODEL_PATH=%s\n' "$source_model"
printf 'STAGED_MODEL_PATH=%s\n' "$model_target"
printf 'MODEL_SHA256=%s\n' "$expected_model_sha"
printf 'MODEL_SIZE_BYTES=%s\n' "$expected_model_size"
printf 'PAPER_RUNTIME=%s\n' "$venv"
printf 'SCIKIT_LEARN_VERSION=%s\n' "$expected_sklearn_version"
printf 'XGBOOST_VERSION=%s\n' "$expected_xgboost_version"
printf 'JOBLIB_VERSION=%s\n' "$expected_joblib_version"
printf 'SOURCE_FEATURE_VERSION=v4-source-time-features-v2\n'
printf 'CORE_SOURCE_POLICY=require_market_start_and_current_all_venues\n'
printf 'MAX_BTC_SOURCE_AGE_SECONDS=2.0\n'
printf 'MAX_BTC_FUTURE_SKEW_SECONDS=1.0\n'
printf 'MAX_DECISION_LAG_SECONDS=2.0\n'
printf 'QUOTE_FRESH_SECONDS=0.25\n'
printf 'TARGET_NOTIONAL_USD=5.00\n'
printf 'FROZEN_MIN_EDGE=0.05\n'
printf 'FAST_LIVE_SOURCE_ACTIVE=false\n'
printf 'FAST_LIVE_SOURCE_ENABLED=false\n'
printf 'DATABASE_ACCESS=read_only\n'
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'HOLDOUT_LABELS_READ=false\n'
printf 'MODEL_REFIT_PERFORMED=false\n'
printf 'THRESHOLD_TUNING_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_ENABLED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'WALLET_MATERIAL_LOADED=false\n'
REMOTE
)" || {
  printf '%s\n' "$OUTPUT" >&2
  fail "remote_stage_or_start_failed"
}

printf '%s\n' "$OUTPUT"
