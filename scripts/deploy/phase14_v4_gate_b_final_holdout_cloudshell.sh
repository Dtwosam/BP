#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_GATE_B_FINAL_HOLDOUT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
VM="${PHASE14_V4_GATE_B_FINAL_HOLDOUT_VM:-bp-recorder}"
ZONE="${PHASE14_V4_GATE_B_FINAL_HOLDOUT_ZONE:-us-east1-c}"
ENV_FILE="${PHASE14_V4_GATE_B_FINAL_HOLDOUT_ENV_FILE:-/etc/bp/bp.env}"
APPROVAL="${PHASE14_V4_GATE_B_FINAL_HOLDOUT_APPROVAL:-}"

EXPECTED_PLAN_FILE_SHA256="564e0c299b360062c5d1e37ceb10050e5b600f4fc29f35451aef8c08a5884dad"
EXPECTED_PLAN_SHA256="9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
EXPECTED_SELECTION_FILE_SHA256="cd7e42eb07fe675ebf4687bfc8162908428e4583e763efd9762b6ec7ec120a53"
EXPECTED_SELECTION_SHA256="895cb70ae0cdbc22f4e3585c77db3ad20f8186d1ee1992a58025d89bb1e2bb1a"
EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
EXPECTED_MODEL_SIZE_BYTES=230132
EXPECTED_HOLDOUT_MARKETS=288

fail() {
  printf 'PHASE14_V4_GATE_B_FINAL_HOLDOUT=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"
git fetch origin main --quiet || fail "fetch_main_failed"
git switch main >/dev/null || fail "switch_main_failed"
git pull --ff-only origin main >/dev/null || fail "pull_main_failed"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "main_head_mismatch"

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_GATE_B_FINAL_HOLDOUT:${LOCAL_HEAD}:${EXPECTED_PLAN_FILE_SHA256}:${EXPECTED_SELECTION_FILE_SHA256}:${EXPECTED_MODEL_SHA256}"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] ||
  fail "explicit_sha_bound_final_holdout_authorization_required"

gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

ARCHIVE="$(mktemp /tmp/bp-v4-final-holdout.XXXXXX.tar.gz)"
trap 'rm -f "$ARCHIVE"' EXIT
git archive --format=tar.gz --output="$ARCHIVE" "$LOCAL_HEAD"
ARCHIVE_SHA256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
REMOTE_ARCHIVE="/tmp/bp-v4-final-holdout-${LOCAL_HEAD}.tar.gz"
gcloud compute scp "$ARCHIVE" "$VM:$REMOTE_ARCHIVE" \
  --project="$PROJECT" --zone="$ZONE" --quiet >/dev/null ||
  fail "archive_upload_failed"

OUTPUT="$(
gcloud compute ssh "$VM" \
  --project="$PROJECT" --zone="$ZONE" --quiet \
  --command="sudo env \
PHASE14_V4_HOLDOUT_HEAD='$LOCAL_HEAD' \
PHASE14_V4_HOLDOUT_ARCHIVE='$REMOTE_ARCHIVE' \
PHASE14_V4_HOLDOUT_ARCHIVE_SHA256='$ARCHIVE_SHA256' \
PHASE14_V4_HOLDOUT_ENV_FILE='$ENV_FILE' \
PHASE14_V4_HOLDOUT_PLAN_FILE_SHA256='$EXPECTED_PLAN_FILE_SHA256' \
PHASE14_V4_HOLDOUT_PLAN_SHA256='$EXPECTED_PLAN_SHA256' \
PHASE14_V4_HOLDOUT_SELECTION_FILE_SHA256='$EXPECTED_SELECTION_FILE_SHA256' \
PHASE14_V4_HOLDOUT_SELECTION_SHA256='$EXPECTED_SELECTION_SHA256' \
PHASE14_V4_HOLDOUT_MODEL_SHA256='$EXPECTED_MODEL_SHA256' \
PHASE14_V4_HOLDOUT_MODEL_SIZE_BYTES='$EXPECTED_MODEL_SIZE_BYTES' \
PHASE14_V4_HOLDOUT_MARKET_COUNT='$EXPECTED_HOLDOUT_MARKETS' bash -s" <<'REMOTE'
set -Eeuo pipefail

HEAD_SHA="${PHASE14_V4_HOLDOUT_HEAD:?}"
ARCHIVE="${PHASE14_V4_HOLDOUT_ARCHIVE:?}"
ARCHIVE_SHA256="${PHASE14_V4_HOLDOUT_ARCHIVE_SHA256:?}"
ENV_FILE="${PHASE14_V4_HOLDOUT_ENV_FILE:?}"
PLAN_FILE_SHA256="${PHASE14_V4_HOLDOUT_PLAN_FILE_SHA256:?}"
PLAN_SHA256="${PHASE14_V4_HOLDOUT_PLAN_SHA256:?}"
SELECTION_FILE_SHA256="${PHASE14_V4_HOLDOUT_SELECTION_FILE_SHA256:?}"
SELECTION_SHA256="${PHASE14_V4_HOLDOUT_SELECTION_SHA256:?}"
MODEL_SHA256="${PHASE14_V4_HOLDOUT_MODEL_SHA256:?}"
MODEL_SIZE_BYTES="${PHASE14_V4_HOLDOUT_MODEL_SIZE_BYTES:?}"
HOLDOUT_MARKET_COUNT="${PHASE14_V4_HOLDOUT_MARKET_COUNT:?}"

REPO=/opt/bp
EVIDENCE_ROOT=/var/lib/bp/evidence
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
PLAN_SOURCE="$EVIDENCE_ROOT/v4-gate-b-plan-v2-20260930.json"
ATTEMPT_MARKER="$EVIDENCE_ROOT/v4-gate-b-final-holdout-v2-attempt.json"
RUNTIME_ROOT=""

fail() {
  printf 'PHASE14_V4_GATE_B_FINAL_HOLDOUT=FAIL:%s\n' "$1" >&2
  if [[ -f "$ATTEMPT_MARKER" ]]; then
    echo "HOLDOUT_TOUCHED=true" >&2
    echo "HOLDOUT_ATTEMPT_MARKER=$ATTEMPT_MARKER" >&2
  else
    echo "HOLDOUT_TOUCHED=false" >&2
  fi
  exit 1
}

cleanup() {
  rm -f "$ARCHIVE"
  [[ -z "$RUNTIME_ROOT" ]] || rm -rf "$RUNTIME_ROOT"
}
trap cleanup EXIT

read_env() {
  local path=$1 key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

require_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -r "$path" ]] || fail "safety_file_missing:$path"
    mode="$(read_env "$path" MODE)"
    live="$(read_env "$path" LIVE_TRADING_ENABLED)"
    trade="$(read_env "$path" MAX_TRADE_SIZE_USD)"
    loss="$(read_env "$path" MAX_DAILY_LOSS_USD)"
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_enabled:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_nonzero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_nonzero:$path"
  done
}

