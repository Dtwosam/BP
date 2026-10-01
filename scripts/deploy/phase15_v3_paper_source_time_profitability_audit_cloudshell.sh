#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_V3_PAPER_AUDIT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE15_V3_PAPER_AUDIT_ZONE:-us-east1-c}"
VM="${PHASE15_V3_PAPER_AUDIT_VM:-bp-recorder}"

fail() {
  printf 'PHASE15_V3_PAPER_SOURCE_TIME_PROFITABILITY_AUDIT=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"

git fetch origin main --quiet || fail "fetch_main_failed"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1

REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
V3_CURRENT=/var/lib/bp/runtime/v3-paper-current

fail() {
  printf 'PHASE15_V3_PAPER_SOURCE_TIME_PROFITABILITY_AUDIT=FAIL:%s\n' "$1" >&2
  exit 1
}

[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"
[[ -e "$V3_CURRENT" ]] || fail "v3_runtime_link_missing"

sudo -u bp env \
  PYTHONPATH="$V3_CURRENT/src" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  "$REPO/.venv/bin/python" - <<'PY'
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from collections.abc import Mapping
from statistics import median
from typing import Any

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)

MAX_SOURCE_AGE_SECONDS = Decimal("2")
MAX_FUTURE_SKEW_SECONDS = Decimal("1")
ZERO = Decimal("0")


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def decimal(value: object | None) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def latest_by(rows: list[dict[str, Any]], key: str, timestamp: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row[key])
        existing = result.get(identity)
        if existing is None or utc(row[timestamp]) > utc(existing[timestamp]):
            result[identity] = row
    return result


def selected_token_event_health(connection, order: Mapping[str, Any]) -> dict[str, Any]:
    observed_at = utc(order["submitted_at"])
    condition_id = str(order["condition_id"])
    token_id = str(order["token_id"])

    rows = connection.execute(
        select(
            schema.raw_market_events.c.received_at,
            schema.raw_market_events.c.source_timestamp,
            schema.raw_market_events.c.payload,
            schema.raw_market_events.c.id,
        )
        .where(
            schema.raw_market_events.c.source == "polymarket",
            schema.raw_market_events.c.stream == "market",
            schema.raw_market_events.c.instrument == condition_id,
            schema.raw_market_events.c.event_type == "price_change",
            schema.raw_market_events.c.source_timestamp.is_not(None),
            schema.raw_market_events.c.received_at <= observed_at,
        )
        .order_by(
            schema.raw_market_events.c.received_at.desc(),
            schema.raw_market_events.c.id.desc(),
        )
        .limit(128)
    ).mappings().all()

    chosen = None
    for candidate in rows:
        payload = candidate["payload"]
        if not isinstance(payload, Mapping):
            continue
        changes = payload.get("price_changes")
        if not isinstance(changes, list):
            continue
        if any(
            isinstance(change, Mapping)
            and str(change.get("asset_id") or "") == token_id
            for change in changes
        ):
            chosen = candidate
            break

    if chosen is None:
        return {
            "eligible": False,
            "reason": "polymarket_source_time_unavailable",
            "event_id": None,
            "received_at": None,
            "source_timestamp": None,
            "source_age_seconds": None,
            "transport_lag_seconds": None,
        }

    received_at = utc(chosen["received_at"])
    source_timestamp = utc(chosen["source_timestamp"])
    source_age = Decimal(str((observed_at - source_timestamp).total_seconds()))
    transport_lag = Decimal(str((received_at - source_timestamp).total_seconds()))

    if source_age < -MAX_FUTURE_SKEW_SECONDS:
        reason = "polymarket_source_clock_ahead"
    elif source_age > MAX_SOURCE_AGE_SECONDS:
        reason = "polymarket_source_lag"
    else:
        reason = None

    return {
        "eligible": reason is None,
        "reason": reason,
        "event_id": int(chosen["id"]),
        "received_at": received_at.isoformat(),
        "source_timestamp": source_timestamp.isoformat(),
        "source_age_seconds": source_age,
        "transport_lag_seconds": transport_lag,
    }


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [decimal(row["realized_pnl"]) for row in rows]
    costs = [decimal(row["total_fill_cost"]) for row in rows]
    positive = sum((value for value in pnl_values if value > 0), ZERO)
    negative = sum((value for value in pnl_values if value < 0), ZERO)
    realized = sum(pnl_values, ZERO)
    cost = sum(costs, ZERO)
    wins = sum(value > 0 for value in pnl_values)
    losses = sum(value < 0 for value in pnl_values)
    breakeven = sum(value == 0 for value in pnl_values)

    by_side: dict[str, dict[str, Any]] = {}
    for side in ("up", "down"):
        side_rows = [row for row in rows if str(row["selected_side"]).lower() == side]
        side_pnl = [decimal(row["realized_pnl"]) for row in side_rows]
        by_side[side] = {
            "trades": len(side_rows),
            "wins": sum(value > 0 for value in side_pnl),
            "losses": sum(value < 0 for value in side_pnl),
            "realized_pnl": sum(side_pnl, ZERO),
        }

    return {
        "settled_trades": len(rows),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": (wins / len(rows)) if rows else None,
        "realized_pnl": realized,
        "total_fill_cost": cost,
        "return_on_cost": (realized / cost) if cost > 0 else None,
        "average_realized_pnl": (realized / len(rows)) if rows else None,
        "gross_positive_pnl": positive,
        "gross_negative_pnl": negative,
        "profit_factor": (
            positive / abs(negative)
            if negative < 0
            else (None if positive == 0 else "Infinity")
        ),
        "by_side": by_side,
    }


settings = Settings(_env_file="/etc/bp/bp.env")
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c default_transaction_read_only=on"},
)

