#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_SUPERSEDE_STOP_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_SUPERSEDE_STOP_ZONE:-us-east1-c}"
VM="${PHASE14_V4_SUPERSEDE_STOP_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V4_SUPERSEDE_STOP_ENV_FILE:-/etc/bp/bp.env}"
PREFLIGHT_ONLY="${PHASE14_V4_SUPERSEDE_STOP_PREFLIGHT_ONLY:-false}"

EXPECTED_RUN_MAIN="f5c76576619c35e65fc8a317d47c8a31dd263950"
EXPECTED_RUN_ID="v4-fresh-book-shadow-20261006T140323Z-f5c76576619c"
EXPECTED_UNIT="bp-${EXPECTED_RUN_ID}.service"
EXPECTED_EVIDENCE="/var/lib/bp/evidence/${EXPECTED_RUN_ID}.jsonl"
EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP=FAIL:%s\n' "$1" >&2
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
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_stale_update_before_stop"

RUNNER="$ROOT/scripts/run_v4_fresh_book_shadow.py"
SOURCE_FEATURES="$ROOT/src/bp_engine/v4_paper/source_time_features.py"
[[ -r "$RUNNER" ]] || fail "corrected_shadow_runner_missing"
[[ -r "$SOURCE_FEATURES" ]] || fail "corrected_source_features_missing"
grep -Fq 'probe_core_source_time_v4_readiness' "$RUNNER" ||
  fail "corrected_six_anchor_probe_missing"
grep -Fq '"source_retry_probe": "core_six_anchor_only"' "$RUNNER" ||
  fail "corrected_six_anchor_probe_evidence_missing"
grep -Fq 'raw_market_events.c.received_at <= requested' "$SOURCE_FEATURES" ||
  fail "strict_received_cutoff_missing"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW_SUPERSEDE_STOP:${EXPECTED_RUN_ID}:${LOCAL_HEAD}:${EXPECTED_MODEL_SHA256}"

case "$PREFLIGHT_ONLY" in
  true)
    printf 'PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP_PREFLIGHT=PASS\n'
    printf 'CANDIDATE_MAIN=%s\n' "$LOCAL_HEAD"
    printf 'EXPECTED_OLD_RUN_MAIN=%s\n' "$EXPECTED_RUN_MAIN"
    printf 'EXPECTED_OLD_RUN_ID=%s\n' "$EXPECTED_RUN_ID"
    printf 'EXPECTED_OLD_UNIT=%s\n' "$EXPECTED_UNIT"
    printf 'EXPECTED_OLD_EVIDENCE=%s\n' "$EXPECTED_EVIDENCE"
    printf 'MODEL_SHA256=%s\n' "$EXPECTED_MODEL_SHA256"
    printf 'EXPECTED_APPROVAL=%s\n' "$EXPECTED_APPROVAL"
    printf 'PRODUCTION_HOST_CONTACTED=false\n'
    printf 'PRODUCTION_MUTATION_PERFORMED=false\n'
    printf 'SERVICE_STOP_PERFORMED=false\n'
    printf 'EVIDENCE_DELETE_PERFORMED=false\n'
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

[[ "${PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP_APPROVAL:-}" == "$EXPECTED_APPROVAL" ]] ||
  fail "explicit_supersede_stop_approval_missing_or_mismatched"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_OLD_RUN_ID=%s\n' "$EXPECTED_RUN_ID"
printf 'EXPECTED_OLD_UNIT=%s\n' "$EXPECTED_UNIT"
printf 'EXPECTED_OLD_EVIDENCE=%s\n' "$EXPECTED_EVIDENCE"
printf 'MODEL_SHA256=%s\n' "$EXPECTED_MODEL_SHA256"
printf 'SUPERSEDE_SCOPE=exact_defective_zero_money_shadow_only\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="sudo bash -s -- '$EXPECTED_RUN_ID' '$EXPECTED_UNIT' '$EXPECTED_EVIDENCE' '$EXPECTED_MODEL_SHA256' '$ENV_FILE'" <<'REMOTE'
set -Eeuo pipefail

expected_run_id="$1"
expected_unit="$2"
expected_evidence="$3"
expected_model_sha="$4"
env_file="$5"

