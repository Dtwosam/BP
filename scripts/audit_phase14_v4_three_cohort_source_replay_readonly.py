#!/usr/bin/env python3
"""Read-only V4 source replay for three preregistered time-stratified features.

No trading, feature persistence, holdout access, recorder changes or retries.
An exact match is limited to the retained compact-state source view at audit time.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.config import Settings, TradingMode
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v4_models import V4_FEATURE_VERSION, V4FeatureTarget
from bp_engine.features.v4_service import build_v4_feature
from bp_engine.storage.schema import market_features

EPOCH = datetime(2026, 9, 20, 12, 40, 53, tzinfo=UTC)
COHORTS = (
    ("early", datetime(2026, 9, 22, 12, tzinfo=UTC)),
    ("middle", datetime(2026, 10, 1, 12, tzinfo=UTC)),
    ("recent", datetime(2026, 10, 10, 12, tzinfo=UTC)),
)
WINDOW = timedelta(hours=1)
MAX_REPLAYS = 3
STATEMENT_TIMEOUT_MS = 2000
LOCK_TIMEOUT_MS = 500
MAX_WALL_SECONDS = 45.0
BETWEEN_REPLAYS_SECONDS = 0.25


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("stored timestamp must have a timezone")
    return value.astimezone(UTC)


def select_cohort_rows(connection) -> list[tuple[str, dict[str, Any] | None]]:
    """Exactly one lowest (feature_at, id) V4 feature in each fixed time window."""
    selected = []
    for name, beginning in COHORTS:
        end = beginning + WINDOW
        statement = (
            select(market_features)
            .where(
                market_features.c.feature_at >= beginning,
                market_features.c.feature_at < end,
                market_features.c.market_start_at >= EPOCH,
                market_features.c.feature_version == V4_FEATURE_VERSION,
            )
            .order_by(market_features.c.feature_at, market_features.c.id)
            .limit(1)
        )
        row = connection.execute(statement).mappings().first()
        selected.append((name, dict(row) if row is not None else None))
    return selected


def classify_replay(stored: dict[str, Any], rebuilt: Any) -> tuple[str, tuple[str, ...]]:
    original_hash = canonical_hash({
        "features": stored["features"], "missing_flags": stored["missing_flags"],
    })
    if original_hash != stored["feature_hash"]:
        return "FAIL_STORED_FEATURE_HASH", ("stored_feature_hash",)
    changed = tuple(
        key for key in (
            "input_fingerprint", "features", "missing_flags",
            "source_cutoffs", "feature_hash",
        )
        if stored[key] != getattr(rebuilt, key)
    )
    if not changed:
        return "PASS_EXACT_SOURCE_REPLAY", ()
    if "source_cutoffs" in changed or "missing_flags" in changed:
        return "INCONCLUSIVE_SOURCE_VIEW_CHANGED", changed
    if "input_fingerprint" in changed:
        return "INCONCLUSIVE_SOURCE_DESCRIPTOR_CHANGED", changed
    return "FAIL_REPLAY_INCONSISTENCY", changed


def replay_selected(connection, stored: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    target = V4FeatureTarget(
        condition_id=str(stored["condition_id"]),
        slug=str(stored["slug"]),
        horizon_seconds=int(stored["horizon_seconds"]),
        market_start_at=_utc(stored["market_start_at"]),
        market_end_at=_utc(stored["market_end_at"]),
    )
    # Default repository=None: this calls no MarketFeatureRepository.store.
    rebuilt = build_v4_feature(
        connection,
        target,
        _utc(stored["feature_at"]),
        generated_at=_utc(stored["generated_at"]),
    )
    return classify_replay(stored, rebuilt)


def audit(connection, deadline: float) -> str:
    if len(COHORTS) != MAX_REPLAYS:
        raise RuntimeError("preregistered cohort count has drifted")
    print("PREREGISTERED_REPLAY_COUNT=3", flush=True)
    for name, beginning in COHORTS:
        print(f"COHORT_WINDOW_{name.upper()}={beginning.isoformat()}/{(beginning+WINDOW).isoformat()}",
              flush=True)
    if time.monotonic() >= deadline:
        return "INCOMPLETE_WALL_BUDGET"

    selected = select_cohort_rows(connection)
    if time.monotonic() >= deadline:
        return "INCOMPLETE_WALL_BUDGET"

    if any(row is None for _, row in selected):
        for name, row in selected:
            print(f"COHORT_{name.upper()}_SELECTION={'FOUND' if row else 'MISSING'}",
                  flush=True)
        return "INCONCLUSIVE_MISSING_COHORT"

    ids = [int(row["id"]) for _, row in selected if row is not None]
    if len(ids) != MAX_REPLAYS or len(set(ids)) != MAX_REPLAYS:
        return "INCONCLUSIVE_DUPLICATE_SELECTION"

    for name, row in selected:
        assert row is not None
        print(f"COHORT_{name.upper()}_FEATURE_ID={row['id']}", flush=True)
        print(f"COHORT_{name.upper()}_FEATURE_AT={_utc(row['feature_at']).isoformat()}",
              flush=True)

    for position, (name, stored) in enumerate(selected):
        if time.monotonic() >= deadline:
            return "INCOMPLETE_WALL_BUDGET"
        assert stored is not None
        verdict, changed = replay_selected(connection, stored)
        print(f"COHORT_{name.upper()}_REPLAY={verdict}", flush=True)
        print(f"COHORT_{name.upper()}_CHANGED_FIELDS={','.join(changed)}", flush=True)
        if verdict != "PASS_EXACT_SOURCE_REPLAY":
            return "STOPPED_ON_FIRST_DISCREPANCY"
        if time.monotonic() >= deadline:
            return "INCOMPLETE_WALL_BUDGET"
        if position < MAX_REPLAYS - 1:
            time.sleep(BETWEEN_REPLAYS_SECONDS)
    return "PASS_THREE_PREREGISTERED_EXACT_REPLAYS"


def main() -> int:
    settings = Settings(_env_file="/etc/bp/bp.env")
    if (
        settings.mode is not TradingMode.RESEARCH
        or settings.live_trading_enabled
        or settings.max_trade_size_usd != 0
        or settings.max_daily_loss_usd != 0
        or settings.max_total_exposure_usd != 0
    ):
        print("V4_THREE_COHORT_SOURCE_REPLAY=BLOCKED_UNSAFE_SETTINGS")
        return 2

    deadline = time.monotonic() + MAX_WALL_SECONDS
    engine = create_engine(settings.database_url, connect_args={
        "connect_timeout": 3,
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={LOCK_TIMEOUT_MS} "
            "-c application_name=bp_v4_three_cohort_source_replay"
        ),
    })
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            assert conn.exec_driver_sql(
                "SHOW default_transaction_read_only"
            ).scalar_one() == "on"
            print("DATABASE_READ_ONLY=PASS", flush=True)
            verdict = audit(conn, deadline)
        print(f"V4_THREE_COHORT_SOURCE_REPLAY={verdict}")
        print("PRODUCTION_MUTATION=false")
        return 0 if verdict == "PASS_THREE_PREREGISTERED_EXACT_REPLAYS" else 1
    except (SQLAlchemyError, ValueError, RuntimeError, AssertionError) as exc:
        print(f"ERROR_TYPE={type(exc).__name__}")
        print("V4_THREE_COHORT_SOURCE_REPLAY=INCONCLUSIVE_QUERY_OR_REPLAY_ERROR")
        print("PRODUCTION_MUTATION=false")
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