try:
    with engine.connect() as connection:
        read_only = connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
        if read_only != "on":
            raise SystemExit("database session is not read-only")

        orders = [
            dict(row)
            for row in connection.execute(
                select(schema.paper_orders)
                .where(
                    schema.paper_orders.c.execution_version
                    == V3_PAPER_EXECUTION_VERSION
                )
                .order_by(
                    schema.paper_orders.c.submitted_at,
                    schema.paper_orders.c.id,
                )
            ).mappings()
        ]
        order_ids = tuple(str(row["paper_order_id"]) for row in orders)

        settlements: list[dict[str, Any]] = []
        if order_ids:
            settlements = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_settlements)
                    .where(schema.paper_settlements.c.paper_order_id.in_(order_ids))
                    .order_by(
                        schema.paper_settlements.c.settled_at,
                        schema.paper_settlements.c.id,
                    )
                ).mappings()
            ]

        latest_settlement = latest_by(
            settlements,
            key="paper_order_id",
            timestamp="settled_at",
        )
        order_by_id = {str(row["paper_order_id"]): row for row in orders}

        baseline: list[dict[str, Any]] = []
        guarded: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        source_ages: list[float] = []
        reason_counts: dict[str, int] = {}

        for order_id, settlement in latest_settlement.items():
            order = order_by_id.get(order_id)
            if order is None:
                continue

            health = selected_token_event_health(connection, order)
            source_age = health["source_age_seconds"]
            if source_age is not None:
                source_ages.append(float(source_age))

            combined = {
                **order,
                **settlement,
                "source_time_health": health,
            }
            baseline.append(combined)

            reason = health["reason"] or "eligible"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if health["eligible"]:
                guarded.append(combined)
            else:
                excluded.append(combined)
finally:
    engine.dispose()

baseline_metrics = metrics(baseline)
guarded_metrics = metrics(guarded)
excluded_metrics = metrics(excluded)

excluded_rows = []
for row in sorted(excluded, key=lambda item: utc(item["submitted_at"])):
    health = row["source_time_health"]
    excluded_rows.append(
        {
            "paper_order_id": str(row["paper_order_id"]),
            "prediction_id": str(row["prediction_id"]),
            "condition_id": str(row["condition_id"]),
            "selected_side": str(row["selected_side"]),
            "submitted_at": utc(row["submitted_at"]).isoformat(),
            "signal_selected_ask": row["signal_selected_ask"],
            "fill_cost": row["total_fill_cost"],
            "realized_pnl": row["realized_pnl"],
            "source_time_reason": health["reason"],
            "source_age_seconds": health["source_age_seconds"],
            "transport_lag_seconds": health["transport_lag_seconds"],
            "source_timestamp": health["source_timestamp"],
            "received_at": health["received_at"],
            "event_id": health["event_id"],
        }
    )

result = {
    "generated_at": datetime.now(UTC).isoformat(),
    "audit": "v3_frozen_paper_source_time_profitability_v1",
    "prediction_version": V3_PAPER_PREDICTION_VERSION,
    "execution_version": V3_PAPER_EXECUTION_VERSION,
    "counterfactual_rule": {
        "decision_time": "paper_order.submitted_at == prediction.recorded_at",
        "selected_token_only": True,
        "event_source": "polymarket",
        "event_stream": "market",
        "event_type": "price_change",
        "max_source_age_seconds": str(MAX_SOURCE_AGE_SECONDS),
        "max_future_skew_seconds": str(MAX_FUTURE_SKEW_SECONDS),
        "action_on_failure": "exclude_trade_fail_closed",
        "repricing_performed": False,
        "model_refit_performed": False,
        "threshold_tuning_performed": False,
    },
    "baseline_actual_paper": baseline_metrics,
    "source_time_guarded_counterfactual": guarded_metrics,
    "excluded_by_guard": excluded_metrics,
    "source_time_reason_counts": dict(sorted(reason_counts.items())),
    "source_age_distribution_seconds": {
        "observed_count": len(source_ages),
        "min": min(source_ages) if source_ages else None,
        "median": median(source_ages) if source_ages else None,
        "max": max(source_ages) if source_ages else None,
    },
    "delta": {
        "trades_removed": len(baseline) - len(guarded),
        "realized_pnl_change": (
            decimal(guarded_metrics["realized_pnl"])
            - decimal(baseline_metrics["realized_pnl"])
        ),
        "fill_cost_change": (
            decimal(guarded_metrics["total_fill_cost"])
            - decimal(baseline_metrics["total_fill_cost"])
        ),
    },
    "profitable_after_guard": (
        decimal(guarded_metrics["realized_pnl"]) > 0
        if guarded
        else None
    ),
    "excluded_trades": excluded_rows,
    "safety": {
        "database_session_read_only": True,
        "service_mutation_performed": False,
        "runtime_file_mutation_performed": False,
        "wallet_or_signing_material_read": False,
        "order_submission_attempted": False,
        "real_money_mutation_performed": False,
    },
}

print(json.dumps(result, indent=2, sort_keys=True, default=str))
PY

printf 'PHASE15_V3_PAPER_SOURCE_TIME_PROFITABILITY_AUDIT=PASS\n'
printf 'MUTATIONS_PERFORMED=false\n'
printf 'REAL_ORDER_SUBMITTED=false\n'
REMOTE

REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'AUDIT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
