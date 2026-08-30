#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
HOST_ROOT=/opt/bp
REPO="${BP_REPO:-$HOST_ROOT}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
HOST_PY="$HOST_ROOT/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase14-live-readiness
RUNTIME_ROOT=/var/lib/bp/phase14-runtime
VENV="$RUNTIME_ROOT/bp-phase14-venv-${EXPECTED_HEAD:0:12}-$$"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 14 host acceptance must run as root" >&2
  exit 2
fi

actual_head="${BP_VERIFIED_HEAD:-}"
if [[ -d "$REPO/.git" ]]; then
  actual_head=$(git -C "$REPO" rev-parse HEAD)
fi
if [[ -z "$actual_head" || "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=candidate_provenance_mismatch" >&2
  echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
  echo "ACTUAL_HEAD=${actual_head:-missing}" >&2
  exit 2
fi

required_files=(
  "$REPO/src/bp_engine/execution/live.py"
  "$REPO/src/bp_engine/execution/live_client.py"
  "$REPO/src/bp_engine/live_readiness/geoblock.py"
  "$REPO/src/bp_engine/live_readiness/risk.py"
  "$REPO/src/bp_engine/live_readiness/service.py"
  "$REPO/scripts/run_live_readiness.py"
  "$HOST_ROOT/scripts/storage_maintenance.py"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing Phase 14 candidate or host file: $path" >&2
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
if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 14 acceptance requires research mode, live disabled, and zero money limits" >&2
  exit 3
fi

services=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
)
assert_services_active() {
  local service
  for service in "${services[@]}"; do
    if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
      echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
      echo "REASON=service_not_active" >&2
      echo "SERVICE=$service" >&2
      exit 4
    fi
  done
}

assert_services_active
install -d -o bp -g bp "$EVIDENCE_ROOT" "$RUNTIME_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

set +e
PRE_DISK=$(sudo -u bp "$HOST_PY" "$HOST_ROOT/scripts/storage_maintenance.py" disk-health \
  --env-file "$ENV_FILE" --path "$RUNTIME_ROOT" \
  2> "$run_dir/storage-disk-health-before.stderr")
DISK_HEALTH_RC=$?
set -e
printf '%s\n' "$PRE_DISK" > "$run_dir/storage-disk-health-before.json"
if ! read -r DISK_STATUS_BEFORE DISK_FREE_BYTES_BEFORE < <(
  printf '%s' "$PRE_DISK" |
    "$HOST_PY" -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["free_bytes"])'
); then
  echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=disk_not_ok" >&2
  echo "DISK_STATUS_BEFORE=error" >&2
  echo "DISK_FREE_BYTES_BEFORE=unknown" >&2
  exit 6
fi
if [[ "$DISK_HEALTH_RC" -ne 0 || "$DISK_STATUS_BEFORE" != "ok" ]]; then
  echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=disk_not_ok" >&2
  echo "DISK_STATUS_BEFORE=$DISK_STATUS_BEFORE" >&2
  echo "DISK_FREE_BYTES_BEFORE=$DISK_FREE_BYTES_BEFORE" >&2
  exit 6
fi

echo "DISK_STATUS_BEFORE=$DISK_STATUS_BEFORE"
echo "DISK_FREE_BYTES_BEFORE=$DISK_FREE_BYTES_BEFORE"

cleanup() {
  set +e
  rm -rf "$VENV"
  set -e
}
trap cleanup EXIT

sudo -u bp "$HOST_PY" -m venv "$VENV"
sudo -u bp "$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "$REPO" \
  > "$run_dir/candidate-python-install.txt"

assert_services_active

set +e
sudo -u bp env BP_ENV_FILE="$ENV_FILE" "$VENV/bin/python" - <<'PY' \
  > "$run_dir/non-spending-acceptance.txt" 2>&1
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version

import polymarket
from sqlalchemy import create_engine, func, insert, select

from bp_engine.config import Settings, TradingMode, get_settings
from bp_engine.execution.live import InterlockDecision, PolymarketLiveExecutionGateway
from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.live_readiness.geoblock import GeoblockClient, GeoblockError
from bp_engine.live_readiness.models import LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.service import LiveReadinessService
from bp_engine.storage import schema

_ = polymarket
sdk_version = version("polymarket-client")
if not sdk_version:
    raise SystemExit("SDK version unavailable")
print("SDK_IMPORT=PASS")

settings = get_settings()
if settings.mode != TradingMode.RESEARCH:
    raise SystemExit("MODE is not research")
if settings.live_trading_enabled is not False:
    raise SystemExit("LIVE_TRADING_ENABLED is not false")
if Decimal(str(settings.max_trade_size_usd)) != Decimal("0"):
    raise SystemExit("MAX_TRADE_SIZE_USD is not zero")
if Decimal(str(settings.max_daily_loss_usd)) != Decimal("0"):
    raise SystemExit("MAX_DAILY_LOSS_USD is not zero")
if Decimal(str(settings.max_total_exposure_usd)) != Decimal("0"):
    raise SystemExit("total exposure limit is not zero")
if int(settings.max_consecutive_losses) != 0:
    raise SystemExit("consecutive loss limit is not zero")

production_engine = create_engine(settings.database_url, pool_pre_ping=True)
schema.metadata.create_all(production_engine)

def production_order_counts() -> tuple[int, int]:
    with production_engine.connect() as connection:
        intents = int(
            connection.execute(select(func.count()).select_from(schema.live_order_intents)).scalar_one()
        )
        events = int(
            connection.execute(select(func.count()).select_from(schema.live_order_events)).scalar_one()
        )
    return intents, events

before_counts = production_order_counts()

try:
    geoblock = GeoblockClient(url=settings.polymarket_geoblock_url).check()
except GeoblockError:
    geoblock_token = "error"
else:
    geoblock_token = "true" if geoblock.blocked else "false"
print(f"GEOBLOCK_BLOCKED={geoblock_token}")

observed_at = datetime.now(UTC)

def live_prediction(seed: str, condition_id: str) -> LivePrediction:
    market_start = observed_at - timedelta(minutes=4)
    market_end = observed_at + timedelta(minutes=1)
    return LivePrediction(
        prediction_id=seed * 64,
        semantic_sha256=("f" if seed != "f" else "e") * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id=condition_id,
        slug=f"btc-updown-5m-{condition_id}",
        horizon_seconds=300,
        market_start_at=market_start,
        market_end_at=market_end,
        scheduled_at=observed_at,
        recorded_at=observed_at,
        lateness_ms=0,
        up_token_id=f"{condition_id}-up",
        down_token_id=f"{condition_id}-down",
        source_calibration_run_id="phase9-source",
        source_calibration_semantic_sha256="3" * 64,
        source_backtest_run_id="phase8-source",
        source_backtest_semantic_sha256="4" * 64,
        source_training_run_id="phase7-source",
        source_training_semantic_sha256="5" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="6" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="7" * 64,
        edge_config={
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.02],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        edge_config_sha256="8" * 64,
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=0.72,
        calibrated_probability=0.72,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.60,
        market_probability_observed_at=observed_at,
        market_probability_downloaded_at=observed_at,
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": f"{condition_id}-up", "fidelity": "1"},
        market_probability_response_sha256="9" * 64,
        up_best_bid=0.59,
        up_best_ask=0.60,
        up_book_cutoff_at=observed_at,
        up_book_fresh=True,
        down_best_bid=0.39,
        down_best_ask=0.41,
        down_book_cutoff_at=observed_at,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=True,
        decision_reason="trade",
        selected_ask=0.60,
        selected_bid=0.59,
        selected_spread=0.01,
        fee=0.0168,
        slippage_buffer=0.01,
        raw_edge=0.12,
        cost_adjusted_edge=0.0932,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True, "reason": "trade"},
        input_fingerprint="a" * 64,
    )


