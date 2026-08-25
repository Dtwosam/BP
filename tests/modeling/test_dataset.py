from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.modeling.dataset import DatasetIntegrityError, load_dataset
from bp_engine.storage.schema import market_features, market_labels, metadata


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _label(start: datetime, *, horizon: int = 300, observed_offset: int = 360) -> dict[str, object]:
    return {
        "condition_id": "condition-1",
        "gamma_market_id": "gamma-1",
        "slug": "btc-updown-5m-1724457600",
        "horizon_seconds": horizon,
        "market_start_at": start,
        "market_end_at": start + timedelta(seconds=300),
        "official_outcome": "Up",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "chainlink",
        "rules_hash": "sha256:rules",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": "sha256:snapshot",
        "source_observed_at": start + timedelta(seconds=observed_offset),
        "generated_at": start + timedelta(seconds=420),
    }


def _feature(start: datetime, minute: int, *, horizon: int = 300, extra=None) -> dict[str, object]:
    feature_at = start + timedelta(minutes=minute)
    features: dict[str, object] = {
        "pm_up_price": 0.55 + minute * 0.01,
        "official_reference_distance": None,
    }
    if extra:
        features.update(extra)
    return {
        "condition_id": "condition-1",
        "slug": "btc-updown-5m-1724457600",
        "horizon_seconds": horizon,
        "market_start_at": start,
        "market_end_at": start + timedelta(seconds=300),
        "feature_at": feature_at,
        "feature_offset_seconds": minute * 60,
        "feature_version": "core-v1",
        "features": features,
        "missing_flags": {
            "official_reference_missing": True,
            "pm_up_book_missing": minute == 1,
        },
        "source_cutoffs": {"pm_up_price": feature_at.isoformat()},
        "input_fingerprint": f"{minute}" * 64,
        "feature_hash": f"{minute + 2}" * 64,
        "generated_at": start + timedelta(hours=1),
    }


def test_load_dataset_joins_frozen_features_to_target_without_metadata_predictors() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(insert(market_labels).values(**_label(start)))
        connection.execute(insert(market_features), [_feature(start, 1), _feature(start, 2)])
        first = load_dataset(
            connection,
            start=start,
            end=start + timedelta(days=1),
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        second = load_dataset(
            connection,
            start=start,
            end=start + timedelta(days=1),
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )

    assert len(first.rows) == 2
    assert first.dataset_sha256 == second.dataset_sha256
    assert {row.target for row in first.rows} == {1}
    assert first.predictor_names == (
        "missing__official_reference_missing",
        "missing__pm_up_book_missing",
        "official_reference_distance",
        "pm_up_price",
    )
    forbidden = {
        "condition_id",
        "feature_at",
        "feature_hash",
        "input_fingerprint",
        "official_outcome",
        "source_observed_at",
    }
    assert forbidden.isdisjoint(first.rows[0].predictors)


def test_load_dataset_rejects_static_market_metadata_mismatch() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(insert(market_labels).values(**_label(start)))
        connection.execute(insert(market_features).values(**_feature(start, 1, horizon=900)))
        with pytest.raises(DatasetIntegrityError, match="metadata"):
            load_dataset(
                connection,
                start=start,
                end=start + timedelta(days=1),
                horizon_seconds=900,
                feature_version="core-v1",
                label_version="official-outcome-v1",
            )


def test_load_dataset_rejects_label_observed_before_market_end() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(insert(market_labels).values(**_label(start, observed_offset=299)))
        connection.execute(insert(market_features).values(**_feature(start, 1)))
        with pytest.raises(DatasetIntegrityError, match="source_observed_at"):
            load_dataset(
                connection,
                start=start,
                end=start + timedelta(days=1),
                horizon_seconds=300,
                feature_version="core-v1",
                label_version="official-outcome-v1",
            )


def test_load_dataset_rejects_forbidden_label_key_inside_feature_payload() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(insert(market_labels).values(**_label(start)))
        connection.execute(
            insert(market_features).values(
                **_feature(start, 1, extra={"official_outcome": 1.0})
            )
        )
        with pytest.raises(DatasetIntegrityError, match="forbidden"):
            load_dataset(
                connection,
                start=start,
                end=start + timedelta(days=1),
                horizon_seconds=300,
                feature_version="core-v1",
                label_version="official-outcome-v1",
            )
