from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from bp_engine.labels.service import generate_labels
from bp_engine.storage.schema import market_labels, metadata, polymarket_market_snapshots


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _resolved_payload(start: datetime, condition_id: str) -> dict[str, object]:
    return {
        "id": f"gamma-{condition_id}",
        "conditionId": condition_id,
        "slug": f"btc-updown-5m-{int(start.timestamp())}",
        "question": "Bitcoin Up or Down",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": "Official BTC resolution rule",
        "outcomes": json.dumps(["Up", "Down"]),
        "outcomePrices": json.dumps(["1", "0"]),
        "clobTokenIds": json.dumps(["up-token", "down-token"]),
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "events": [{"id": "event-1"}],
    }


def test_generate_labels_condition_scope_filters_before_payload_parsing() -> None:
    engine = _engine()
    start = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    requested = _resolved_payload(start, "requested")

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots),
            [
                {
                    "condition_id": "requested",
                    "gamma_market_id": "gamma-requested",
                    "slug": requested["slug"],
                    "downloaded_at": start + timedelta(minutes=6),
                    "payload_sha256": "sha256:requested",
                    "payload": requested,
                },
                {
                    "condition_id": "holdout-unrequested",
                    "gamma_market_id": "gamma-holdout",
                    "slug": "btc-updown-5m-holdout",
                    "downloaded_at": start + timedelta(minutes=11),
                    "payload_sha256": "sha256:holdout",
                    # Deliberately not a valid Gamma payload. If the implementation
                    # parses this row, the scoped call has already crossed its boundary.
                    "payload": {"conditionId": "holdout-unrequested"},
                },
            ],
        )

        stats = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=5),
            generated_at=start + timedelta(minutes=20),
            condition_ids=("requested",),
        )
        labels = connection.execute(select(market_labels)).mappings().all()

    assert stats.conditions_considered == 1
    assert stats.inserted == 1
    assert [row["condition_id"] for row in labels] == ["requested"]
