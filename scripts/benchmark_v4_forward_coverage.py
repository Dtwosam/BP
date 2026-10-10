#!/usr/bin/env python3
"""Opt-in, isolated PostgreSQL benchmark for V4 full vs forward-only coverage.

Run ONLY with BP_V4_COVERAGE_BENCHMARK=1 and a local test database URL.
Synthetic schema and all inserted rows are rolled back in one transaction.
No production connection, migration, or persisted data is permitted.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import MetaData, create_engine, insert, text
from sqlalchemy.engine import make_url

from bp_engine.features.v4_coverage import (
    build_v4_coverage_report,
    build_v4_forward_coverage_summary,
)
from bp_engine.features.v4_models import V4_FEATURE_VERSION
from bp_engine.storage.schema import market_features

ROW_COUNT = 20_000
REPEATS = 3
START = datetime(2026, 9, 20, 12, 45, tzinfo=UTC)
OFFSETS = (60, 120, 180, 240)
COMPARED_KEYS = (
    "row_count",
    "market_count",
    "regime",
    "future_cutoff_violation_count",
    "polymarket_predictor_key_count",
    "regime_invariant_violation_count",
    "policy_selected",
    "training_run",
    "automatic_promotion",
)


def fixture_row(index: int) -> dict[str, object]:
    market_idx, offset_idx = divmod(index, 4)
    market_start = START + timedelta(minutes=5 * market_idx)
    offset = OFFSETS[offset_idx]
    feature_at = market_start + timedelta(seconds=offset)
    features: dict[str, float] = {
        "regime_bull": 1.0 if market_idx % 3 == 0 else 0.0,
        "regime_bear": 1.0 if market_idx % 3 == 1 else 0.0,
        "regime_sideways_mixed": 1.0 if market_idx % 3 == 2 else 0.0,
    }
    flags: dict[str, bool] = {}
    cutoffs: dict[str, str] = {}
    for prefix in ("coinbase", "bybit_spot", "bybit_linear"):
        for suffix in (
            "return_from_market_start", "return_30s", "return_60s",
            "return_120s", "return_5m", "return_15m", "return_60m",
        ):
            features[f"{prefix}_{suffix}"] = 0.001
        flags[f"{prefix}_current_missing"] = False
        flags[f"{prefix}_current_stale"] = False
        cutoffs[f"{prefix}_current_state"] = feature_at.isoformat()
        for horizon in ("5m", "15m", "60m"):
            flags[f"{prefix}_regime_trailing_{horizon}_missing"] = False
            flags[f"{prefix}_regime_trailing_{horizon}_stale"] = False
            cutoffs[f"{prefix}_regime_trailing_{horizon}_state"] = (
                feature_at - timedelta(minutes=5)
            ).isoformat()

    condition_id = f"synthetic-{market_idx:06d}"
    return {
        "condition_id": condition_id,
        "slug": f"btc-updown-300-{market_idx}",
        "horizon_seconds": 300,
        "market_start_at": market_start,
        "market_end_at": market_start + timedelta(seconds=300),
        "feature_at": feature_at,
        "feature_offset_seconds": offset,
        "feature_version": V4_FEATURE_VERSION,
        "features": features,
        "missing_flags": flags,
        "source_cutoffs": cutoffs,
        "input_fingerprint": "a" * 64,
        "feature_hash": "b" * 64,
        "generated_at": market_start + timedelta(seconds=330),
    }


def main() -> None:
    if os.environ.get("BP_V4_COVERAGE_BENCHMARK") != "1":
        raise SystemExit("benchmark requires BP_V4_COVERAGE_BENCHMARK=1")
    url = os.environ.get("BP_TEST_DATABASE_URL")
    if not url:
        raise SystemExit("benchmark requires BP_TEST_DATABASE_URL")
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in ("localhost", "127.0.0.1")
        or parsed.database != "bp"
    ):
        raise SystemExit("benchmark requires local PostgreSQL test database bp")

    engine = create_engine(url)
    schema_name = f"v4_coverage_bench_{uuid4().hex[:12]}"
    results: dict[str, list[float]] = {"full": [], "slim": []}
    try:
        with engine.connect() as connection:
            tx = connection.begin()
            try:
                connection.execute(text(f"CREATE SCHEMA {schema_name}"))
                isolated_table = market_features.to_metadata(
                    MetaData(), schema=schema_name
                )
                isolated_table.create(connection)
                connection.execute(text(f"SET LOCAL search_path TO {schema_name}"))
                for first in range(0, ROW_COUNT, 500):
                    connection.execute(
                        insert(isolated_table),
                        [fixture_row(i) for i in range(first, min(first + 500, ROW_COUNT))],
                    )
                for rep in range(REPEATS):
                    outputs: dict[str, dict[str, object]] = {}
                    # Alternate ordering to reduce warm-cache bias.
                    methods = (
                        ("full", build_v4_coverage_report),
                        ("slim", build_v4_forward_coverage_summary),
                    )
                    if rep % 2:
                        methods = tuple(reversed(methods))
                    for label, method in methods:
                        started = time.monotonic()
                        outputs[label] = method(connection, epoch_start=START)
                        results[label].append(time.monotonic() - started)
                    for key in COMPARED_KEYS:
                        assert outputs["slim"][key] == outputs["full"][key], key
                    assert outputs["slim"]["row_count"] == ROW_COUNT
                    assert outputs["slim"]["future_cutoff_violation_count"] == 0
                    assert outputs["slim"]["polymarket_predictor_key_count"] == 0
                    assert outputs["slim"]["regime_invariant_violation_count"] == 0
            finally:
                # CREATE SCHEMA and all synthetic inserts are rolled back together.
                tx.rollback()
    finally:
        engine.dispose()

    full_median = statistics.median(results["full"])
    slim_median = statistics.median(results["slim"])
    print(
        "V4_COVERAGE_BENCHMARK="
        + json.dumps(
            {
                "dialect": "postgresql",
                "data": "synthetic, local, transactionally rolled back",
                "rows": ROW_COUNT,
                "repetitions": REPEATS,
                "full_seconds": [round(x, 3) for x in results["full"]],
                "slim_seconds": [round(x, 3) for x in results["slim"]],
                "full_median_seconds": round(full_median, 3),
                "slim_median_seconds": round(slim_median, 3),
                "observed_speedup_ratio": round(full_median / slim_median, 2),
                "all_invariant_counts_equal": True,
                "production_benchmark": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
