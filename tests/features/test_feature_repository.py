import importlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from bp_engine.storage import schema


def _modules():
    models = importlib.import_module("bp_engine.features.models")
    repository = importlib.import_module("bp_engine.features.repository")
    return models, repository


def _feature(**overrides: object):
    models, _ = _modules()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "condition_id": "condition-1",
        "slug": "btc-updown-5m-1787659200",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(minutes=5),
        "feature_at": start + timedelta(minutes=2),
        "feature_offset_seconds": 120,
        "feature_version": "core-v1",
        "features": {"seconds_remaining": 180, "pm_up_price": 0.62},
        "missing_flags": {"pm_up_price_missing": False},
        "source_cutoffs": {"polymarket_price": "2026-08-25T12:02:00Z"},
        "input_fingerprint": "a" * 64,
        "feature_hash": "b" * 64,
        "generated_at": start + timedelta(minutes=6),
    }
    values.update(overrides)
    return models.MarketFeature(**values)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def test_exact_rerun_is_existing_and_preserves_first_generated_at() -> None:
    _, repository_module = _modules()
    engine = _engine()
    repository = repository_module.MarketFeatureRepository()
    first_feature = _feature()
    duplicate_feature = _feature(
        generated_at=first_feature.generated_at + timedelta(hours=1)
    )

    with engine.begin() as connection:
        first = repository.store(connection, first_feature)
        before = connection.execute(select(schema.market_features)).mappings().one()
        duplicate = repository.store(connection, duplicate_feature)
        after = connection.execute(select(schema.market_features)).mappings().one()

    assert first.created is True
    assert duplicate.created is False
    assert before["generated_at"] == after["generated_at"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("features", {"seconds_remaining": 180, "pm_up_price": 0.61}),
        ("missing_flags", {"pm_up_price_missing": True}),
        ("source_cutoffs", {"polymarket_price": "2026-08-25T12:01:59Z"}),
        ("input_fingerprint", "c" * 64),
        ("feature_hash", "d" * 64),
    ],
)
def test_changed_semantics_at_same_natural_key_fail_closed(
    field: str, value: object
) -> None:
    _, repository_module = _modules()
    engine = _engine()
    repository = repository_module.MarketFeatureRepository()

    with engine.begin() as connection:
        repository.store(connection, _feature())
        with pytest.raises(repository_module.FeatureConflict, match="condition-1"):
            repository.store(connection, _feature(**{field: value}))


def test_repository_rejects_feature_time_outside_market_window() -> None:
    _, repository_module = _modules()
    engine = _engine()
    repository = repository_module.MarketFeatureRepository()
    feature = _feature(feature_at=datetime(2026, 8, 25, 12, 5, tzinfo=UTC))

    with engine.begin() as connection:
        with pytest.raises(ValueError, match="feature_at"):
            repository.store(connection, feature)
