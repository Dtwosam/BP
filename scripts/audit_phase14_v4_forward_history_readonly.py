#!/usr/bin/env python3
"""Bounded, read-only full-history V4 feature and eligible-market audit.

Run only after review: from a clean Mac checkout, pipe this file to the
production host's pinned /opt/bp/.venv/bin/python as user bp. Never run
the forward writer for verification. No database migrations are required.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.config import Settings, TradingMode
from bp_engine.features.hashing import canonical_hash

EPOCH = datetime(2026, 9, 20, 12, 40, 53, tzinfo=UTC)
VERSION = "core-v4-regime-aware"
OFFSETS = (60, 120, 180, 240)
PAGE_SIZE = 64
DELAY_SECONDS = 0.15
MAX_RUNTIME_SECONDS = 900
HEX64 = re.compile(r"^[0-9a-f]{64}$")

FEATURE_PAGE = text("""
SELECT id, condition_id, slug, horizon_seconds,
       market_start_at, market_end_at, feature_at,
       feature_offset_seconds, features, missing_flags,
       source_cutoffs, input_fingerprint, feature_hash
FROM market_features
WHERE id > :cursor AND id <= :high
  AND feature_version = :version
ORDER BY id
LIMIT :page_size
""")
MARKET_PAGE = text("""
SELECT id, condition_id, horizon_seconds, start_at, end_at
FROM polymarket_markets
WHERE id > :cursor AND id <= :high
ORDER BY id
LIMIT :page_size
""")
MARKET_OFFSETS = text("""
SELECT condition_id, feature_offset_seconds
FROM market_features
WHERE condition_id IN :conditions AND feature_version = :version
""").bindparams(bindparam("conditions", expanding=True))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive timestamp in audited data")
    return value.astimezone(UTC)


def _cutoff(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("source cutoff is not a timestamp string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def inspect_feature(row: dict[str, Any]) -> list[str]:
    """Check one persisted feature row without invoking the generator."""
    problems = []
    try:
        start = _utc(row["market_start_at"])
        end = _utc(row["market_end_at"])
        at = _utc(row["feature_at"])
        offset = int(row["feature_offset_seconds"])
        if (
            row["horizon_seconds"] != 300
            or end - start != timedelta(seconds=300)
            or offset not in OFFSETS
            or at != start + timedelta(seconds=offset)
            or not start < at < end
        ):
            problems.append("time_geometry")

        features = row["features"]
        missing = row["missing_flags"]
        cutoffs = row["source_cutoffs"]
        if not all(isinstance(value, dict) for value in (features, missing, cutoffs)):
            problems.append("json_structure")
        else:
            if any(not isinstance(value, bool) for value in missing.values()):
                problems.append("missing_flag_type")
            if canonical_hash({"features": features, "missing_flags": missing}) != row["feature_hash"]:
                problems.append("feature_hash")
            if any(str(key).lower().startswith("pm_") or
                   "polymarket" in str(key).lower() for key in features):
                problems.append("polymarket_predictor")
            regime = [features.get("regime_" + key) for key in
                      ("bull", "bear", "sideways_mixed")]
            numeric = []
            for value in regime:
                try:
                    numeric.append(
                        None if value is None or isinstance(value, bool)
                        else float(value)
                    )
                except (ValueError, TypeError, OverflowError):
                    numeric.append(None)
            if all(v is not None and math.isfinite(v) for v in numeric):
                if sum(v == 1.0 for v in numeric) != 1:
                    problems.append("regime_invariant")
            for value in cutoffs.values():
                try:
                    if _cutoff(value) > at:
                        problems.append("future_source_cutoff")
                except ValueError:
                    problems.append("invalid_source_cutoff")
        if not isinstance(row["input_fingerprint"], str) or not HEX64.fullmatch(
            row["input_fingerprint"]
        ):
            problems.append("input_fingerprint_format")
        if not isinstance(row["feature_hash"], str) or not HEX64.fullmatch(
            row["feature_hash"]
        ):
            problems.append("feature_hash_format")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        problems.append("invalid_row:" + type(exc).__name__)
    return sorted(set(problems))


def inspect_market_offsets(
    market_rows: list[dict[str, Any]], offset_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: dict[str, list[int]] = {
        str(row["condition_id"]): [] for row in market_rows
    }
    for row in offset_rows:
        condition = str(row["condition_id"])
        if condition in seen:
            seen[condition].append(int(row["feature_offset_seconds"]))
    return [
        row for row in market_rows
        if tuple(sorted(seen[str(row["condition_id"])])) != OFFSETS
    ]


def _budget(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("900-second read-only audit budget exceeded")


def _scan_features(conn: Any, high: int, deadline: float) -> tuple[int, int, list[str]]:
    cursor = 0
    count = 0
    violations = 0
    examples: list[str] = []
    while True:
        _budget(deadline)
        page = conn.execute(FEATURE_PAGE, {
            "cursor": cursor, "high": high,
            "version": VERSION, "page_size": PAGE_SIZE,
        }).mappings().all()
        if not page:
            break
        for row in page:
            cursor = int(row["id"])
            if _utc(row["market_start_at"]) < EPOCH:
                continue
            count += 1
            issues = inspect_feature(dict(row))
            if issues:
                violations += 1
                if len(examples) < 5:
                    examples.append(f"id={cursor} categories={','.join(issues)}")
        if len(page) < PAGE_SIZE:
            break
        time.sleep(DELAY_SECONDS)
    return count, violations, examples


def _scan_markets(
    conn: Any, high: int, cutoff: datetime, deadline: float,
) -> tuple[int, int, int | None, list[str]]:
    cursor = 0
    eligible = 0
    pending = 0
    oldest: datetime | None = None
    examples: list[str] = []
    while True:
        _budget(deadline)
        page = conn.execute(MARKET_PAGE, {
            "cursor": cursor, "high": high, "page_size": PAGE_SIZE,
        }).mappings().all()
        if not page:
            break
        cursor = int(page[-1]["id"])
        targets = [
            dict(row) for row in page
            if int(row["horizon_seconds"]) == 300
            and _utc(row["start_at"]) >= EPOCH
            and _utc(row["end_at"]) <= cutoff
        ]
        eligible += len(targets)
        if targets:
            _budget(deadline)
            offsets = conn.execute(MARKET_OFFSETS, {
                "conditions": [str(row["condition_id"]) for row in targets],
                "version": VERSION,
            }).mappings().all()
            incomplete = inspect_market_offsets(targets, [dict(row) for row in offsets])
            pending += len(incomplete)
            for row in incomplete:
                end = _utc(row["end_at"])
                if oldest is None or end < oldest:
                    oldest = end
                if len(examples) < 5:
                    examples.append(str(row["condition_id"]))
        if len(page) < PAGE_SIZE:
            break
        time.sleep(DELAY_SECONDS)
    age = None if oldest is None else max(0, int((cutoff - oldest).total_seconds()))
    return eligible, pending, age, examples


def run(env_file: str) -> int:
    settings = Settings(_env_file=env_file)
    if (
        settings.mode is not TradingMode.RESEARCH
        or settings.live_trading_enabled
        or settings.max_trade_size_usd != 0
        or settings.max_daily_loss_usd != 0
        or settings.max_total_exposure_usd != 0
    ):
        print("V4_HISTORY_AUDIT=BLOCKED_UNSAFE_SETTINGS")
        return 2

    deadline = time.monotonic() + MAX_RUNTIME_SECONDS
    engine = create_engine(settings.database_url, connect_args={
        "connect_timeout": 3,
        "options": (
            "-c default_transaction_read_only=on "
            "-c statement_timeout=3000 -c lock_timeout=500 "
            "-c application_name=bp_v4_full_history_readonly"
        ),
    })
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            assert conn.exec_driver_sql(
                "SHOW default_transaction_read_only"
            ).scalar_one() == "on"
            print("DATABASE_READ_ONLY=PASS", flush=True)
            f_high = int(conn.execute(text(
                "SELECT COALESCE(max(id), 0) FROM market_features"
            )).scalar_one())
            m_high = int(conn.execute(text(
                "SELECT COALESCE(max(id), 0) FROM polymarket_markets"
            )).scalar_one())
            cutoff = datetime.now(UTC) - timedelta(seconds=15)
            print(f"FEATURE_ID_HIGH_WATER={f_high}", flush=True)
            print(f"MARKET_ID_HIGH_WATER={m_high}", flush=True)
            feature_count, violations, issues = _scan_features(
                conn, f_high, deadline
            )
            eligible, pending, age, pending_examples = _scan_markets(
                conn, m_high, cutoff, deadline
            )

        print(f"FORWARD_V4_FEATURE_ROWS_AUDITED={feature_count}")
        print(f"FEATURE_INTEGRITY_VIOLATIONS={violations}")
        print(f"ELIGIBLE_MARKETS_AUDITED={eligible}")
        print(f"PENDING_MARKETS={pending}")
        print(f"OLDEST_PENDING_AGE_SECONDS={age if age is not None else 'NONE'}")
        for issue in issues:
            print("FEATURE_VIOLATION_SAMPLE=" + issue)
        for condition in pending_examples:
            print("PENDING_CONDITION_SAMPLE=" + condition)
        print("INPUT_FINGERPRINT_RECOMPUTED=false")
        print("SINGLE_TRANSACTION_SNAPSHOT=false")
        if not feature_count or not eligible:
            verdict = "INCONCLUSIVE_NO_ROWS"
        elif violations:
            verdict = "FAIL_INTEGRITY"
        elif pending:
            verdict = "REVIEW_PENDING_BACKLOG"
        else:
            verdict = "PASS_BOUNDED_FULL_HISTORY"
        print("V4_HISTORY_AUDIT=" + verdict)
        print("PRODUCTION_MUTATION=false")
        return 0 if verdict == "PASS_BOUNDED_FULL_HISTORY" else 1
    except (SQLAlchemyError, TimeoutError, ValueError, AssertionError) as exc:
        print("V4_HISTORY_AUDIT=INCOMPLETE", flush=True)
        print("AUDIT_ERROR_TYPE=" + type(exc).__name__, flush=True)
        print("PRODUCTION_MUTATION=false", flush=True)
        return 2
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    return run(parser.parse_args().env_file)


if __name__ == "__main__":
    sys.exit(main())