fail() {
  printf 'PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP=FAIL:%s\n' "$1" >&2
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
RECORDER_UNIT=bp-recorder.service
POSTGRES_UNIT=bp-postgres.service
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env

[[ "$expected_run_id" == "v4-fresh-book-shadow-20261006T140323Z-f5c76576619c" ]] ||
  fail "unexpected_run_id"
[[ "$expected_unit" == "bp-$expected_run_id.service" ]] || fail "unexpected_unit"
[[ "$expected_evidence" == "/var/lib/bp/evidence/$expected_run_id.jsonl" ]] ||
  fail "unexpected_evidence_path"
[[ "$expected_model_sha" == "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf" ]] ||
  fail "unexpected_model_sha"

systemctl is-active --quiet "$SOURCE_UNIT" && fail "fast_live_source_active"
systemctl is-enabled --quiet "$SOURCE_UNIT" && fail "fast_live_source_enabled"
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active"
systemctl is-active --quiet "$POSTGRES_UNIT" || fail "postgres_not_active"
require_zero_money_file "$env_file"
require_zero_money_file "$SAFETY_FILE"

[[ -f "$expected_evidence" && ! -L "$expected_evidence" ]] ||
  fail "old_evidence_missing_or_invalid"
sudo -u bp test -r "$expected_evidence" || fail "old_evidence_unreadable"
systemctl is-active --quiet "$expected_unit" || fail "old_shadow_not_active"

sudo -u bp python3 - "$expected_evidence" "$expected_model_sha" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_model_sha = sys.argv[2]
records = []
ignored_non_json_lines = 0
for line_number, raw in enumerate(
    path.read_text(encoding="utf-8").splitlines(),
    start=1,
):
    stripped = raw.strip()
    if not stripped:
        continue
    if not stripped.startswith("{"):
        ignored_non_json_lines += 1
        continue
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"malformed JSON evidence at line {line_number}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"non-object JSON evidence at line {line_number}")
    records.append(parsed)

if not records:
    raise SystemExit("old shadow evidence contains no JSON event records")
starts = [row for row in records if row.get("event") == "v4_fresh_book_shadow_started"]
if len(starts) != 1:
    raise SystemExit(f"expected one old start event, got {len(starts)}")
start = starts[0]
if start.get("model_sha256") != expected_model_sha:
    raise SystemExit("old shadow model sha mismatch")
for key, expected in (
    ("database_read_only", True),
    ("order_submission_enabled", False),
    ("wallet_material_loaded", False),
    ("holdout_labels_read", False),
    ("model_refit_performed", False),
    ("threshold_tuning_performed", False),
):
    if start.get(key) != expected:
        raise SystemExit(f"old shadow safety mismatch: {key}")
source_ineligible = sum(
    row.get("event") == "v4_fresh_book_shadow_source_ineligible"
    for row in records
)
predictions = sum(row.get("event") == "v4_source_time_prediction" for row in records)
evaluated = sum(
    row.get("event") == "v4_fresh_book_shadow_evaluated"
    for row in records
)
paper_trades = sum(
    row.get("event") == "v4_fresh_book_shadow_evaluated" and bool(row.get("trade"))
    for row in records
)
if source_ineligible <= 0:
    raise SystemExit("old shadow has no source-ineligible evidence")
print(f"OLD_EVIDENCE_NON_JSON_LINE_COUNT={ignored_non_json_lines}")
print(f"OLD_SOURCE_INELIGIBLE_COUNT={source_ineligible}")
print(f"OLD_PREDICTION_COUNT={predictions}")
print(f"OLD_EVALUATED_COUNT={evaluated}")
print(f"OLD_PAPER_TRADE_COUNT={paper_trades}")
PY

printf 'OLD_SHADOW_ACTIVE_BEFORE_STOP=true\n'
systemctl stop "$expected_unit" || fail "old_shadow_stop_failed"

for _ in $(seq 1 50); do
  if ! systemctl is-active --quiet "$expected_unit"; then
    break
  fi
  sleep 0.1
done
systemctl is-active --quiet "$expected_unit" && fail "old_shadow_still_active_after_stop"

[[ -f "$expected_evidence" && ! -L "$expected_evidence" ]] ||
  fail "old_evidence_not_preserved"

printf 'PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP=PASS\n'
printf 'STOPPED_RUN_ID=%s\n' "$expected_run_id"
printf 'STOPPED_UNIT=%s\n' "$expected_unit"
printf 'PRESERVED_EVIDENCE=%s\n' "$expected_evidence"
printf 'OLD_SHADOW_ACTIVE_AFTER_STOP=false\n'
printf 'RECORDER_ACTIVE=true\n'
printf 'POSTGRES_ACTIVE=true\n'
printf 'FAST_LIVE_SOURCE_ACTIVE=false\n'
printf 'LIVE_TRADING_ENABLED=false\n'
printf 'REAL_MONEY_USD=0\n'
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'WALLET_MATERIAL_LOADED=false\n'
printf 'EVIDENCE_DELETE_PERFORMED=false\n'
printf 'SERVICE_STOP_PERFORMED=true\n'
printf 'PRODUCTION_MUTATION_PERFORMED=true\n'
REMOTE
