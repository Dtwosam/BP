#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
HOST_ROOT=/opt/bp
REPO="${BP_REPO:-$HOST_ROOT}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
HOST_PY="$HOST_ROOT/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase12-paper-execution
RUNTIME_ROOT=/var/lib/bp/phase12-runtime
WINDOW_SECONDS="${PHASE12_ACCEPTANCE_WINDOW_SECONDS:-600}"
MAX_WINDOWS="${PHASE12_ACCEPTANCE_MAX_WINDOWS:-3}"
POLL_SECONDS="${PHASE12_ACCEPTANCE_POLL_SECONDS:-5}"
VENV="$RUNTIME_ROOT/bp-phase12-venv-${EXPECTED_HEAD:0:12}-$$"
SOURCE_5M="phase9-300-c9f0e00eb7836af08008c66909f8f179"
SOURCE_15M="phase9-900-15c234f25588b23cce73a12f87a2e2ea"
PREDICTOR_PID=""

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 12 host acceptance must run as root" >&2
  exit 2
fi
for value in "$WINDOW_SECONDS" "$MAX_WINDOWS" "$POLL_SECONDS"; do
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "Phase 12 acceptance timing values must be positive integers" >&2
    exit 2
  fi
done

actual_head="${BP_VERIFIED_HEAD:-}"
if [[ -d "$REPO/.git" ]]; then
  actual_head=$(git -C "$REPO" rev-parse HEAD)