find_file_by_sha256() {
  local root=$1 suffix=$2 expected=$3 path digest
  while IFS= read -r -d '' path; do
    digest="$(sha256sum "$path" 2>/dev/null | awk '{print $1}' || true)"
    if [[ "$digest" == "$expected" ]]; then
      printf '%s\n' "$path"
      return 0
    fi
  done < <(find "$root" -xdev -type f -name "*$suffix" -print0 2>/dev/null)
  return 1
}

find_model_by_sha256() {
  local root path size digest
  for root in "$EVIDENCE_ROOT" /var/lib/bp/runtime /opt/bp; do
    [[ -d "$root" ]] || continue
    while IFS= read -r -d '' path; do
      size="$(stat -c '%s' "$path" 2>/dev/null || true)"
      [[ "$size" == "$MODEL_SIZE_BYTES" ]] || continue
      digest="$(sha256sum "$path" 2>/dev/null | awk '{print $1}' || true)"
      if [[ "$digest" == "$MODEL_SHA256" ]]; then
        printf '%s\n' "$path"
        return 0
      fi
    done < <(find "$root" -xdev -type f -size "${MODEL_SIZE_BYTES}c" -print0 2>/dev/null)
  done
  return 1
}

[[ -r "$ARCHIVE" ]] || fail "archive_missing"
[[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" == "$ARCHIVE_SHA256" ]] ||
  fail "archive_sha_mismatch"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
systemctl is-active --quiet bp-postgres.service || fail "postgres_not_active"
systemctl is-active --quiet bp-recorder.service || fail "recorder_not_active"
if systemctl is-active --quiet bp-phase15-fast-live-source.service; then
  fail "fast_live_source_active"
fi
test ! -e "$ATTEMPT_MARKER" || fail "holdout_attempt_already_exists"
require_zero_money

[[ -r "$PLAN_SOURCE" ]] || fail "frozen_plan_missing"
[[ "$(sha256sum "$PLAN_SOURCE" | awk '{print $1}')" == "$PLAN_FILE_SHA256" ]] ||
  fail "frozen_plan_file_sha256_mismatch"
SELECTION_SOURCE="$(find_file_by_sha256 "$EVIDENCE_ROOT" ".json" "$SELECTION_FILE_SHA256")" ||
  fail "frozen_selection_file_not_found"
MODEL_SOURCE="$(find_model_by_sha256)" || fail "frozen_model_file_not_found"

"$REPO/.venv/bin/python" - "$PLAN_SOURCE" "$SELECTION_SOURCE" \
  "$PLAN_SHA256" "$SELECTION_SHA256" "$MODEL_SHA256" "$MODEL_SIZE_BYTES" \
  "$HOLDOUT_MARKET_COUNT" <<'PY'
import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
selection = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expected_plan, expected_selection, expected_model = sys.argv[3:6]
expected_model_size = int(sys.argv[6])
expected_holdout_count = int(sys.argv[7])

if plan.get("plan_sha256") != expected_plan:
    raise SystemExit("plan semantic SHA mismatch")
holdout_ids = (plan.get("final") or {}).get("holdout_condition_ids") or []
if len(holdout_ids) != expected_holdout_count:
    raise SystemExit("holdout market count mismatch")
if plan.get("final_holdout_evaluated") is not False:
    raise SystemExit("plan already records holdout evaluation")

if selection.get("selection_sha256") != expected_selection:
    raise SystemExit("selection semantic SHA mismatch")
if selection.get("stage") != "ordinary_selection_frozen":
    raise SystemExit("selection stage mismatch")
if selection.get("plan_sha256") != expected_plan:
    raise SystemExit("selection plan mismatch")
if selection.get("holdout_labels_read") is not False:
    raise SystemExit("holdout labels already read")
if selection.get("holdout_evaluated") is not False:
    raise SystemExit("holdout already evaluated")
artifact = selection.get("model_artifact") or {}
if artifact.get("sha256") != expected_model:
    raise SystemExit("model SHA mismatch in selection")
if int(artifact.get("size_bytes", -1)) != expected_model_size:
    raise SystemExit("model size mismatch in selection")
selected = (selection.get("final") or {}).get("selected_forecast") or {}
edge = (selection.get("final") or {}).get("frozen_edge_policy") or {}
if selected.get("candidate") != "full_v4_xgboost":
    raise SystemExit("frozen candidate changed")
if int(selected.get("offset_seconds", -1)) != 240:
    raise SystemExit("frozen offset changed")
if selected.get("calibration_method") != "identity":
    raise SystemExit("frozen calibration changed")
if edge.get("policy") != "trade_threshold" or float(edge.get("min_edge", -1)) != 0.05:
    raise SystemExit("frozen edge policy changed")
PY

RUNTIME_ROOT="$(mktemp -d /var/tmp/bp-v4-final-holdout.XXXXXX)"
chmod 0755 "$RUNTIME_ROOT"
tar -xzf "$ARCHIVE" -C "$RUNTIME_ROOT"
test -r "$RUNTIME_ROOT/src/bp_engine/v4_research/holdout.py" ||
  fail "candidate_holdout_module_missing"

RUN_ID="phase14-v4-gate-b-final-holdout-$(date -u +%Y%m%dT%H%M%SZ)-${HEAD_SHA:0:12}"
RUN_DIR="$EVIDENCE_ROOT/$RUN_ID"
test ! -e "$RUN_DIR" || fail "run_dir_already_exists"

"$REPO/.venv/bin/python" - "$ATTEMPT_MARKER" "$RUN_DIR" "$HEAD_SHA" \
  "$PLAN_FILE_SHA256" "$PLAN_SHA256" "$SELECTION_FILE_SHA256" \
  "$SELECTION_SHA256" "$MODEL_SHA256" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "purpose": "phase14-v4-gate-b-final-holdout-attempt-v1",
    "status": "HOLDOUT_ACCESS_COMMITTED_BEFORE_LABEL_READ",
    "committed_at": datetime.now(UTC).isoformat(),
    "run_dir": sys.argv[2],
    "candidate_main": sys.argv[3],
    "plan_file_sha256": sys.argv[4],
    "plan_sha256": sys.argv[5],
    "selection_file_sha256": sys.argv[6],
    "selection_sha256": sys.argv[7],
    "model_artifact_sha256": sys.argv[8],
    "one_shot": True,
    "automatic_promotion": False,
    "paper_activation_authorized": False,
    "live_trading_enabled": False,
}
encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
try:
    os.write(fd, encoded)
    os.fsync(fd)
finally:
    os.close(fd)
PY

install -d -o bp -g bp -m 0750 "$RUN_DIR"
install -o bp -g bp -m 0640 "$PLAN_SOURCE" "$RUN_DIR/plan.json"
install -o bp -g bp -m 0640 "$SELECTION_SOURCE" "$RUN_DIR/selection.json"
install -o bp -g bp -m 0640 "$MODEL_SOURCE" "$RUN_DIR/model.joblib"

sudo -u bp env MODE=research LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$RUNTIME_ROOT/src" \
  "$REPO/.venv/bin/python" -m bp_engine.v4_research.cli \
    --env-file "$ENV_FILE" evaluate-holdout \
    --plan "$RUN_DIR/plan.json" \
    --selection "$RUN_DIR/selection.json" \
    --model "$RUN_DIR/model.joblib" \
    --output "$RUN_DIR/holdout.json" > "$RUN_DIR/summary.json" ||
  fail "v4_final_holdout_evaluation_failed"

"$REPO/.venv/bin/python" - "$RUN_DIR/holdout.json" "$RUN_DIR/summary.json" \
  "$PLAN_SHA256" "$SELECTION_SHA256" "$MODEL_SHA256" "$HOLDOUT_MARKET_COUNT" <<'PY'
import json
import sys
from pathlib import Path

holdout = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expected_plan, expected_selection, expected_model = sys.argv[3:6]
expected_markets = int(sys.argv[6])

if holdout.get("stage") != "final_holdout_evaluated":
    raise SystemExit("holdout stage mismatch")
if holdout.get("plan_sha256") != expected_plan:
    raise SystemExit("holdout plan SHA mismatch")
if holdout.get("selection_sha256") != expected_selection:
    raise SystemExit("holdout selection SHA mismatch")
if holdout.get("model_artifact_sha256") != expected_model:
    raise SystemExit("holdout model SHA mismatch")
if holdout.get("holdout_labels_read") is not True:
    raise SystemExit("holdout label marker missing")
if holdout.get("holdout_evaluated_once") is not True:
    raise SystemExit("holdout one-shot marker missing")
for key in (
    "model_refit_performed",
    "threshold_tuning_performed",
    "policy_reselection_performed",
    "automatic_promotion",
    "paper_activation_authorized",
    "paper_activation_performed",
    "live_trading_enabled",
):
    if holdout.get(key) is not False:
        raise SystemExit(f"unsafe holdout flag: {key}")
if int(holdout.get("real_money_usd", -1)) != 0:
    raise SystemExit("real money boundary changed")
forecast = ((holdout.get("holdout_evaluation") or {}).get("forecast") or {}).get("metrics") or {}
if int(forecast.get("market_count", -1)) != expected_markets:
    raise SystemExit("holdout evaluated market count mismatch")
if summary.get("paper_activation_performed") is not False:
    raise SystemExit("summary crossed paper activation boundary")
PY

chmod 0440 "$ATTEMPT_MARKER" "$RUN_DIR/plan.json" "$RUN_DIR/selection.json" \
  "$RUN_DIR/model.joblib" "$RUN_DIR/holdout.json" "$RUN_DIR/summary.json"

echo "PHASE14_V4_GATE_B_FINAL_HOLDOUT=PASS"
echo "HOLDOUT_TOUCHED=true"
echo "HOLDOUT_ATTEMPT_MARKER=$ATTEMPT_MARKER"
echo "EVIDENCE_DIR=$RUN_DIR"
echo "CANDIDATE_MAIN=$HEAD_SHA"
echo "PLAN_SHA256=$PLAN_SHA256"
echo "SELECTION_SHA256=$SELECTION_SHA256"
echo "MODEL_ARTIFACT_SHA256=$MODEL_SHA256"
echo "HOLDOUT_MARKET_COUNT=$HOLDOUT_MARKET_COUNT"
echo "DATABASE_WRITES_PERFORMED=false"
echo "MODEL_REFIT_PERFORMED=false"
echo "THRESHOLD_TUNING_PERFORMED=false"
echo "POLICY_RESELECTION_PERFORMED=false"
echo "AUTOMATIC_PROMOTION=false"
echo "PAPER_ACTIVATION_PERFORMED=false"
echo "LIVE_TRADING_ENABLED=false"
echo "REAL_MONEY_USD=0"
REMOTE
)" || {
  rc=$?
  printf '%s\n' "$OUTPUT"
  exit "$rc"
}

printf '%s\n' "$OUTPUT"
