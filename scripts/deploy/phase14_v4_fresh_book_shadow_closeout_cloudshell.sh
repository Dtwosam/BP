#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_CLOSEOUT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"\nZONE="${PHASE14_V4_CLOSEOUT_ZONE:-us-east1-c}"\nVM="${PHASE14_V4_CLOSEOUT_VM:-bp-recorder}"\nENV_FILE="${PHASE14_V4_CLOSEOUT_ENV_FILE:-/etc/bp/bp.env}"

EXPECTED_RUN_MAIN="f5c76576619c35e65fc8a317d47c8a31dd263950"
EXPECTED_RUN_ID="v4-fresh-book-shadow-20261006T140323Z-f5c76576619c"
EXPECTED_UNIT="bp-$EXPECTED_RUN_ID.service"
EXPECTED_EVIDENCE="/var/lib/bp/evidence/$EXPECTED_RUN_ID.jsonl"
EXPECTED_RELEASE="/var/lib/bp/runtime/v4-source-time-fresh-book-shadow-$EXPECTED_RUN_MAIN"
EXPECTED_VENV="/var/lib/bp/runtime/v4-paper-venv-$EXPECTED_RUN_MAIN"
EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
EXPECTED_RUN_SECONDS=86400
DURATION_TOLERANCE_SECONDS=120

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_CLOSEOUT=FAIL:%s\n' "$1" >&2
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

VERIFY="$ROOT/scripts/verify_v4_fresh_book_shadow_closeout.py"
REPORT="$ROOT/scripts/report_v4_fresh_book_pnl.py"
[[ -r "$VERIFY" ]] || fail "closeout_verifier_missing"
[[ -r "$REPORT" ]] || fail "pnl_reporter_missing"
VERIFY_B64="$(base64 < "$VERIFY" | tr -d '\n')"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_RUN_MAIN=%s\n' "$EXPECTED_RUN_MAIN"
printf 'EXPECTED_RUN_ID=%s\n' "$EXPECTED_RUN_ID"
printf 'EXPECTED_EVIDENCE=%s\n' "$EXPECTED_EVIDENCE"
printf 'EXPECTED_MODEL_SHA256=%s\n' "$EXPECTED_MODEL_SHA256"
printf 'EXPECTED_RUN_SECONDS=%s\n' "$EXPECTED_RUN_SECONDS"
printf 'CLOSEOUT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s -- \
    '$EXPECTED_RUN_MAIN' \
    '$EXPECTED_RUN_ID' \
    '$EXPECTED_UNIT' \
    '$EXPECTED_EVIDENCE' \
    '$EXPECTED_RELEASE' \
    '$EXPECTED_VENV' \
    '$EXPECTED_MODEL_SHA256' \
    '$EXPECTED_RUN_SECONDS' \
    '$DURATION_TOLERANCE_SECONDS' \
    '$ENV_FILE' \
    '$VERIFY_B64' \
    '$REPORT_B64'" <<'REMOTE'
set -Eeuo pipefail

expected_main="$1"
expected_run_id="$2"
expected_unit="$3"
expected_evidence="$4"
expected_release="$5"
expected_venv="$6"
expected_model_sha="$7"
expected_run_seconds="$8"
duration_tolerance="$9"
shift 9
env_file="$1"
verify_b64="$2"
report_b64="$3"

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_CLOSEOUT=FAIL:%s\n' "$1" >&2
  exit 1
}

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

SOURCE_UNIT=bp-phase15-fast-live-source.service
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env

[[ "$expected_main" =~ ^[0-9a-f]{40}$ ]] || fail "expected_main_invalid"
[[ "$expected_model_sha" =~ ^[0-9a-f]{64}$ ]] ||
  fail "expected_model_sha_invalid"
[[ "$expected_run_seconds" =~ ^[0-9]+$ ]] || fail "run_seconds_invalid"
[[ "$duration_tolerance" =~ ^[0-9]+$ ]] || fail "duration_tolerance_invalid"

if systemctl is-active --quiet "$expected_unit"; then
  fail "shadow_still_active"