fi
if [[ -z "$actual_head" || "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "PHASE12_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=candidate_provenance_mismatch" >&2
  echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
  echo "ACTUAL_HEAD=${actual_head:-missing}" >&2
  exit 2
fi

required_files=(
  "$REPO/deploy/systemd/bp-paper-execution.service"
  "$REPO/src/bp_engine/execution/cli.py"
  "$REPO/src/bp_engine/execution/service.py"
  "$REPO/src/bp_engine/live_prediction/cli.py"
  "$REPO/src/bp_engine/dashboard/repository.py"
  "$REPO/migrations/0011_paper_execution.sql"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing Phase 12 candidate file: $path" >&2
    exit 2
  fi
done
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" ]]; then
  echo "missing production environment or host Python" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
DATABASE_URL=$(read_env DATABASE_URL)
if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 12 requires research mode, live disabled, and zero real-money limits" >&2
  exit 3
fi
if [[ -z "$DATABASE_URL" ]]; then
  echo "DATABASE_URL is missing from $ENV_FILE" >&2
  exit 3
fi

for service in \
  bp-recorder.service \
  bp-postgres.service \
  bp-dashboard-api.service \
  bp-dashboard-web.service; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "$service must be active before Phase 12 acceptance" >&2
    exit 4
  fi
done

UNIT="$REPO/deploy/systemd/bp-paper-execution.service"
for contract in \
  'User=bp' \
  'Group=bp' \
  'Environment=MODE=research' \
  'Environment=LIVE_TRADING_ENABLED=false' \
  'Environment=MAX_TRADE_SIZE_USD=0' \
  'Environment=MAX_DAILY_LOSS_USD=0' \
  'NoNewPrivileges=true' \
  'ProtectHome=true' \
  'ProtectSystem=full' \
  'IPAddressDeny=any' \
  'IPAddressAllow=localhost'; do
  if ! grep -qx "$contract" "$UNIT"; then
    echo "paper worker unit violates contract: $contract" >&2
    exit 4
  fi
done
if grep -Eq -- '--host|--port' "$UNIT"; then
  echo "paper worker must not expose a listener" >&2
  exit 4
fi

install -d -o bp -g bp "$EVIDENCE_ROOT" "$RUNTIME_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

cleanup() {
  set +e
  if [[ -n "$PREDICTOR_PID" ]]; then
    kill "$PREDICTOR_PID" >/dev/null 2>&1 || true
    wait "$PREDICTOR_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$VENV"
  set -e
}
trap cleanup EXIT

sudo -u bp "$HOST_PY" -m venv "$VENV"
sudo -u bp "$VENV/bin/python" -m pip install --disable-pip-version-check "$REPO" \
  > "$run_dir/candidate-python-install.txt"

run_candidate() {
  sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    DATABASE_URL="$DATABASE_URL" \
    "$@"
}

run_candidate "$VENV/bin/python" - <<'PY' > "$run_dir/schema-check.txt"
from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.storage import schema

settings = get_settings()
engine = create_engine(settings.database_url)
schema.metadata.create_all(engine)
required = {"paper_orders", "paper_fills", "paper_order_terminal_events", "paper_settlements"}
with engine.connect() as connection:
    names = set(schema.metadata.tables)
if not required.issubset(names):
    raise SystemExit(f"paper schema metadata incomplete: {sorted(required - names)}")
print("PAPER_SCHEMA=READY")
engine.dispose()
PY

START_ISO=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
sudo -u bp env \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  DATABASE_URL="$DATABASE_URL" \
  "$VENV/bin/python" -m bp_engine.live_prediction run \
    --source-calibration-run-id "$SOURCE_5M" \
    --source-calibration-run-id "$SOURCE_15M" \
    --env-file "$ENV_FILE" \
    --database-url "$DATABASE_URL" \
    --poll-interval-seconds 1 \
    --max-lateness-seconds 10 \
    > "$run_dir/predictor.log" 2>&1 &
PREDICTOR_PID=$!
printf 'PREDICTOR_PID=%s\n' "$PREDICTOR_PID" > "$run_dir/predictor-process.txt"
sleep 2
if ! kill -0 "$PREDICTOR_PID" >/dev/null 2>&1; then
  echo "PHASE12_HOST_ACCEPTANCE=FAIL"
  echo "REASON=prospective_predictor_failed_to_start"
  tail -n 120 "$run_dir/predictor.log" || true
  exit 5
fi

paper_once() {
  run_candidate "$VENV/bin/python" -m bp_engine.execution --once \
    | tee -a "$run_dir/paper-worker-runs.jsonl"
}

find_prospective() {
  run_candidate "$VENV/bin/python" - "$START_ISO" <<'PY'
from __future__ import annotations

import sys
from datetime import datetime
from sqlalchemy import create_engine, select

from bp_engine.config import get_settings
from bp_engine.storage import schema

cutoff = datetime.fromisoformat(sys.argv[1])
engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    rows = connection.execute(
        select(
            schema.live_predictions.c.prediction_id,
            schema.live_predictions.c.trade,
            schema.live_predictions.c.executable,
            schema.live_predictions.c.recorded_at,
            schema.live_predictions.c.horizon_seconds,
        )
        .where(
            schema.live_predictions.c.recorded_at >= cutoff,
            schema.live_predictions.c.horizon_seconds.in_((300, 900)),
        )
        .order_by(schema.live_predictions.c.recorded_at, schema.live_predictions.c.id)
    ).mappings().all()
engine.dispose()
trades = [row for row in rows if row["trade"] is True and row["executable"] is True]
if trades:
    print(f"TRADE:{trades[-1]['prediction_id']}")
elif rows:
    print(f"NO_TRADE:{rows[-1]['prediction_id']}")
else:
    print("NONE")
PY
}

paper_once
candidate_state=NONE
target_prediction_id=""
for window in $(seq 1 "$MAX_WINDOWS"); do
  deadline=$(( $(date +%s) + WINDOW_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if ! kill -0 "$PREDICTOR_PID" >/dev/null 2>&1; then
      echo "PHASE12_HOST_ACCEPTANCE=FAIL"
      echo "REASON=prospective_predictor_exited_during_acceptance"
      tail -n 120 "$run_dir/predictor.log" || true
      exit 5
    fi
    paper_once
    candidate_state=$(find_prospective)
    if [[ "$candidate_state" == TRADE:* ]]; then
      target_prediction_id=${candidate_state#TRADE:}
      break 2
    fi
    if [[ "$candidate_state" == NO_TRADE:* ]]; then
      target_prediction_id=${candidate_state#NO_TRADE:}
    fi
    sleep "$POLL_SECONDS"
  done
  echo "EXTENDED_WINDOW=$window" | tee -a "$run_dir/acceptance-window.txt"
done

if [[ -z "$target_prediction_id" ]]; then
  echo "PHASE12_HOST_ACCEPTANCE=FAIL"
  echo "REASON=no_prospective_5m_15m_prediction_after_extended_window"
  tail -n 120 "$run_dir/predictor.log" || true
  exit 5
fi

prospective_kind=NO_TRADE
if [[ "$candidate_state" == TRADE:* ]]; then
  prospective_kind=TRADE
  terminal_ready=0
  for _ in $(seq 1 12); do
    paper_once
    if run_candidate "$VENV/bin/python" - "$target_prediction_id" <<'PY' > "$run_dir/target-ready.txt"
from __future__ import annotations

import sys
from sqlalchemy import create_engine, select

from bp_engine.config import get_settings
from bp_engine.storage import schema

prediction_id = sys.argv[1]
engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    order = connection.execute(
        select(schema.paper_orders).where(schema.paper_orders.c.prediction_id == prediction_id)
    ).mappings().one_or_none()
    if order is None:
        raise SystemExit(1)
    terminal = connection.execute(
        select(schema.paper_order_terminal_events).where(
            schema.paper_order_terminal_events.c.paper_order_id == order["paper_order_id"]
        )
    ).mappings().one_or_none()
    if terminal is None:
        raise SystemExit(1)
print(f"PAPER_ORDER_ID={order['paper_order_id']}")
print(f"TERMINAL_STATUS={terminal['terminal_status']}")
engine.dispose()
PY
    then
      terminal_ready=1
      break
    fi
    sleep 2
  done
  if (( ! terminal_ready )); then
    echo "PHASE12_HOST_ACCEPTANCE=FAIL"
    echo "REASON=prospective_trade_did_not_reach_terminal_paper_state"
    exit 5
  fi
fi

fingerprint_target() {
  run_candidate "$VENV/bin/python" - "$target_prediction_id" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
from sqlalchemy import create_engine, select

from bp_engine.config import get_settings
from bp_engine.storage import schema

prediction_id = sys.argv[1]
engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    order = connection.execute(
        select(schema.paper_orders).where(schema.paper_orders.c.prediction_id == prediction_id)
    ).mappings().one_or_none()
    values: dict[str, object] = {
        "prediction_id": prediction_id,
        "order": None,
        "fills": [],
        "terminal": None,
        "settlements": [],
    }
    if order is not None:
        order_id = order["paper_order_id"]
        values["order"] = str(order["semantic_sha256"])
        values["fills"] = [
            str(value)
            for value in connection.execute(
                select(schema.paper_fills.c.semantic_sha256)
                .where(schema.paper_fills.c.paper_order_id == order_id)
                .order_by(schema.paper_fills.c.id)
            ).scalars().all()
        ]
        values["terminal"] = connection.execute(
            select(schema.paper_order_terminal_events.c.semantic_sha256).where(
                schema.paper_order_terminal_events.c.paper_order_id == order_id
            )
        ).scalar_one_or_none()
        values["settlements"] = [
            str(value)
            for value in connection.execute(
                select(schema.paper_settlements.c.semantic_sha256)
                .where(schema.paper_settlements.c.paper_order_id == order_id)
                .order_by(schema.paper_settlements.c.id)
            ).scalars().all()
        ]
engine.dispose()
blob = json.dumps(values, sort_keys=True, default=str, separators=(",", ":")).encode()
print(hashlib.sha256(blob).hexdigest())
PY
}

before_fingerprint=$(fingerprint_target)
run_candidate "$VENV/bin/python" -m bp_engine.execution --once \
  | tee -a "$run_dir/paper-worker-runs.jsonl"
after_fingerprint=$(fingerprint_target)
if [[ "$before_fingerprint" != "$after_fingerprint" ]]; then
  echo "PHASE12_HOST_ACCEPTANCE=FAIL"
  echo "REASON=paper_rerun_not_idempotent_for_target"
  exit 6
fi
echo "IDEMPOTENT_RERUN=PASS" > "$run_dir/idempotency.txt"

run_candidate "$VENV/bin/python" - "$target_prediction_id" "$prospective_kind" <<'PY' > "$run_dir/target-evidence.json"
from __future__ import annotations

import json
import sys
from decimal import Decimal
from sqlalchemy import create_engine, select

from bp_engine.config import get_settings
from bp_engine.storage import schema

prediction_id, kind = sys.argv[1:3]
engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    prediction = connection.execute(
        select(schema.live_predictions).where(schema.live_predictions.c.prediction_id == prediction_id)
    ).mappings().one()
    order = connection.execute(
        select(schema.paper_orders).where(schema.paper_orders.c.prediction_id == prediction_id)
    ).mappings().one_or_none()
    evidence: dict[str, object] = {
        "prediction_id": prediction_id,
        "horizon_seconds": int(prediction["horizon_seconds"]),
        "trade": bool(prediction["trade"]),
        "executable": bool(prediction["executable"]),
        "paper_order_id": None,
        "fill_count": 0,
        "terminal_status": None,
    }
    if kind == "NO_TRADE":
        if prediction["trade"] is not False:
            raise SystemExit("no-trade target is not an immutable trade=false signal")
        if order is not None:
            raise SystemExit("trade=false signal produced a paper order")
    else:
        if prediction["trade"] is not True or prediction["executable"] is not True:
            raise SystemExit("trade target is not eligible")
        if order is None:
            raise SystemExit("eligible prospective signal has no paper order")
        order_id = order["paper_order_id"]
        fills = connection.execute(
            select(schema.paper_fills)
            .where(schema.paper_fills.c.paper_order_id == order_id)
            .order_by(schema.paper_fills.c.fill_at, schema.paper_fills.c.id)
        ).mappings().all()
        terminal = connection.execute(
            select(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order_id
            )
        ).mappings().one()
        for fill in fills:
            if fill["fill_at"] < order["arrival_at"] or fill["fill_at"] > order["expires_at"]:
                raise SystemExit("paper fill time is non-causal")
            if fill["replay_cutoff_at"] > order["expires_at"]:
                raise SystemExit("paper replay used post-expiry evidence")
            if fill["book_anchor_event_id"] is None:
                raise SystemExit("paper fill lacks causal book anchor")
            if Decimal(str(fill["price"])) > Decimal(str(order["limit_price"])):
                raise SystemExit("paper fill exceeded order limit")
        if not fills and terminal["terminal_status"] not in {
            "EXPIRED",
            "MARKET_ENDED_UNFILLED",
        }:
            raise SystemExit("zero-fill order lacks an explicit no-fill terminal reason")
        evidence.update(
            paper_order_id=str(order_id),
            fill_count=len(fills),
            terminal_status=str(terminal["terminal_status"]),
        )
print(json.dumps(evidence, sort_keys=True, default=str))
engine.dispose()
PY

run_candidate "$VENV/bin/python" - <<'PY' > "$run_dir/reconciliation.json"
from __future__ import annotations

import json
from decimal import Decimal
from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.dashboard.repository import PostgresDashboardRepository

engine = create_engine(get_settings().database_url)
evidence = PostgresDashboardRepository(engine).get_paper_execution_evidence()
paper = evidence["paper_pnl"]
reconciliation = paper["reconciliation"]
if reconciliation["status"] != "OK" or int(reconciliation["violation_count"]) != 0:
    raise SystemExit(f"paper reconciliation failed: {reconciliation!r}")
if Decimal(str(paper["current_cash"])) < Decimal("0"):
    raise SystemExit("paper cash is negative")
print(json.dumps({
    "paper_orders": len(evidence["paper_orders"]),
    "paper_fills": len(evidence["paper_fills"]),
    "paper_settlements": len(evidence["paper_settlements"]),
    "current_cash": str(paper["current_cash"]),
    "realized_pnl": str(paper["realized_pnl"]),
    "reconciliation": reconciliation,
}, sort_keys=True, default=str))
engine.dispose()
PY

for service in \
  bp-recorder.service \
  bp-postgres.service \
  bp-dashboard-api.service \
  bp-dashboard-web.service; do
  if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
    echo "PHASE12_HOST_ACCEPTANCE=FAIL"
    echo "REASON=phase11_service_disturbed:$service"
    exit 7
  fi
done

{
  echo "PHASE12_HOST_ACCEPTANCE=PASS"
  echo "HEAD=$EXPECTED_HEAD"
  echo "PROSPECTIVE_KIND=$prospective_kind"
  echo "PROSPECTIVE_PREDICTION_ID=$target_prediction_id"
  echo "RECONCILIATION_STATUS=OK"
  echo "IDEMPOTENT_RERUN=PASS"
  echo "RECORDER_STATUS=$(systemctl is-active bp-recorder.service)"
  echo "POSTGRES_STATUS=$(systemctl is-active bp-postgres.service)"
  echo "DASHBOARD_API_STATUS=$(systemctl is-active bp-dashboard-api.service)"
  echo "DASHBOARD_WEB_STATUS=$(systemctl is-active bp-dashboard-web.service)"
  echo "EVIDENCE_DIR=$run_dir"
} | tee "$run_dir/summary.txt"