def build_case(seed: str, condition_id: str):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    repository = LiveReadinessRepository()
    prediction = live_prediction(seed, condition_id)
    prediction_repository = LivePredictionRepository()
    draft = build_paper_order(
        asdict(prediction),
        PaperExecutionConfig(order_ttl_ms=30_000),
        Decimal("100"),
    )
    if not isinstance(draft, PaperOrderDraft):
        raise SystemExit("synthetic order draft was not created")
    with engine.begin() as connection:
        prediction_repository.store(connection, prediction)
        repository.store_reconciliation_run(
            connection,
            observed_at=observed_at - timedelta(milliseconds=500),
            unresolved_count=0,
            critical_count=0,
            evidence={
                "account_snapshot": {
                    "realized_daily_pnl_usd": "0",
                    "consecutive_losses": 0,
                    "total_exposure_usd": "0",
                }
            },
        )
        connection.execute(
            insert(schema.market_state_1s).values(
                bucket_at=observed_at - timedelta(seconds=1),
                state_key=f"polymarket:market:{condition_id}:up",
                source="polymarket",
                stream="market",
                instrument=condition_id,
                market_id=condition_id,
                asset_id=f"{condition_id}-up",
                last_event_at=observed_at - timedelta(milliseconds=250),
                state={
                    "best_bid": "0.59",
                    "best_ask": "0.60",
                    "bid_depth": "50",
                    "ask_depth": "50",
                },
            )
        )
    return engine, repository, draft.request


def healthy_policy() -> LiveRiskPolicy:
    return LiveRiskPolicy(
        max_trade_size_usd=Decimal("10"),
        max_total_exposure_usd=Decimal("25"),
        max_daily_loss_usd=Decimal("10"),
        max_consecutive_losses=3,
        min_edge=Decimal("0.02"),
        min_probability=Decimal("0.60"),
        min_liquidity_usd=Decimal("1"),
        max_spread=Decimal("0.05"),
        max_prediction_age_seconds=Decimal("10"),
        min_time_to_expiry_seconds=Decimal("10"),
        cooldown_seconds=Decimal("0"),
    )


