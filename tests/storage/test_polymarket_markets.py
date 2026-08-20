import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.polymarket_markets import PolymarketMarketRepository, RuleChangeDetected
from bp_engine.storage.schema import metadata, polymarket_markets

FIXTURES = Path(__file__).parents[1] / "fixtures" / "polymarket"


def load_market():
    payload = json.loads((FIXTURES / "btc_updown_15m_gamma.json").read_text())
    return parse_gamma_market(payload)


def make_repository():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, PolymarketMarketRepository()


def test_repository_inserts_once_and_idempotently_refreshes_same_market() -> None:
    engine, repository = make_repository()
    market = load_market()
    observed_at = datetime(2026, 8, 20, 4, 1, tzinfo=UTC)

    with engine.begin() as connection:
        first = repository.upsert(connection, market, observed_at)
        second = repository.upsert(connection, market, observed_at)
        count = connection.scalar(select(func.count()).select_from(polymarket_markets))

    assert first.created is True
    assert second.created is False
    assert second.status_changed is False
    assert count == 1


def test_repository_refreshes_lifecycle_status_without_replacing_rules() -> None:
    engine, repository = make_repository()
    market = load_market()
    resolved = market.model_copy(
        update={
            "active": False,
            "closed": True,
            "accepting_orders": False,
            "resolved_outcome": "Down",
        }
    )

    with engine.begin() as connection:
        repository.upsert(connection, market, datetime(2026, 8, 20, 4, 1, tzinfo=UTC))
        result = repository.upsert(connection, resolved, datetime(2026, 8, 20, 4, 16, tzinfo=UTC))
        row = connection.execute(
            select(polymarket_markets).where(
                polymarket_markets.c.condition_id == market.condition_id
            )
        ).mappings().one()

    assert result.status_changed is True
    assert row["active"] is False
    assert row["closed"] is True
    assert row["accepting_orders"] is False
    assert row["resolved_outcome"] == "Down"
    assert row["rules_hash"] == market.rules_hash


def test_repository_rejects_silent_rule_change_for_same_condition() -> None:
    engine, repository = make_repository()
    market = load_market()
    changed = market.model_copy(update={"rules_hash": "sha256:different"})

    with engine.begin() as connection:
        repository.upsert(connection, market, datetime(2026, 8, 20, 4, 1, tzinfo=UTC))
        with pytest.raises(RuleChangeDetected, match=market.condition_id):
            repository.upsert(connection, changed, datetime(2026, 8, 20, 4, 2, tzinfo=UTC))
