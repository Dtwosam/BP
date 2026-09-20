from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.features.v4_coverage import build_v4_coverage_report
from bp_engine.features.v4_models import V4_FEATURE_VERSION
from bp_engine.storage import schema

START = datetime(2026, 9, 20, 12, 45, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _insert_feature(
    connection,
    *,
    condition_id: str,
    offset: int,
    regime: str,
    future_cutoff: bool = False,
    polymarket_key: bool = False,
) -> None:
    feature_at = START + timedelta(seconds=offset)
    features = {
        "coinbase_return_from_market_start": 0.001,
        "coinbase_return_30s": 0.001,
        "coinbase_return_60s": 0.001,
        "coinbase_return_120s": 0.001,
        "bybit_spot_return_from_market_start": 0.001,
        "bybit_spot_return_30s": 0.001,
        "bybit_spot_return_60s": 0.001,
        "bybit_spot_return_120s": 0.001,
        "bybit_linear_return_from_market_start": 0.001,
        "bybit_linear_return_30s": 0.001,
        "bybit_linear_return_60s": 0.001,
        "bybit_linear_return_120s": 0.001,
        "coinbase_return_5m": 0.002,
        "coinbase_return_15m": 0.003,
        "coinbase_return_60m": 0.004,
        "bybit_spot_return_5m": 0.002,
        "bybit_spot_return_15m": 0.003,
        "bybit_spot_return_60m": 0.004,
        "bybit_linear_return_5m": 0.002,
        "bybit_linear_return_15m": 0.003,
        "bybit_linear_return_60m": 0.004,
        "regime_bull": 1.0 if regime == "bull" else 0.0,
        "regime_bear": 1.0 if regime == "bear" else 0.0,
        "regime_sideways_mixed": 1.0 if regime == "sideways_mixed" else 0.0,
    }
    if polymarket_key:
        features["pm_up_ask"] = 0.5
    source_cutoff = feature_at + timedelta(seconds=1) if future_cutoff else feature_at
    missing_flags = {}
    source_cutoffs = {}
    for prefix in ("coinbase", "bybit_spot", "bybit_linear"):
        missing_flags[f"{prefix}_current_missing"] = False
        missing_flags[f"{prefix}_current_stale"] = False
        source_cutoffs[f"{prefix}_current_state"] = source_cutoff.isoformat()
        for horizon in ("5m", "15m", "60m"):
            missing_flags[f"{prefix}_regime_trailing_{horizon}_missing"] = False
            missing_flags[f"{prefix}_regime_trailing_{horizon}_stale"] = False
            source_cutoffs[
                f"{prefix}_regime_trailing_{horizon}_state"
            ] = (feature_at - timedelta(minutes=5)).isoformat()
    connection.execute(
        insert(schema.market_features).values(
            condition_id=condition_id,
            slug=f"btc-updown-5m-{condition_id}",
            horizon_seconds=300,
            market_start_at=START,
            market_end_at=START + timedelta(seconds=300),
            feature_at=feature_at,
            feature_offset_seconds=offset,
            feature_version=V4_FEATURE_VERSION,
            features=features,
            missing_flags=missing_flags,
            source_cutoffs=source_cutoffs,
            input_fingerprint="a" * 64,
            feature_hash="b" * 64,
            generated_at=START + timedelta(minutes=10),
        )
    )


def test_v4_coverage_reports_regimes_without_selecting_policy() -> None:
    engine = _engine()
    with engine.begin() as connection:
        for offset in (60, 120, 180, 240):
            _insert_feature(
                connection,
                condition_id="bull-market",
                offset=offset,
                regime="bull",
            )
        report = build_v4_coverage_report(connection, epoch_start=START)

    assert report["feature_version"] == V4_FEATURE_VERSION
    assert report["row_count"] == 4
    assert report["market_count"] == 1
    assert report["offsets"] == [60, 120, 180, 240]
    assert report["regime"]["bull"]["market_count"] == 1
    assert report["regime"]["bear"]["market_count"] == 0
    assert report["future_cutoff_violation_count"] == 0
    assert report["polymarket_predictor_key_count"] == 0
    assert report["regime_invariant_violation_count"] == 0
    assert report["policy_selected"] is False
    assert report["training_run"] is False
    assert report["automatic_promotion"] is False


def test_v4_coverage_detects_future_cutoff_and_polymarket_predictor() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _insert_feature(
            connection,
            condition_id="bad-market",
            offset=60,
            regime="sideways_mixed",
            future_cutoff=True,
            polymarket_key=True,
        )
        report = build_v4_coverage_report(connection, epoch_start=START)

    assert report["future_cutoff_violation_count"] == 3
    assert report["polymarket_predictor_key_count"] == 1
