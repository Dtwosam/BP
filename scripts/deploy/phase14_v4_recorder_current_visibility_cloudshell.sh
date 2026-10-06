#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_CURRENT_VISIBILITY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_CURRENT_VISIBILITY_ZONE:-us-east1-c}"
VM="${PHASE14_V4_CURRENT_VISIBILITY_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V4_CURRENT_VISIBILITY_ENV_FILE:-/etc/bp/bp.env}"
EXPECTED_RUN_ID="v4-fresh-book-shadow-20261006T185619Z-271db613e003"
EXPECTED_UNIT="bp-${EXPECTED_RUN_ID}.service"
EXPECTED_EVIDENCE="/var/lib/bp/evidence/${EXPECTED_RUN_ID}.jsonl"

fail() {
  printf 'PHASE14_V4_RECORDER_CURRENT_VISIBILITY=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
[[ "$(git branch --show-current)" == "main" ]] || fail "local_branch_not_main"
git fetch origin main --quiet || fail "fetch_main_failed"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_stale_update_before_probe"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

REPORT="$ROOT/scripts/report_v4_recorder_visibility.py"
[[ -r "$REPORT" ]] || fail "visibility_report_missing"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_RUN_ID=%s\n' "$EXPECTED_RUN_ID"
printf 'REPORT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s -- '$ENV_FILE' '$EXPECTED_RUN_ID' '$EXPECTED_UNIT' '$EXPECTED_EVIDENCE' '$REPORT_B64'" <<'REMOTE'
set -Eeuo pipefail

env_file="$1"
expected_run_id="$2"
expected_unit="$3"
expected_evidence="$4"
report_b64="$5"

fail() {
  printf 'PHASE14_V4_RECORDER_CURRENT_VISIBILITY=FAIL:%s\n' "$1" >&2
  exit 1
}

REPO=/opt/bp
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
RECORDER_UNIT=bp-recorder.service
POSTGRES_UNIT=bp-postgres.service
SOURCE_UNIT=bp-phase15-fast-live-source.service

read_env() {
  local path=$1 key=$2
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

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "python_runtime_missing"
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active"
systemctl is-active --quiet "$POSTGRES_UNIT" || fail "postgres_not_active"
systemctl is-active --quiet "$expected_unit" || fail "corrected_shadow_not_active"
systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_active"
systemctl is-enabled --quiet "$SOURCE_UNIT" && fail "fast_live_source_enabled"
require_zero_money_file "$env_file"
require_zero_money_file "$SAFETY_FILE"
[[ -f "$expected_evidence" && ! -L "$expected_evidence" ]] ||
  fail "shadow_evidence_missing_or_invalid"

printf 'DEPLOYED_RECORDER_HEAD=%s\n' "$(git -C "$REPO" rev-parse HEAD)"
printf 'RECORDER_MAIN_PID=%s\n' "$(systemctl show -p MainPID --value "$RECORDER_UNIT")"
printf 'RECORDER_N_RESTARTS=%s\n' "$(systemctl show -p NRestarts --value "$RECORDER_UNIT")"
printf 'SHADOW_UNIT_ACTIVE=true\n'

sudo -u bp env \
  -u RECORDER_QUEUE_MAXSIZE \
  -u RECORDER_BATCH_SIZE \
  -u RECORDER_WRITER_WORKERS \
  -u RECORDER_FLUSH_INTERVAL_SECONDS \
  "$REPO/.venv/bin/python" - "$env_file" <<'PY'
import sys
from bp_engine.config import Settings

settings = Settings(_env_file=sys.argv[1])
print(f"RECORDER_QUEUE_MAXSIZE={settings.recorder_queue_maxsize}")
print(f"RECORDER_BATCH_SIZE={settings.recorder_batch_size}")
print(f"RECORDER_WRITER_WORKERS={settings.recorder_writer_workers}")
print(f"RECORDER_FLUSH_INTERVAL_SECONDS={settings.recorder_flush_interval_seconds}")
PY

sudo -u bp python3 - "$expected_evidence" "$expected_run_id" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
expected_run_id = sys.argv[2]
records = []
for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
    stripped = raw.strip()
    if not stripped or not stripped.startswith("{"):
        continue
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"malformed JSON-looking evidence at line {line_number}: {exc}") from exc
    if isinstance(parsed, dict):
        records.append(parsed)

starts = [row for row in records if row.get("event") == "v4_fresh_book_shadow_started"]
if len(starts) != 1:
    raise SystemExit(f"expected one corrected shadow start record, got {len(starts)}")
start = starts[0]
if start.get("source_retry_probe") != "core_six_anchor_only":
    raise SystemExit("corrected shadow retry probe mismatch")
if start.get("source_received_cutoff") != "received_at_lte_decision_at":
    raise SystemExit("corrected shadow received cutoff mismatch")

ineligible = [
    row for row in records
    if row.get("event") == "v4_fresh_book_shadow_source_ineligible"
]
predictions = [
    row for row in records
    if row.get("event") == "v4_source_time_prediction"
]
evaluated = [
    row for row in records
    if row.get("event") == "v4_fresh_book_shadow_evaluated"
]
retry_counts = [
    int(row.get("source_retry_count", 0) or 0)
    for row in ineligible
]
reason_counts = Counter(
    str(reason)
    for row in ineligible
    for reason in row.get("source_ineligible_reasons", [])
)
print(f"SHADOW_RUN_ID={expected_run_id}")
print(f"SHADOW_SOURCE_INELIGIBLE_COUNT={len(ineligible)}")
print(f"SHADOW_PREDICTION_COUNT={len(predictions)}")
print(f"SHADOW_EVALUATED_COUNT={len(evaluated)}")
print(f"SHADOW_RETRY_COUNT_MIN={min(retry_counts) if retry_counts else 0}")
print(f"SHADOW_RETRY_COUNT_MAX={max(retry_counts) if retry_counts else 0}")
print(
    "SHADOW_RETRY_COUNT_MEDIAN="
    + (
        str(sorted(retry_counts)[len(retry_counts) // 2])
        if retry_counts
        else "0"
    )
)
print(
    "SHADOW_SOURCE_REASON_COUNTS="
    + json.dumps(dict(sorted(reason_counts.items())), sort_keys=True)
)
PY

tmp="$(mktemp -d /tmp/bp-v4-current-visibility.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
chmod 0755 "$tmp"
printf '%s' "$report_b64" | base64 -d > "$tmp/report_v4_recorder_visibility.py"
chmod 0644 "$tmp/report_v4_recorder_visibility.py"

timeout --signal=TERM --kill-after=5s 60s \
  sudo -u bp env \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$REPO/src" \
    "$REPO/.venv/bin/python" \
    "$tmp/report_v4_recorder_visibility.py" \
    --env-file "$env_file" \
    --samples 80 \
    --interval-seconds 0.25

printf 'PHASE14_V4_RECORDER_CURRENT_VISIBILITY=PASS\n'
printf 'DATABASE_ACCESS=read_only\n'
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'SERVICE_MUTATION_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_MONEY_USD=0\n'
REMOTE
