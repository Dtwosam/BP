import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, insert, select

from bp_engine.labels.models import MarketLabel
from bp_engine.labels.repository import LabelConflict, MarketLabelRepository
from bp_engine.labels.service import generate_labels
from bp_engine.storage.schema import market_labels, polymarket_market_snapshots

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _apply_migration(database_url: str, path: str) -> None:
    engine = create_engine(database_url)
    script = Path(path).read_text(encoding="utf-8")
    with engine.begin() as connection:
        for statement in script.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)


def _payload(
    start: datetime,
    *,
    condition_id: str,
    gamma_market_id: str,
    outcome: str | None,
    closed: bool,
) -> dict[str, object]:
    if outcome == "Up":
        outcome_prices = ["1", "0"]
    elif outcome == "Down":
        outcome_prices = ["0", "1"]
    else:
        outcome_prices = ["0.5", "0.5"]

    return {
        "id": gamma_market_id,
        "conditionId": condition_id,
        "slug": f"btc-updown-5m-{int(start.timestamp())}",
        "question": "Bitcoin Up or Down",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": "Official BTC resolution rule",
        "outcomes": json.dumps(["Up", "Down"]),
        "outcomePrices": json.dumps(outcome_prices),
        "clobTokenIds": json.dumps(["up-token", "down-token"]),
        "active": not closed,
        "closed": closed,
        "acceptingOrders": not closed,
        "events": [{"id": "phase5-postgres-event"}],
    }


def _snapshot_values(
    *,
    start: datetime,
    downloaded_at: datetime,
    condition_id: str,
    gamma_market_id: str,
    sha: str,
    outcome: str | None,
    closed: bool,
) -> dict[str, object]:
    payload = _payload(
        start,
        condition_id=condition_id,
        gamma_market_id=gamma_market_id,
        outcome=outcome,
        closed=closed,
    )
    return {
        "condition_id": condition_id,
        "gamma_market_id": gamma_market_id,
        "slug": str(payload["slug"]),
        "downloaded_at": downloaded_at,
        "payload_sha256": sha,
        "payload": payload,
    }


def test_postgres_label_generation_is_idempotent_and_preserves_provenance() -> None:
    assert DATABASE_URL is not None
    _apply_migration(DATABASE_URL, "migrations/0004_historical_backfill.sql")
    _apply_migration(DATABASE_URL, "migrations/0005_market_labels.sql")
    engine = create_engine(DATABASE_URL)

    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    resolved_condition = "phase5-postgres-resolved"
    unresolved_condition = "phase5-postgres-unresolved"
    resolved_sha = "sha256:phase5-postgres-resolved"
    observed_at = start + timedelta(minutes=6)

    with engine.begin() as connection:
        connection.execute(
            market_labels.delete().where(
                market_labels.c.condition_id.in_([resolved_condition, unresolved_condition])
            )
        )
        connection.execute(
            polymarket_market_snapshots.delete().where(
                polymarket_market_snapshots.c.condition_id.in_(
                    [resolved_condition, unresolved_condition]
                )
            )
        )
        connection.execute(
            insert(polymarket_market_snapshots),
            [
                _snapshot_values(
                    start=start,
                    downloaded_at=observed_at,
                    condition_id=resolved_condition,
                    gamma_market_id="phase5-postgres-market-resolved",
                    sha=resolved_sha,
                    outcome="Up",
                    closed=True,
                ),
                _snapshot_values(
                    start=start + timedelta(minutes=5),
                    downloaded_at=start + timedelta(minutes=11),
                    condition_id=unresolved_condition,
                    gamma_market_id="phase5-postgres-market-unresolved",
                    sha="sha256:phase5-postgres-unresolved",
                    outcome=None,
                    closed=True,
                ),
            ],
        )
        first = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=10),
            generated_at=start + timedelta(minutes=20),
        )

    with engine.begin() as connection:
        second = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=10),
            generated_at=start + timedelta(minutes=30),
        )
        stored = connection.execute(
            select(market_labels).where(
                market_labels.c.condition_id == resolved_condition
            )
        ).mappings().one()
        unresolved_count = connection.scalar(
            select(func.count()).select_from(market_labels).where(
                market_labels.c.condition_id == unresolved_condition
            )
        )

    assert first.conditions_considered == 2
    assert first.inserted == 1
    assert first.existing == 0
    assert first.skipped == 1
    assert second.conditions_considered == 2
    assert second.inserted == 0
    assert second.existing == 1
    assert second.skipped == 1
    assert unresolved_count == 0
    assert stored["official_outcome"] == "Up"
    assert stored["source_snapshot_sha256"] == resolved_sha
    assert stored["source_observed_at"] == observed_at
    assert stored["resolution_source"] == (
        "https://data.chain.link/streams/btc-usd-twap-60s-streams"
    )
    assert stored["rules_hash"]
    assert stored["label_source"] == "polymarket_gamma_snapshot"
    assert stored["label_version"] == "official-outcome-v1"
    assert stored["start_reference"] is None
    assert stored["end_reference"] is None

    changed = MarketLabel(
        condition_id=stored["condition_id"],
        gamma_market_id=stored["gamma_market_id"],
        slug=stored["slug"],
        horizon_seconds=stored["horizon_seconds"],
        market_start_at=stored["market_start_at"],
        market_end_at=stored["market_end_at"],
        official_outcome="Down",
        start_reference=stored["start_reference"],
        end_reference=stored["end_reference"],
        resolution_source=stored["resolution_source"],
        rules_hash=stored["rules_hash"],
        label_source=stored["label_source"],
        label_version=stored["label_version"],
        source_snapshot_sha256=stored["source_snapshot_sha256"],
        source_observed_at=stored["source_observed_at"],
        generated_at=start + timedelta(minutes=40),
    )
    with engine.begin() as connection:
        with pytest.raises(LabelConflict, match=resolved_condition):
            MarketLabelRepository().store(connection, changed)
