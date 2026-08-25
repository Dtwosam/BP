from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.features import cli
from bp_engine.features.models import FeatureTarget
from bp_engine.features.repository import FeatureConflict
from bp_engine.features.service import generate_features
from bp_engine.storage.schema import market_features, metadata, raw_market_events


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _target(horizon_seconds: int = 300) -> FeatureTarget:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    return FeatureTarget(
        condition_id=f"preserve-condition-{horizon_seconds}",
        slug=f"btc-updown-{horizon_seconds}s-preserve-test",
        horizon_seconds=horizon_seconds,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=horizon_seconds),
    )


def _retroactive_coinbase_coverage(received_at: datetime) -> dict[str, object]:
    return {
        "source": "coinbase",
        "stream": "spot",
        "instrument": "BTC-USD",
        "event_type": "ticker_update",
        "source_timestamp": received_at,
        "received_at": received_at,
        "sequence": None,
        "market_id": None,
        "asset_id": None,
        "payload": {"events": [{"tickers": [{"price": "64000"}]}]},
        "dedupe_key": f"retroactive-coverage-{received_at.isoformat()}",
    }


def test_preserve_existing_skips_retroactive_enrichment_without_rewriting() -> None:
    engine = _engine()
    five = _target(300)
    fifteen = _target(900)
    generated_at = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)

    with engine.begin() as connection:
        first = generate_features(connection, [five], generated_at=generated_at)
        before = connection.execute(
            select(
                market_features.c.condition_id,
                market_features.c.feature_at,
                market_features.c.input_fingerprint,
                market_features.c.feature_hash,
                market_features.c.generated_at,
            ).order_by(market_features.c.feature_at)
        ).all()
        connection.execute(
            insert(raw_market_events),
            [_retroactive_coinbase_coverage(five.market_start_at + timedelta(seconds=59))],
        )

        with pytest.raises(FeatureConflict, match="conflicting feature"):
            generate_features(
                connection,
                [five],
                generated_at=generated_at + timedelta(hours=1),
            )

        expanded = generate_features(
            connection,
            [five, fifteen],
            generated_at=generated_at + timedelta(hours=2),
            preserve_existing=True,
        )
        after = connection.execute(
            select(
                market_features.c.condition_id,
                market_features.c.feature_at,
                market_features.c.input_fingerprint,
                market_features.c.feature_hash,
                market_features.c.generated_at,
            )
            .where(market_features.c.condition_id == five.condition_id)
            .order_by(market_features.c.feature_at)
        ).all()

    assert first.inserted == 4
    assert expanded.planned_rows == 18
    assert expanded.existing == 4
    assert expanded.inserted == 14
    assert after == before


def test_feature_cli_exposes_preserve_existing_as_opt_in_only() -> None:
    base = [
        "--start",
        "2026-08-24T00:00:00Z",
        "--end",
        "2026-08-25T00:00:00Z",
    ]

    strict = cli.build_parser().parse_args(base)
    preserved = cli.build_parser().parse_args([*base, "--preserve-existing"])

    assert strict.preserve_existing is False
    assert preserved.preserve_existing is True


def test_phase7_host_gate_preserves_frozen_features_after_backfill() -> None:
    source = Path("scripts/deploy/phase7_host_acceptance.sh").read_text(encoding="utf-8")

    backfill_index = source.index("historical_backfill.py")
    feature_index = source.index("generate_features.py")
    assert backfill_index < feature_index
    assert "--preserve-existing" in source
    assert "FEATURE_ROWS_BEFORE" in source
    assert "PRESERVED_FEATURE_ROWS" in source
    assert "preserved feature count does not match pre-existing immutable rows" in source