fi
unit_state="$(systemctl show "$expected_unit" --property=ActiveState --value 2>/dev/null || true)"
case "$unit_state" in
  activating|deactivating|reloading)
    fail "shadow_unit_transitional:$unit_state"
    ;;
esac

systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_active"
systemctl is-enabled --quiet "$SOURCE_UNIT" && fail "fast_live_source_enabled"

require_zero_money_file "$env_file"
require_zero_money_file "$SAFETY_FILE"

[[ -f "$expected_evidence" && ! -L "$expected_evidence" ]] ||
  fail "evidence_missing_or_invalid"
sudo -u bp test -r "$expected_evidence" || fail "evidence_unreadable"

[[ -d "$expected_release" && ! -L "$expected_release" ]] ||
  fail "runtime_release_missing_or_invalid"
[[ -f "$expected_release/.release-head" ]] ||
  fail "runtime_release_head_marker_missing"
[[ "$(cat "$expected_release/.release-head")" == "$expected_main" ]] ||
  fail "runtime_release_head_mismatch"
model="$expected_release/frozen-v4-model.joblib"
[[ -f "$model" && ! -L "$model" ]] || fail "staged_model_missing_or_invalid"
[[ "$(sha256sum "$model" | awk '{print $1}')" == "$expected_model_sha" ]] ||
  fail "staged_model_sha_mismatch"

[[ -d "$expected_venv" && ! -L "$expected_venv" ]] ||
  fail "paper_runtime_missing_or_invalid"
[[ -x "$expected_venv/bin/python" ]] || fail "paper_runtime_python_missing"
[[ -f "$expected_venv/.ready" ]] || fail "paper_runtime_not_ready"
[[ -f "$expected_venv/.release-head" ]] ||
  fail "paper_runtime_head_marker_missing"
[[ "$(cat "$expected_venv/.release-head")" == "$expected_main" ]] ||
  fail "paper_runtime_head_mismatch"

tmp="$(mktemp -d /tmp/bp-v4-closeout.XXXXXX)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

printf '%s' "$verify_b64" | base64 -d > "$tmp/verify.py"
printf '%s' "$report_b64" | base64 -d > "$tmp/report.py"
chmod 0644 "$tmp/verify.py" "$tmp/report.py"
chown bp:bp "$tmp/verify.py" "$tmp/report.py"
chmod 0755 "$tmp"

if [[ -z "$unit_state" ]]; then
  unit_state="not-found-or-collected"
fi
printf 'UNIT_ACTIVE=false\n'
printf 'UNIT_STATE=%s\n' "$unit_state"
printf 'FAST_LIVE_SOURCE_ACTIVE=false\n'
printf 'FAST_LIVE_SOURCE_ENABLED=false\n'
printf 'MODE=research\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_MONEY_USD=0\n'
printf 'DATABASE_ACCESS=read_only\n'

sudo -u bp env \
  PYTHONDONTWRITEBYTECODE=1 \
  "$expected_venv/bin/python" "$tmp/verify.py" \
  --evidence "$expected_evidence" \
  --expected-run-id "$expected_run_id" \
  --expected-model-sha256 "$expected_model_sha" \
  --expected-run-seconds "$expected_run_seconds" \
  --duration-tolerance-seconds "$duration_tolerance" ||
  fail "completion_verification_failed"

timeout --signal=TERM --kill-after=5s 120s sudo -u bp env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONNOUSERSITE=1 \
  PYTHONPATH="$expected_release/src" \
  "$expected_venv/bin/python" "$tmp/report.py" \
  --env-file "$env_file" \
  --evidence-glob "$expected_evidence" ||
  fail "pnl_report_failed"

printf 'PHASE14_V4_FRESH_BOOK_SHADOW_CLOSEOUT=PASS\n'
printf 'RUN_ID=%s\n' "$expected_run_id"
printf 'RUN_MAIN=%s\n' "$expected_main"
printf 'EVIDENCE_FILE=%s\n' "$expected_evidence"
printf 'MODEL_SHA256=%s\n' "$expected_model_sha"
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'SERVICE_MUTATION_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'WALLET_MATERIAL_LOADED=false\n'
REMOTE