def fail_client_factory():
    raise AssertionError("client factory must not be called")

interlock_engine, interlock_repository, interlock_request = build_case(
    "b", "phase14-host-interlock"
)
interlock_gateway = PolymarketLiveExecutionGateway(
    engine=interlock_engine,
    repository=interlock_repository,
    policy=healthy_policy(),
    client_factory=fail_client_factory,
    interlock=lambda _now: InterlockDecision(
        eligible=False,
        reasons=("live_trading_disabled",),
    ),
    api_health=lambda: True,
    now=lambda: observed_at,
)
interlock_ack = interlock_gateway.submit_order(interlock_request)
if interlock_ack.accepted or interlock_ack.reason != "live_interlock_blocked":
    raise SystemExit(f"interlock did not block submission: {interlock_ack}")
print("INTERLOCK_BLOCKS_SUBMISSION=PASS")
interlock_engine.dispose()

risk_engine, risk_repository, risk_request = build_case("c", "phase14-host-risk")
zero_policy = LiveRiskPolicy(
    max_trade_size_usd=Decimal("0"),
    max_total_exposure_usd=Decimal("0"),
    max_daily_loss_usd=Decimal("0"),
    max_consecutive_losses=0,
    min_edge=Decimal("0.02"),
    min_probability=Decimal("0.60"),
    min_liquidity_usd=Decimal("1"),
    max_spread=Decimal("0.05"),
    max_prediction_age_seconds=Decimal("10"),
    min_time_to_expiry_seconds=Decimal("10"),
    cooldown_seconds=Decimal("0"),
)
risk_gateway = PolymarketLiveExecutionGateway(
    engine=risk_engine,
    repository=risk_repository,
    policy=zero_policy,
    client_factory=fail_client_factory,
    interlock=lambda _now: InterlockDecision(eligible=True),
    api_health=lambda: True,
    now=lambda: observed_at,
)
risk_ack = risk_gateway.submit_order(risk_request)
if risk_ack.accepted or risk_ack.reason != "trade_size_limit_exceeded":
    raise SystemExit(f"zero-limit risk policy did not block submission: {risk_ack}")
print("RISK_RULES=PASS")
risk_engine.dispose()

reconciliation_engine = create_engine("sqlite://")
schema.metadata.create_all(reconciliation_engine)
reconciliation_service = LiveReadinessService(
    engine=reconciliation_engine,
    repository=LiveReadinessRepository(),
    settings=Settings(),
    activation_loader=lambda **_kwargs: None,
    kill_switch_probe=lambda _path: True,
    geoblock_check=lambda **_kwargs: None,
    sdk_health=lambda: False,
    wallet_configured=lambda: False,
)
reconciliation = reconciliation_service.reconcile_snapshot(
    official_orders=(),
    observed_at=observed_at,
)
if reconciliation.unresolved_count != 0 or reconciliation.critical_discrepancy_count != 0:
    raise SystemExit(f"isolated reconciliation was not clean: {reconciliation}")
print("RECONCILIATION=PASS")
reconciliation_engine.dispose()

after_counts = production_order_counts()
production_engine.dispose()
if after_counts != before_counts:
    raise SystemExit(
        "production live order ledger changed during non-spending acceptance: "
        f"before={before_counts} after={after_counts}"
    )
print("REAL_ORDER_SIDE_EFFECTS=0")
PY
acceptance_rc=$?
set -e
if [[ "$acceptance_rc" -ne 0 ]]; then
  echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=non_spending_acceptance_failed" >&2
  tail -n 160 "$run_dir/non-spending-acceptance.txt" || true
  exit "$acceptance_rc"
fi

for token in \
  SDK_IMPORT=PASS \
  INTERLOCK_BLOCKS_SUBMISSION=PASS \
  RISK_RULES=PASS \
  RECONCILIATION=PASS \
  REAL_ORDER_SIDE_EFFECTS=0; do
  if ! grep -qx "$token" "$run_dir/non-spending-acceptance.txt"; then
    echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
    echo "REASON=missing_acceptance_token" >&2
    echo "TOKEN=$token" >&2
    exit 7
  fi
done
if ! grep -Eq '^GEOBLOCK_BLOCKED=(true|false|error)$' "$run_dir/non-spending-acceptance.txt"; then
  echo "PHASE14_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=missing_geoblock_token" >&2
  exit 7
fi

assert_services_active
echo "SERVICES_ACTIVE=PASS" | tee "$run_dir/services.txt"

echo "CANDIDATE_HEAD=$EXPECTED_HEAD"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
grep -E '^(SDK_IMPORT|INTERLOCK_BLOCKS_SUBMISSION|RISK_RULES|RECONCILIATION|GEOBLOCK_BLOCKED|REAL_ORDER_SIDE_EFFECTS)=' \
  "$run_dir/non-spending-acceptance.txt"
echo "LIVE_GATE_ELIGIBLE=false"
echo "EVIDENCE_DIR=$run_dir"
echo "PHASE14_HOST_ACCEPTANCE=PASS"
