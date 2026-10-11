#!/usr/bin/env python3
"""Replay one persisted V4 feature fingerprint from retained compact BTC states.

Repository-only until reviewed. Uses read-only SQL and does not invoke a
feature writer, select policy, train, promote or perform a database mutation.
A historical source view may have changed: replay divergence is inconclusive
about the original observations, not evidence of deliberate tampering.
"""
from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.config import Settings, TradingMode
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v4_models import V4_FEATURE_VERSION, V4FeatureTarget
from bp_engine.features.v4_service import build_v4_feature
from bp_engine.storage.schema import market_features

EPOCH = datetime(2026, 9, 20, 12, 40, 53, tzinfo=UTC)
STATEMENT_TIMEOUT_MS = 2000
LOCK_TIMEOUT_MS = 500
FEATURE_LOOKUPS_MAX = 1


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def select_one_feature(connection, feature_id: int | None):
    query = select(market_features).where(
        market_features.c.feature_version == V4_FEATURE_VERSION,
        market_features.c.market_start_at >= EPOCH,
    )
    if feature_id is not None:
        query = query.where(market_features.c.id == feature_id)
    else:
        query = query.order_by(market_features.c.id.desc()).limit(FEATURE_LOOKUPS_MAX)
    row = connection.execute(query).mappings().first()
    return None if row is None else dict(row)


def classify_replay(stored: dict, rebuilt) -> tuple[str, tuple[str, ...]]:
    """Classify discrepancies without claiming source-view immutability."""
    stored_hash = canonical_hash({
        "features": stored["features"],
        "missing_flags": stored["missing_flags"],
    })
    if stored_hash != stored["feature_hash"]:
        return "FAIL_STORED_FEATURE_HASH", ("stored_feature_hash",)

    changes = tuple(
        name for name in (
            "input_fingerprint", "feature_hash",
            "features", "missing_flags", "source_cutoffs",
        )
        if stored[name] != getattr(rebuilt, name)
    )
    if not changes:
        return "PASS_EXACT_SOURCE_REPLAY", ()

    if "source_cutoffs" in changes or "missing_flags" in changes:
        return "INCONCLUSIVE_SOURCE_VIEW_CHANGED", changes
    if "input_fingerprint" in changes:
        return "INCONCLUSIVE_SOURCE_DESCRIPTOR_CHANGED", changes
    return "FAIL_REPLAY_INCONSISTENCY", changes


def replay_one(connection, feature_id: int | None) -> tuple[str, dict]:
    stored = select_one_feature(connection, feature_id)
    if stored is None:
        return "INCONCLUSIVE_FEATURE_NOT_FOUND", {}

    target = V4FeatureTarget(
        condition_id=str(stored["condition_id"]),
        slug=str(stored["slug"]),
        horizon_seconds=int(stored["horizon_seconds"]),
        market_start_at=_utc(stored["market_start_at"]),
        market_end_at=_utc(stored["market_end_at"]),
    )
    # build_v4_feature defaults repository=None; no MarketFeatureRepository.store
    # and no SQL INSERT/UPDATE/DELETE. Reads at the original feature timestamp.
    rebuilt = build_v4_feature(
        connection, target, _utc(stored["feature_at"]),
        generated_at=_utc(stored["generated_at"]),
    )
    verdict, differences = classify_replay(stored, rebuilt)
    return verdict, {
        "feature_id": int(stored["id"]),
        "feature_at": _utc(stored["feature_at"]).isoformat(),
        "changed_fields": differences,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--feature-id", type=int, default=None)
    args = parser.parse_args(argv)
    if args.feature_id is not None and args.feature_id < 1:
        parser.error("--feature-id must be positive")

    s = Settings(_env_file=args.env_file)
    if (
        s.mode is not TradingMode.RESEARCH
        or s.live_trading_enabled
        or s.max_trade_size_usd != 0
        or s.max_daily_loss_usd != 0
        or s.max_total_exposure_usd != 0
    ):
        print("V4_SOURCE_REPLAY=BLOCKED_UNSAFE_SETTINGS")
        return 2

    started = time.monotonic()
    engine = create_engine(s.database_url, connect_args={
        "connect_timeout": 3,
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={LOCK_TIMEOUT_MS} "
            "-c application_name=bp_v4_single_source_replay"
        ),
    })
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            assert connection.exec_driver_sql(
                "SHOW default_transaction_read_only"
            ).scalar_one() == "on"
            print("DATABASE_READ_ONLY=PASS", flush=True)
            verdict, info = replay_one(connection, args.feature_id)
        for key, value in info.items():
            print(f"{key.upper()}={','.join(value) if isinstance(value, tuple) else value}")
        print(f"REPLAY_ELAPSED_SECONDS={time.monotonic() - started:.2f}")
        print(f"V4_SOURCE_REPLAY={verdict}")
        print("PRODUCTION_MUTATION=false")
        return 0 if verdict == "PASS_EXACT_SOURCE_REPLAY" else 1
    except (SQLAlchemyError, ValueError, RuntimeError, AssertionError) as exc:
        print(f"REPLAY_ERROR_TYPE={type(exc).__name__}")
        print("V4_SOURCE_REPLAY=INCONCLUSIVE_QUERY_OR_REPLAY_ERROR")
        print("PRODUCTION_MUTATION=false")
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
