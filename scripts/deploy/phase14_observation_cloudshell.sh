#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_OBSERVATION_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_OBSERVATION_ZONE:-us-east1-c}"
VM="${PHASE14_OBSERVATION_VM:-bp-recorder}"

fail_local() {
  echo "PHASE14_OBSERVATION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail_local "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail_local "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail_local "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail_local "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .   || fail_local "gcloud_auth_missing"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
SAFETY_FILE=/etc/bp/bp-prospective-runtime-safety.env
EXPECTED_DEPLOYED_HEAD="52b4355d6f077373b873f7a6f42bc37a20ddbc7b"
EXPECTED_V3_RUNTIME="/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770"
EXPECTED_V4_RUNTIME="/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574"
V3_CURRENT=/var/lib/bp/runtime/v3-paper-current
V4_CURRENT=/var/lib/bp/runtime/v4-forward-current

fail() {
  echo "PHASE14_OBSERVATION=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -r "$SAFETY_FILE" ]] || fail "safety_file_missing"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$EXPECTED_DEPLOYED_HEAD" ]]   || fail "unexpected_deployed_head"
[[ "$(readlink -f "$V3_CURRENT")" == "$EXPECTED_V3_RUNTIME" ]]   || fail "unexpected_v3_runtime"
[[ "$(readlink -f "$V4_CURRENT")" == "$EXPECTED_V4_RUNTIME" ]]   || fail "unexpected_v4_runtime"

for unit in   bp-postgres.service   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service   bp-storage-maintenance.timer   bp-storage-disk-health.timer   bp-v4-forward-coverage.timer
do
  systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
done

MODE=$(read_env "$ENV_FILE" MODE)
LIVE=$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)
TRADE=$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)
LOSS=$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)
SAFE_MODE=$(read_env "$SAFETY_FILE" MODE)
SAFE_LIVE=$(read_env "$SAFETY_FILE" LIVE_TRADING_ENABLED)
SAFE_TRADE=$(read_env "$SAFETY_FILE" MAX_TRADE_SIZE_USD)
SAFE_LOSS=$(read_env "$SAFETY_FILE" MAX_DAILY_LOSS_USD)

[[ "$MODE" == "research" && "$SAFE_MODE" == "research" ]] || fail "mode_not_research"
[[ "$LIVE" == "false" && "$SAFE_LIVE" == "false" ]] || fail "live_trading_not_false"
[[ "$TRADE" == "0" && "$SAFE_TRADE" == "0" ]] || fail "max_trade_size_not_zero"
[[ "$LOSS" == "0" && "$SAFE_LOSS" == "0" ]] || fail "max_daily_loss_not_zero"

V3_JSON=$(sudo -u bp env   PYTHONPATH="$V3_CURRENT/src"   MODE=research   LIVE_TRADING_ENABLED=false   MAX_TRADE_SIZE_USD=0   MAX_DAILY_LOSS_USD=0   "$REPO/.venv/bin/python" - <<'PY'
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text
from bp_engine.config import Settings
from bp_engine.v3_paper.report import build_v3_paper_report

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)
try:
    with engine.connect() as connection:
        mode = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
        if mode != "on":
            raise SystemExit("v3 observation database session is not read-only")
    payload = build_v3_paper_report(engine, recent_limit=20)
finally:
    engine.dispose()

def value(item):
    if isinstance(item, Decimal):
        return format(item, "f")
    if isinstance(item, datetime):
        return item.astimezone(UTC).isoformat()
    if isinstance(item, dict):
        return {str(key): value(entry) for key, entry in item.items()}
    if isinstance(item, (list, tuple)):
        return [value(entry) for entry in item]
    return item

print(json.dumps(value(payload), sort_keys=True))
PY
)

V4_JSON=$(sudo -u bp env   PYTHONPATH="$V4_CURRENT/src"   MODE=research   LIVE_TRADING_ENABLED=false   MAX_TRADE_SIZE_USD=0   MAX_DAILY_LOSS_USD=0   "$REPO/.venv/bin/python" - <<'PY'
import json

from sqlalchemy import create_engine, text
from bp_engine.config import Settings
from bp_engine.features.v4_coverage import build_v4_coverage_report
from bp_engine.features.v4_forward import V4_FORWARD_EPOCH

settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)
try:
    with engine.connect() as connection:
        mode = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
        if mode != "on":
            raise SystemExit("v4 observation database session is not read-only")
        payload = build_v4_coverage_report(connection, epoch_start=V4_FORWARD_EPOCH)
