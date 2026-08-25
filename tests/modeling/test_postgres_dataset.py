from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.modeling.dataset import load_dataset
from bp_engine.storage.schema import market_features, market_labels, metadata

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def test_postgres_dataset_snapshot_is_deterministic() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    condition_id = "phase7-postgres-dataset"
    start = datetime(2026, 8, 23, 23, 45, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    label = {
        "condition_id": condition_id,
        "gamma_market_id": "phase7-postgres-dataset-gamma",
        "slug": "btc-updown-5m-phase7-postgres-dataset",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": end,
        "official_outcome": "Down",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "chainlink",
        "rules_hash": "r" * 64,
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": "s" * 64,
        "source_observed_at": end + timedelta(seconds=5),
        "generated_at": end + timedelta(seconds=10),
    }
    features = []
    for minute in (1, 2):
        feature_at = start + timedelta(minutes=minute)
        features.append(
            {
                "condition_id": condition_id,
                "slug": label["slug"],
                "horizon_seconds": 300,
                "market_start_at": start,
                "market_end_at": end,
                "feature_at": feature_at,
                "feature_offset_seconds": minute * 60,
                "feature_version": "core-v1",
                "features": {
                    "official_reference_distance": None,
                    "pm_up_price": 0.45 + minute * 0.01,
                },
                "missing_flags": {"official_reference_missing": True},
                "source_cutoffs": {"polymarket_up_price": feature_at.isoformat()},
                "input_fingerprint": str(minute) * 64,
                "feature_hash": str(minute + 2) * 64,
                "generated_at": end + timedelta(minutes=1),
            }
        )

    with engine.begin() as connection:
        connection.execute(
            delete(market_features).where(market_features.c.condition_id == condition_id)
        )
        connection.execute(
            delete(market_labels).where(market_labels.c.condition_id == condition_id)
        )
        connection.execute(insert(market_labels).values(**label))
        connection.execute(insert(market_features), features)
        first = load_dataset(
            connection,
            start=start,
            end=start + timedelta(hours=1),
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        second = load_dataset(
            connection,
            start=start,
            end=start + timedelta(hours=1),
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        connection.execute(
            delete(market_features).where(market_features.c.condition_id == condition_id)
        )
        connection.execute(
            delete(market_labels).where(market_labels.c.condition_id == condition_id)
        )

    assert first.dataset_sha256 == second.dataset_sha256
    assert [row.feature_at for row in first.rows] == sorted(row.feature_at for row in first.rows)
    assert {row.target for row in first.rows} == {0}
