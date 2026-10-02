#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_SOURCE_TIME_AUDIT_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_SOURCE_TIME_AUDIT_ZONE:-us-east1-c}"
VM="${PHASE14_V4_SOURCE_TIME_AUDIT_VM:-bp-recorder}"

fail() {
  printf 'PHASE14_V4_SOURCE_TIME_AUDIT=FAIL:%s\n' "$1" >&2
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

fail() {
  printf 'PHASE14_V4_SOURCE_TIME_AUDIT=FAIL:%s\n' "$1" >&2
  exit 1
}

[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"
[[ -r "$ENV_FILE" ]] || fail "environment_file_missing"

sudo -u bp env \
  PYTHONPATH="$REPO/src" \
  MODE=research \
  LIVE_TRADING_ENABLED=false \
  MAX_TRADE_SIZE_USD=0 \
  MAX_DAILY_LOSS_USD=0 \
  "$REPO/.venv/bin/python" - <<'PY'
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.storage import schema
SOURCE_TIME_LIMIT_SECONDS = Decimal("2")
LEGACY_FRESHNESS_SECONDS = Decimal("10")
MAX_FUTURE_SKEW_SECONDS = Decimal("1")
SELECTED_OFFSET_SECONDS = 240
RESEARCH_PLAN_VERSION = "v4-gate-b-preregister-v2"
FEATURE_VERSION = "core-v4-regime-aware"
EPOCH_START = datetime(2026, 9, 24, 0, 0, tzinfo=UTC)
EPOCH_END = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
ZERO = Decimal("0")


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def summarize(values: list[Decimal]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "p95": None, "p99": None, "max": None}

    def percentile(q: Decimal) -> Decimal:
        if len(ordered) == 1:
            return ordered[0]
        position = q * Decimal(len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        fraction = position - Decimal(low)
        return ordered[low] + (ordered[high] - ordered[low]) * fraction

    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": Decimal(str(median(ordered))),
        "p95": percentile(Decimal("0.95")),
        "p99": percentile(Decimal("0.99")),
        "max": ordered[-1],
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

        feature_rows = [
            dict(row)
            for row in connection.execute(
                select(
                    schema.market_features.c.condition_id,
                    schema.market_features.c.feature_at,
                    schema.market_features.c.source_cutoffs,
                    schema.market_features.c.features,
                    schema.market_features.c.missing_flags,
                ).where(
                    schema.market_features.c.feature_version == FEATURE_VERSION,
                    schema.market_features.c.feature_offset_seconds == SELECTED_OFFSET_SECONDS,
                    schema.market_features.c.market_start_at >= EPOCH_START,
                    schema.market_features.c.market_start_at < HOLDOUT_START,
                )
            ).mappings()
        ]

        future_cutoff_violations = 0
        polymarket_predictor_key_count = 0
        for row in feature_rows:
            feature_at = utc(row["feature_at"])
            cutoffs = row["source_cutoffs"] or {}
            for cutoff in cutoffs.values():
                parsed = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
                future_cutoff_violations += int(utc(parsed) > feature_at)
            polymarket_predictor_key_count += sum(
                str(key).startswith(("pm_", "polymarket_"))
                for key in (row["features"] or {})
            )

        feed_rows = connection.execute(
            text(
                """
                WITH priced AS (
                    SELECT
                        source,
                        stream,
                        EXTRACT(EPOCH FROM (received_at - source_timestamp)) AS transport_lag_seconds,
                        EXTRACT(
                            EPOCH FROM (
                                received_at - LAG(received_at) OVER (
                                    PARTITION BY source, stream
                                    ORDER BY received_at, id
                                )
                            )
                        ) AS event_gap_seconds
                    FROM raw_market_events
                    WHERE source_timestamp IS NOT NULL
                      AND received_at >= :epoch_start - INTERVAL '1 hour'
                      AND received_at < :HOLDOUT_START
                      AND (
                        (
                          source = 'coinbase'
                          AND stream = 'spot'
                          AND instrument = 'BTC-USD'
                          AND (
                            event_type LIKE 'ticker_%'
                            OR event_type LIKE 'market_trades_%'
                          )
                        )
                        OR
                        (
                          source = 'bybit'
                          AND stream IN ('spot', 'linear')
                          AND instrument = 'BTCUSDT'
                          AND event_type IN ('ticker', 'trade')
                        )
                      )
                )
                SELECT
                    source,
                    stream,
                    COUNT(*) AS event_count,
                    MIN(transport_lag_seconds) AS transport_min,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY transport_lag_seconds
                    ) AS transport_median,
                    percentile_cont(0.95) WITHIN GROUP (
                        ORDER BY transport_lag_seconds
                    ) AS transport_p95,
                    percentile_cont(0.99) WITHIN GROUP (
                        ORDER BY transport_lag_seconds
                    ) AS transport_p99,
                    MAX(transport_lag_seconds) AS transport_max,
                    COUNT(*) FILTER (
                        WHERE transport_lag_seconds > 2
                    ) AS transport_over_2s,
                    COUNT(*) FILTER (
                        WHERE transport_lag_seconds > 10
                    ) AS transport_over_10s,
                    COUNT(*) FILTER (
                        WHERE transport_lag_seconds < -1
                    ) AS source_clock_ahead_over_1s,
                    COUNT(event_gap_seconds) AS gap_count,
                    MIN(event_gap_seconds) AS gap_min,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY event_gap_seconds
                    ) AS gap_median,
                    percentile_cont(0.95) WITHIN GROUP (
                        ORDER BY event_gap_seconds
                    ) AS gap_p95,
                    percentile_cont(0.99) WITHIN GROUP (
                        ORDER BY event_gap_seconds
                    ) AS gap_p99,
                    MAX(event_gap_seconds) AS gap_max,
                    COUNT(*) FILTER (
                        WHERE event_gap_seconds > 10
                    ) AS gaps_over_10s
                FROM priced
                GROUP BY source, stream
                ORDER BY source, stream
                """
            ),
            {
                "epoch_start": EPOCH_START,
                "HOLDOUT_START": HOLDOUT_START,
            },
        ).mappings().all()

        feed_health: dict[str, dict[str, Any]] = {}
        for row in feed_rows:
            key = f'{row["source"]}:{row["stream"]}'
            feed_health[key] = {
                "transport_lag_seconds": {
                    "count": int(row["event_count"]),
                    "min": row["transport_min"],
                    "median": row["transport_median"],
                    "p95": row["transport_p95"],
                    "p99": row["transport_p99"],
                    "max": row["transport_max"],
                },
                "price_event_gap_seconds": {
                    "count": int(row["gap_count"]),
                    "min": row["gap_min"],
                    "median": row["gap_median"],
                    "p95": row["gap_p95"],
                    "p99": row["gap_p99"],
                    "max": row["gap_max"],
                },
                "transport_lag_over_2s": int(row["transport_over_2s"]),
                "transport_lag_over_10s": int(row["transport_over_10s"]),
                "source_clock_ahead_over_1s": int(
                    row["source_clock_ahead_over_1s"]
                ),
                "price_event_gaps_over_10s": int(row["gaps_over_10s"]),
            }

        execution_rows = connection.execute(
            text(
                """
                WITH targets AS (
                    SELECT DISTINCT ON (mf.condition_id)
                        mf.condition_id,
                        mf.feature_at,
                        pm.up_token_id,
                        pm.down_token_id
                    FROM market_features AS mf
                    JOIN polymarket_markets AS pm
                      ON pm.condition_id = mf.condition_id
                    WHERE mf.feature_version = :feature_version
                      AND mf.feature_offset_seconds = :selected_offset
                      AND mf.market_start_at >= :epoch_start
                      AND mf.market_start_at < :HOLDOUT_START
                    ORDER BY mf.condition_id, mf.id DESC
                )
                SELECT
                    t.condition_id,
                    t.feature_at,
                    up_evt.received_at AS up_received_at,
                    up_evt.source_timestamp AS up_source_timestamp,
                    down_evt.received_at AS down_received_at,
                    down_evt.source_timestamp AS down_source_timestamp
                FROM targets AS t
                LEFT JOIN LATERAL (
                    SELECT r.received_at, r.source_timestamp
                    FROM raw_market_events AS r
                    WHERE r.source = 'polymarket'
                      AND r.stream = 'market'
                      AND r.instrument = t.condition_id
                      AND r.event_type = 'price_change'
                      AND r.received_at <= t.feature_at
                      AND r.source_timestamp IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              COALESCE(r.payload->'price_changes', '[]'::jsonb)
                          ) AS change
                          WHERE change->>'asset_id' = t.up_token_id
                      )
                    ORDER BY r.received_at DESC, r.id DESC
                    LIMIT 1
                ) AS up_evt ON TRUE
                LEFT JOIN LATERAL (
                    SELECT r.received_at, r.source_timestamp
                    FROM raw_market_events AS r
                    WHERE r.source = 'polymarket'
                      AND r.stream = 'market'
                      AND r.instrument = t.condition_id
                      AND r.event_type = 'price_change'
                      AND r.received_at <= t.feature_at
                      AND r.source_timestamp IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              COALESCE(r.payload->'price_changes', '[]'::jsonb)
                          ) AS change
                          WHERE change->>'asset_id' = t.down_token_id
                      )
                    ORDER BY r.received_at DESC, r.id DESC
                    LIMIT 1
                ) AS down_evt ON TRUE
                ORDER BY t.feature_at, t.condition_id
                """
            ),
            {
                "feature_version": FEATURE_VERSION,
                "selected_offset": SELECTED_OFFSET_SECONDS,
                "epoch_start": EPOCH_START,
                "HOLDOUT_START": HOLDOUT_START,
            },
        ).mappings().all()

        side_age_2 = {"up": [], "down": []}
        side_transport = {"up": [], "down": []}
        unavailable = {"up": 0, "down": 0}
        eligible_2 = {"up": 0, "down": 0}
        eligible_10 = {"up": 0, "down": 0}
        both_2 = 0
        both_10 = 0
        for row in execution_rows:
            feature_at = utc(row["feature_at"])
            row_eligible_2: dict[str, bool] = {}
            row_eligible_10: dict[str, bool] = {}
            for side in ("up", "down"):
                source_at = row[f"{side}_source_timestamp"]
                received_at = row[f"{side}_received_at"]
                if source_at is None or received_at is None:
                    unavailable[side] += 1
                    row_eligible_2[side] = False
                    row_eligible_10[side] = False
                    continue
                source_at = utc(source_at)
                received_at = utc(received_at)
                source_age = dec((feature_at - source_at).total_seconds())
                transport_lag = dec((received_at - source_at).total_seconds())
                side_age_2[side].append(source_age)
                side_transport[side].append(transport_lag)
                ok_future = source_age >= -MAX_FUTURE_SKEW_SECONDS
                ok2 = ok_future and source_age <= SOURCE_TIME_LIMIT_SECONDS
                ok10 = ok_future and source_age <= LEGACY_FRESHNESS_SECONDS
                eligible_2[side] += int(ok2)
                eligible_10[side] += int(ok10)
                row_eligible_2[side] = ok2
                row_eligible_10[side] = ok10
            both_2 += int(row_eligible_2.get("up", False) and row_eligible_2.get("down", False))
            both_10 += int(row_eligible_10.get("up", False) and row_eligible_10.get("down", False))
finally:
    engine.dispose()

result = {
    "generated_at": datetime.now(UTC).isoformat(),
    "audit": "v4_source_time_integrity_v1",
    "scope": {
        "research_plan_version": RESEARCH_PLAN_VERSION,
        "feature_version": FEATURE_VERSION,
        "epoch_start": EPOCH_START.isoformat(),
        "HOLDOUT_START_exclusive": HOLDOUT_START.isoformat(),
        "selected_offset_seconds": SELECTED_OFFSET_SECONDS,
        "final_holdout_labels_read": False,
    },
    "selected_feature_integrity": {
        "row_count": len(feature_rows),
        "future_cutoff_violation_count": future_cutoff_violations,
        "polymarket_predictor_key_count": polymarket_predictor_key_count,
    },
    "btc_predictor_feed_source_time_health": feed_health,
    "polymarket_execution_source_time_health": {
        "market_count": len(execution_rows),
        "source_age_seconds_by_side": {
            "up": summarize(side_age_2["up"]),
            "down": summarize(side_age_2["down"]),
        },
        "transport_lag_seconds_by_side": {
            "up": summarize(side_transport["up"]),
            "down": summarize(side_transport["down"]),
        },
        "source_time_unavailable_by_side": unavailable,
        "eligible_within_2s_by_side": eligible_2,
        "eligible_within_10s_by_side": eligible_10,
        "both_sides_eligible_within_2s": both_2,
        "both_sides_eligible_within_10s": both_10,
    },
    "interpretation": {
        "two_second_rule_matches_v3_source_time_guard": True,
        "ten_second_rule_matches_legacy_v4_compact_state_freshness": True,
        "old_v4_edge_policy_reused_v3_execution_books": True,
        "model_refit_performed": False,
        "threshold_tuning_performed": False,
        "final_holdout_evaluated": False,
    },
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

printf 'PHASE14_V4_SOURCE_TIME_AUDIT=PASS\n'
printf 'MUTATIONS_PERFORMED=false\n'
printf 'FINAL_HOLDOUT_LABELS_READ=false\n'
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
