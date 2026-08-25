from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from bp_engine.labels.models import MarketLabel
from bp_engine.labels.repository import LabelConflict, MarketLabelRepository
from bp_engine.storage.schema import market_labels, metadata


def _label(**overrides: object) -> MarketLabel:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "condition_id": "condition-1",
        "gamma_market_id": "market-1",
        "slug": "btc-updown-5m-1787659200",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(minutes=5),
        "official_outcome": "Up",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "rules_hash": "sha256:rules",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": "sha256:snapshot-a",
        "source_observed_at": start + timedelta(minutes=6),
        "generated_at": start + timedelta(minutes=7),
    }
    values.update(overrides)
    return MarketLabel(**values)  # type: ignore[arg-type]


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def test_exact_rerun_is_existing_and_preserves_first_generated_at() -> None:
    engine = _engine()
    repository = MarketLabelRepository()
    first_label = _label()
    later_generation = _label(
        generated_at=first_label.generated_at + timedelta(hours=1),
    )

    with engine.begin() as connection:
        first = repository.store(connection, first_label)
        before = connection.execute(select(market_labels)).mappings().one()
        duplicate = repository.store(connection, later_generation)
        after = connection.execute(select(market_labels)).mappings().one()

    assert first.created is True
    assert duplicate.created is False
    assert before["generated_at"] == after["generated_at"]
    assert after["official_outcome"] == "Up"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("official_outcome", "Down"),
        ("rules_hash", "sha256:changed-rules"),
        ("horizon_seconds", 900),
        ("source_snapshot_sha256", "sha256:snapshot-b"),
        ("source_observed_at", datetime(2026, 8, 25, 12, 8, tzinfo=UTC)),
    ],
)
def test_changed_semantic_label_at_same_natural_key_fails_closed(
    field: str,
    value: object,
) -> None:
    engine = _engine()
    repository = MarketLabelRepository()
    original = _label()
    changed = _label(**{field: value})

    with engine.begin() as connection:
        repository.store(connection, original)
        with pytest.raises(LabelConflict, match="condition-1"):
            repository.store(connection, changed)


def test_repository_rejects_pre_end_source_timestamp() -> None:
    engine = _engine()
    repository = MarketLabelRepository()
    label = _label(
        source_observed_at=datetime(2026, 8, 25, 12, 4, 59, tzinfo=UTC),
    )

    with engine.begin() as connection:
        with pytest.raises(ValueError, match="source_observed_at"):
            repository.store(connection, label)
