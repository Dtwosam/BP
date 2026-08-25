from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from bp_engine.features.models import FeatureTarget
from bp_engine.storage.schema import market_features, metadata, raw_market_events


def _service():
    return importlib.import_module("bp_engine.features.service")


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _target(horizon_seconds: int = 300) -> FeatureTarget:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    return FeatureTarget(
        condition_id=f"condition-{horizon_seconds}",
        slug=f"btc-updown-{horizon_seconds}s-test",
        horizon_seconds=horizon_seconds,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=horizon_seconds),
    )


def _raw_coverage(received_at: datetime) -> dict[str, object]:
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
        "dedupe_key": f"coverage-{received_at.isoformat()}",
    }


def test_plan_feature_times_uses_completed_minutes_strictly_inside_market() -> None:
    service = _service()
    five = _target(300)
    fifteen = _target(900)

    assert [
        int((value - five.market_start_at).total_seconds())
        for value in service.plan_feature_times(five)
    ] == [60, 120, 180, 240]
    assert [
        int((value - fifteen.market_start_at).total_seconds())
        for value in service.plan_feature_times(fifteen)
    ] == list(range(60, 900, 60))


def test_generate_features_persists_immutable_rows_and_reruns_existing_only() -> None:
    service = _service()
    engine = _engine()
    target = _target()
    generated_at = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)

    with engine.begin() as connection:
        first = service.generate_features(
            connection,
            [target],
            generated_at=generated_at,
        )
        second = service.generate_features(
            connection,
            [target],
            generated_at=generated_at + timedelta(hours=1),
        )
        rows = connection.execute(
            select(market_features).order_by(market_features.c.feature_at)
        ).mappings().all()

    assert first.targets_considered == 1
    assert first.planned_rows == 4
    assert first.inserted == 4
    assert first.existing == 0
    assert second.inserted == 0
    assert second.existing == 4
    assert len(rows) == 4
    assert {row["feature_version"] for row in rows} == {"core-v1"}
    assert all(row["features"]["official_reference_distance"] is None for row in rows)
    assert all(row["missing_flags"]["official_reference_missing"] is True for row in rows)


def test_zero_trade_flow_with_feed_coverage_is_not_marked_missing() -> None:
    service = _service()
    engine = _engine()
    target = _target()
    feature_at = target.market_start_at + timedelta(minutes=2)
    coverage_at = feature_at - timedelta(seconds=1)

    with engine.begin() as connection:
        connection.execute(insert(raw_market_events), [_raw_coverage(coverage_at)])
        feature = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=feature_at + timedelta(hours=1),
        )

    assert feature.features["coinbase_trade_flow_buy_volume"] == 0.0
    assert feature.features["coinbase_trade_flow_sell_volume"] == 0.0
    assert feature.features["coinbase_trade_flow_trade_count"] == 0
    assert feature.missing_flags["coinbase_trade_flow_missing"] is False
    assert feature.source_cutoffs["coinbase_trade_flow"] == coverage_at.isoformat().replace(
        "+00:00", "Z"
    )


def test_all_persisted_source_cutoffs_are_no_later_than_feature_time() -> None:
    service = _service()
    engine = _engine()
    target = _target()
    feature_at = target.market_start_at + timedelta(minutes=2)

    with engine.begin() as connection:
        feature = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=feature_at + timedelta(hours=1),
        )

    for cutoff in feature.source_cutoffs.values():
        parsed = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        assert parsed <= feature_at


def test_feature_payload_contains_no_label_outcome_or_reference_substitution() -> None:
    service = _service()
    engine = _engine()
    target = _target()
    feature_at = target.market_start_at + timedelta(minutes=2)

    with engine.begin() as connection:
        feature = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=feature_at + timedelta(hours=1),
        )

    forbidden = {"official_outcome", "resolved_outcome", "start_reference", "end_reference"}
    assert forbidden.isdisjoint(feature.features)
    assert feature.features["official_reference_distance"] is None
    assert feature.missing_flags["official_reference_missing"] is True


def test_input_fingerprint_includes_zero_trade_coverage_evidence() -> None:
    service = _service()
    target = _target()
    feature_at = target.market_start_at + timedelta(minutes=2)

    first_engine = _engine()
    with first_engine.begin() as connection:
        connection.execute(
            insert(raw_market_events), [_raw_coverage(feature_at - timedelta(seconds=2))]
        )
        first = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=feature_at + timedelta(hours=1),
        )

    second_engine = _engine()
    with second_engine.begin() as connection:
        connection.execute(
            insert(raw_market_events), [_raw_coverage(feature_at - timedelta(seconds=1))]
        )
        second = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=feature_at + timedelta(hours=1),
        )

    assert first.features == second.features
    assert first.input_fingerprint != second.input_fingerprint
    assert first.feature_hash == second.feature_hash