finally:
    engine.dispose()

print(json.dumps(payload, default=str, sort_keys=True))
PY
)

STORAGE_JSON=$(sudo -u bp env   PYTHONPATH="$REPO/src"   MODE=research   LIVE_TRADING_ENABLED=false   MAX_TRADE_SIZE_USD=0   MAX_DAILY_LOSS_USD=0   "$REPO/.venv/bin/python" - <<'PY'
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from bp_engine.config import Settings
from bp_engine.storage.maintenance import build_composite_storage_health

settings = Settings(_env_file="/etc/bp/bp.env")
target = Path(settings.storage_health_path or settings.storage_archive_dir)
if not target.is_dir():
    raise SystemExit(f"observation storage path must already exist: {target}")

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)
try:
    with engine.connect() as connection:
        mode = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
        if mode != "on":
            raise SystemExit("storage observation database session is not read-only")
    payload = build_composite_storage_health(
        engine,
        target,
        settings,
        now=datetime.now(UTC),
    )
finally:
    engine.dispose()

print(json.dumps(payload, default=str, sort_keys=True))
PY
)

V3_B64=$(printf '%s' "$V3_JSON" | base64 -w0)
V4_B64=$(printf '%s' "$V4_JSON" | base64 -w0)
STORAGE_B64=$(printf '%s' "$STORAGE_JSON" | base64 -w0)

"$REPO/.venv/bin/python" - "$V3_B64" "$V4_B64" "$STORAGE_B64" <<'PY'
import base64
import json
import sys
from datetime import UTC, datetime

v3 = json.loads(base64.b64decode(sys.argv[1]).decode())
v4 = json.loads(base64.b64decode(sys.argv[2]).decode())
storage = json.loads(base64.b64decode(sys.argv[3]).decode())

v4_integrity = {
    "future_cutoff_violation_count": int(v4.get("future_cutoff_violation_count", 0)),
    "polymarket_predictor_key_count": int(v4.get("polymarket_predictor_key_count", 0)),
    "regime_invariant_violation_count": int(v4.get("regime_invariant_violation_count", 0)),
    "policy_selected": bool(v4.get("policy_selected", False)),
    "training_run": bool(v4.get("training_run", False)),
    "automatic_promotion": bool(v4.get("automatic_promotion", False)),
}
v4_ok = (
    v4_integrity["future_cutoff_violation_count"] == 0
    and v4_integrity["polymarket_predictor_key_count"] == 0
    and v4_integrity["regime_invariant_violation_count"] == 0
    and v4_integrity["policy_selected"] is False
    and v4_integrity["training_run"] is False
    and v4_integrity["automatic_promotion"] is False
)
guards = storage.get("guards") or {}
storage_ok = (
    storage.get("status") == "ok"
    and storage.get("storage_mode") == "partitioned"
    and all(
        guards.get(name) is True
        for name in ("maintenance_fresh", "current_partition_present", "retention_current")
    )
)

payload = {
    "generated_at": datetime.now(UTC).isoformat(),
    "mode": "phase14_observation_only",
    "runtime": {
        "deployed_head": "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "v3_runtime": "/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770",
        "v4_runtime": "/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574",
        "database_sessions_read_only": True,
        "production_files_created": False,
        "service_or_timer_mutation": False,
        "checkout_mutation": False,
    },
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "research_zero_money": True,
    },
    "v3_paper": v3,
    "v4_forward_coverage": v4,
    "storage": storage,
    "integrity": {
        "research_zero_money": True,
        "v4": {**v4_integrity, "ok": v4_ok},
        "storage_ok": storage_ok,
        "all_observation_guards_ok": v4_ok and storage_ok,
    },
}
print(json.dumps(payload, indent=2, sort_keys=True))
PY

echo "PHASE14_OBSERVATION=PASS" >&2
REMOTE

REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "LOCAL_MAIN=$LOCAL_HEAD"
echo "This helper is read-only: no production checkout, service, timer, database, or filesystem mutation."

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
