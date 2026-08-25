from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.storage.schema import (
    btc_candles,
    market_features,
    market_labels,
    market_state_1s,
    polymarket_price_history,
    raw_market_events,
)

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _cli():
    from bp_engine.features import cli

    return cli


def _apply_migration(database_url: str, path: str) -> None:
    engine = create_engine(database_url)
    script = Path(path).read_text(encoding="utf-8")
    with engine.begin() as connection:
        for statement in script.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)


def _label_values(start: datetime, condition_id: str) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "gamma_market_id": "phase6-postgres-market",
        "slug": f"btc-updown-5m-{int(start.timestamp())}",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(minutes=5),
        "official_outcome": "Down",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "rules_hash": "sha256:phase6-postgres-rules",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": "sha256:phase6-postgres-snapshot",
        "source_observed_at": start + timedelta(minutes=6),
        "generated_at": start + timedelta(minutes=7),
    }


def _price_rows(start: datetime, condition_id: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for outcome, asset_id, base_price in (
        ("Up", "phase6-postgres-up-token", 0.51),
        ("Down", "phase6-postgres-down-token", 0.48),
    ):
        for index, seconds in enumerate((30, 90, 150, 210)):
            rows.append(
                {
                    "source": "polymarket_clob",
                    "condition_id": condition_id,
                    "asset_id": asset_id,
                    "outcome": outcome,
                    "observed_at": start + timedelta(seconds=seconds),
                    "price": str(base_price + index * 0.01),
                    "fidelity_minutes": 1,
                }
            )
    return rows


def _candle_rows(start: datetime) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    first_bucket = start - timedelta(minutes=16)
    for index in range(20):
        bucket = first_bucket + timedelta(minutes=index)
        price = 64000 + index * 10
        rows.append(
            {
                "source": "coinbase",
                "market_type": "spot",
                "symbol": "BTC-USD",
                "interval_seconds": 60,
                "bucket_at": bucket,
                "open": str(price - 5),
                "high": str(price + 10),
                "low": str(price - 10),
                "close": str(price),
                "volume": "1.25",
                "turnover": None,
                "raw_payload": {"fixture": "phase6-postgres", "index": index},
            }
        )
    return rows


def _state_rows(start: datetime, condition_id: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for minute in range(1, 5):
        feature_at = start + timedelta(minutes=minute)
        observed = feature_at - timedelta(seconds=2)
        rows.extend(
            [
                {
                    "bucket_at": observed,
                    "state_key": f"phase6:pm:up:{minute}",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": condition_id,
                    "market_id": condition_id,
                    "asset_id": "phase6-postgres-up-token",
                    "last_event_at": observed,
                    "state": {
                        "best_bid": "0.50",
                        "best_ask": "0.52",
                        "bid_depth": "100",
                        "ask_depth": "80",
                    },
                },
                {
                    "bucket_at": observed,
                    "state_key": f"phase6:pm:down:{minute}",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": condition_id,
                    "market_id": condition_id,
                    "asset_id": "phase6-postgres-down-token",
                    "last_event_at": observed,
                    "state": {
                        "best_bid": "0.47",
                        "best_ask": "0.49",
                        "bid_depth": "70",
                        "ask_depth": "90",
                    },
                },
                {
                    "bucket_at": observed,
                    "state_key": f"phase6:bybit:spot:{minute}",
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "market_id": None,
                    "asset_id": None,
                    "last_event_at": observed,
                    "state": {
                        "last_price": "64050",
                        "best_bid": "64049",
                        "best_ask": "64051",
                    },
                },
                {
                    "bucket_at": observed,
                    "state_key": f"phase6:bybit:linear:{minute}",
                    "source": "bybit",
                    "stream": "linear",
                    "instrument": "BTCUSDT",
                    "market_id": None,
                    "asset_id": None,
                    "last_event_at": observed,
                    "state": {
                        "last_price": "64055",
                        "mark_price": "64054",
                        "index_price": "64052",
                        "funding_rate": "0.0001",
                        "open_interest": "12500",
                    },
                },
            ]
        )
    return rows


def _raw_rows(start: datetime, condition_id: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    feeds = (
        ("polymarket", "market", condition_id),
        ("coinbase", "spot", "BTC-USD"),
        ("bybit", "spot", "BTCUSDT"),
        ("bybit", "linear", "BTCUSDT"),
    )
    for minute in range(1, 5):
        received = start + timedelta(minutes=minute, seconds=-1)
        for source, stream, instrument in feeds:
            rows.append(
                {
                    "source": source,
                    "stream": stream,
                    "instrument": instrument,
                    "event_type": "ticker",
                    "source_timestamp": received,
                    "received_at": received,
                    "sequence": None,
                    "market_id": condition_id if source == "polymarket" else None,
                    "asset_id": None,
                    "payload": {"fixture": "phase6-postgres", "source": source},
                    "dedupe_key": (
                        f"phase6-postgres:{minute}:{source}:{stream}:{instrument}"
                    ),
                }
            )
    return rows


def test_postgres_feature_generation_is_idempotent_and_leakage_safe() -> None:
    assert DATABASE_URL is not None
    for migration in (
        "migrations/0002_raw_recorder.sql",
        "migrations/0003_retention_and_state.sql",
        "migrations/0004_historical_backfill.sql",
        "migrations/0005_market_labels.sql",
        "migrations/0006_market_features.sql",
    ):
        _apply_migration(DATABASE_URL, migration)

    cli = _cli()
    engine = create_engine(DATABASE_URL)
    start = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    condition_id = "phase6-postgres-condition"

    with engine.begin() as connection:
        connection.execute(
            market_features.delete().where(market_features.c.condition_id == condition_id)
        )
        connection.execute(
            market_labels.delete().where(market_labels.c.condition_id == condition_id)
        )
        connection.execute(
            polymarket_price_history.delete().where(
                polymarket_price_history.c.condition_id == condition_id
            )
        )
        connection.execute(
            market_state_1s.delete().where(
                market_state_1s.c.state_key.like("phase6:%")
            )
        )
        connection.execute(
            raw_market_events.delete().where(
                raw_market_events.c.dedupe_key.like("phase6-postgres:%")
            )
        )
        connection.execute(
            btc_candles.delete().where(
                btc_candles.c.source == "coinbase",
                btc_candles.c.market_type == "spot",
                btc_candles.c.symbol == "BTC-USD",
                btc_candles.c.interval_seconds == 60,
                btc_candles.c.bucket_at >= start - timedelta(minutes=16),
                btc_candles.c.bucket_at < end,
            )
        )
        connection.execute(insert(market_labels), [_label_values(start, condition_id)])
        connection.execute(
            insert(polymarket_price_history), _price_rows(start, condition_id)
        )
        connection.execute(insert(btc_candles), _candle_rows(start))
        connection.execute(insert(market_state_1s), _state_rows(start, condition_id))
        connection.execute(insert(raw_market_events), _raw_rows(start, condition_id))

        targets = cli.load_targets(
            connection,
            start=start,
            end=end,
        )
        first = cli.generate_features(
            connection,
            targets,
            generated_at=start + timedelta(hours=1),
            step_seconds=60,
        )
        second = cli.generate_features(
            connection,
            targets,
            generated_at=start + timedelta(hours=2),
            step_seconds=60,
        )
        rows = connection.execute(
            select(market_features)
            .where(market_features.c.condition_id == condition_id)
            .order_by(market_features.c.feature_at)
        ).mappings().all()

    assert len(targets) == 1
    assert first.targets_considered == 1
    assert first.planned_rows == 4
    assert first.inserted == 4
    assert first.existing == 0
    assert second.targets_considered == 1
    assert second.planned_rows == 4
    assert second.inserted == 0
    assert second.existing == 4
    assert len(rows) == 4

    keys = {
        (row["condition_id"], row["feature_at"], row["feature_version"])
        for row in rows
    }
    assert len(keys) == 4
    forbidden = {
        "official_outcome",
        "resolved_outcome",
        "start_reference",
        "end_reference",
        "resolution_source",
        "label_source",
        "label_version",
    }
    for row in rows:
        assert row["feature_version"] == "core-v1"
        assert row["features"]["official_reference_distance"] is None
        assert row["missing_flags"]["official_reference_missing"] is True
        assert forbidden.isdisjoint(row["features"])
        assert forbidden.isdisjoint(row["missing_flags"])
        assert forbidden.isdisjoint(row["source_cutoffs"])
        for cutoff in row["source_cutoffs"].values():
            parsed = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
            assert parsed <= row["feature_at"]
