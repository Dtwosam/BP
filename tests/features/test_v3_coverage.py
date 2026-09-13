import inspect
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.features import v3_coverage
from bp_engine.features.v3_models import V3_FEATURE_VERSION
from bp_engine.storage import schema

START = datetime(2026, 9, 13, 14, 0, tzinfo=UTC)
PREFIXES = ("coinbase", "bybit_spot", "bybit_linear")
RETURN_FIELDS = tuple(
    f"{prefix}_{suffix}"
    for prefix in PREFIXES
    for suffix in (
        "return_from_market_start",
        "return_30s",
        "return_60s",
        "return_120s",
    )
)


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _feature_row(
    *,
    condition_id: str,
    offset: int,
    input_char: str,
    feature_char: str,
    missing_current: tuple[str, ...] = (),
    stale_current: tuple[str, ...] = (),
    missing_returns: tuple[str, ...] = (),
    current_ages: dict[str, float] | None = None,
    future_cutoff: bool = False,
    extra_feature: tuple[str, object] | None = None,
) -> dict[str, object]:
    feature_at = START + timedelta(seconds=offset)
    ages = current_ages or {
        "coinbase": 1.0,
        "bybit_spot": 2.0,
        "bybit_linear": 3.0,
    }
    features: dict[str, object] = {
        "seconds_elapsed": offset,
        "seconds_remaining": 300 - offset,
        "fraction_elapsed": offset / 300,
        "horizon_seconds": 300,
    }
    for field in RETURN_FIELDS:
        features[field] = None if field in missing_returns else 0.001
    features.update(
        {
            "coinbase_bybit_spot_return_spread": 0.0,
            "coinbase_bybit_spot_direction_agree": 1.0,
            "spot_linear_direction_agree": 1.0,
            "bybit_linear_vs_spot_basis": 0.0005,
            "bybit_linear_funding_rate": 0.0001,
            "bybit_linear_open_interest": 1000.0,
        }
    )
    if extra_feature is not None:
        features[extra_feature[0]] = extra_feature[1]

    missing_flags: dict[str, bool] = {}
    source_cutoffs: dict[str, str] = {}
    for prefix in PREFIXES:
        missing_flags[f"{prefix}_current_missing"] = prefix in missing_current
        missing_flags[f"{prefix}_current_stale"] = prefix in stale_current
        if prefix not in missing_current:
            source_cutoffs[f"{prefix}_current_state"] = _z(
                feature_at - timedelta(seconds=ages[prefix])
            )
    if future_cutoff:
        source_cutoffs["diagnostic_future"] = _z(feature_at + timedelta(seconds=1))

    return {
        "condition_id": condition_id,
        "slug": f"btc-updown-5m-{condition_id}",
        "horizon_seconds": 300,
        "market_start_at": START,
        "market_end_at": START + timedelta(seconds=300),
        "feature_at": feature_at,
        "feature_offset_seconds": offset,
        "feature_version": V3_FEATURE_VERSION,
        "features": features,
        "missing_flags": missing_flags,
        "source_cutoffs": source_cutoffs,
        "input_fingerprint": input_char * 64,
        "feature_hash": feature_char * 64,
        "generated_at": START + timedelta(hours=1),
    }


def _rows() -> list[dict[str, object]]:
    return [
        _feature_row(
            condition_id="market-a",
            offset=60,
            input_char="1",
            feature_char="a",
            missing_current=("bybit_linear",),
            missing_returns=("bybit_linear_return_30s",),
            current_ages={"coinbase": 1.0, "bybit_spot": 2.0, "bybit_linear": 3.0},
        ),
        _feature_row(
            condition_id="market-a",
            offset=120,
            input_char="2",
            feature_char="b",
            stale_current=("bybit_spot",),
            missing_returns=("coinbase_return_120s",),
            current_ages={"coinbase": 3.0, "bybit_spot": 12.0, "bybit_linear": 4.0},
        ),
        _feature_row(
            condition_id="market-b",
            offset=60,
            input_char="3",
            feature_char="c",
            current_ages={"coinbase": 10.0, "bybit_spot": 8.0, "bybit_linear": 6.0},
            future_cutoff=True,
            extra_feature=("pm_up_price", 0.7),
        ),
    ]


def _report(rows: list[dict[str, object]]) -> dict[str, object]:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(schema.market_features), rows)
        connection.execute(
            insert(schema.market_features),
            {
                **rows[0],
                "condition_id": "ignored-v2",
                "slug": "ignored-v2",
                "feature_version": "core-v2-last-trade",
                "input_fingerprint": "f" * 64,
                "feature_hash": "e" * 64,
            },
        )
        return v3_coverage.build_v3_coverage_report(connection)


def test_v3_coverage_report_is_deterministic_outcome_blind_and_descriptive() -> None:
    first = _report(_rows())
    second = _report(list(reversed(_rows())))

    assert first == second
    assert first["feature_version"] == V3_FEATURE_VERSION
    assert first["row_count"] == 3
    assert first["market_count"] == 2
    assert first["offsets"] == [60, 120]
    assert first["by_offset"] == {
        "60": {"row_count": 2, "market_count": 2},
        "120": {"row_count": 1, "market_count": 1},
    }
    assert first["sources"]["coinbase"]["current_state"] == {
        "available_count": 3,
        "missing_count": 0,
        "stale_count": 0,
        "age_s": {
            "count": 3,
            "min": 1.0,
            "median": 3.0,
            "p90": 10.0,
            "max": 10.0,
        },
    }
    assert first["sources"]["bybit_spot"]["current_state"]["available_count"] == 3
    assert first["sources"]["bybit_spot"]["current_state"]["missing_count"] == 0
    assert first["sources"]["bybit_spot"]["current_state"]["stale_count"] == 1
    assert first["sources"]["bybit_linear"]["current_state"]["available_count"] == 2
    assert first["sources"]["bybit_linear"]["current_state"]["missing_count"] == 1
    assert first["returns"]["coinbase_return_120s"] == {
        "available_count": 2,
        "missing_count": 1,
    }
    assert first["returns"]["bybit_linear_return_30s"] == {
        "available_count": 2,
        "missing_count": 1,
    }
    assert first["returns"]["coinbase_return_30s"] == {
        "available_count": 3,
        "missing_count": 0,
    }
    assert first["future_cutoff_violation_count"] == 1
    assert first["polymarket_predictor_key_count"] == 1
    assert len(first["coverage_input_sha256"]) == 64
    int(first["coverage_input_sha256"], 16)
    assert first["policy_selected"] is False
    assert first["training_run"] is False
    assert first["automatic_promotion"] is False


def test_v3_coverage_empty_report_is_explicit() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        report = v3_coverage.build_v3_coverage_report(connection)

    assert report["row_count"] == 0
    assert report["market_count"] == 0
    assert report["offsets"] == []
    assert report["by_offset"] == {}
    assert report["future_cutoff_violation_count"] == 0
    assert report["polymarket_predictor_key_count"] == 0
    assert report["policy_selected"] is False
    assert report["training_run"] is False
    assert report["automatic_promotion"] is False


def test_v3_coverage_source_is_outcome_training_and_policy_isolated() -> None:
    source = inspect.getsource(v3_coverage).lower()
    for forbidden in (
        "market_labels",
        "official_outcome",
        "live_prediction_evaluations",
        "paper_settlements",
        "realized_pnl",
        "calibration",
        "adaptive_train",
    ):
        assert forbidden not in source
