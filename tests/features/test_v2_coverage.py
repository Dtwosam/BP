import inspect
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.features import v2_coverage
from bp_engine.features.v2_models import V2_FEATURE_VERSION
from bp_engine.storage import schema

START = datetime(2026, 9, 2, 13, 0, tzinfo=UTC)


def _z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _feature_row(
    *,
    condition_id: str,
    offset: int,
    up_source_age: float | None,
    up_availability_age: float | None,
    down_source_age: float | None,
    down_availability_age: float | None,
    up_book_missing: bool,
    up_book_stale: bool,
    up_book_age: float | None,
    down_book_missing: bool,
    down_book_stale: bool,
    down_book_age: float | None,
    input_char: str,
    feature_char: str,
    invalid_value: object | None = None,
    future_cutoff: bool = False,
) -> dict[str, object]:
    feature_at = START + timedelta(seconds=offset)
    features: dict[str, object] = {
        "seconds_elapsed": offset,
        "seconds_remaining": 300 - offset,
        "fraction_elapsed": offset / 300,
        "horizon_seconds": 300,
        "pm_up_last_trade_price": None if up_source_age is None else 0.61,
        "pm_up_last_trade_source_age_s": up_source_age,
        "pm_up_last_trade_availability_age_s": up_availability_age,
        "pm_down_last_trade_price": None if down_source_age is None else 0.39,
        "pm_down_last_trade_source_age_s": down_source_age,
        "pm_down_last_trade_availability_age_s": down_availability_age,
        "pm_up_best_bid": None if up_book_missing else 0.60,
        "pm_up_best_ask": None if up_book_missing else 0.62,
        "pm_up_mid": None if up_book_missing else 0.61,
        "pm_up_spread": None if up_book_missing else 0.02,
        "pm_up_bid_depth": None if up_book_missing else 10.0,
        "pm_up_ask_depth": None if up_book_missing else 8.0,
        "pm_up_book_imbalance": None if up_book_missing else 1.0 / 9.0,
        "pm_down_best_bid": None if down_book_missing else 0.38,
        "pm_down_best_ask": None if down_book_missing else 0.40,
        "pm_down_mid": None if down_book_missing else 0.39,
        "pm_down_spread": None if down_book_missing else 0.02,
        "pm_down_bid_depth": None if down_book_missing else 9.0,
        "pm_down_ask_depth": None if down_book_missing else 7.0,
        "pm_down_book_imbalance": None if down_book_missing else 0.125,
    }
    if invalid_value is not None:
        features["pm_up_spread"] = invalid_value

    missing_flags = {
        "pm_up_last_trade_missing": up_source_age is None,
        "pm_down_last_trade_missing": down_source_age is None,
        "pm_up_book_missing": up_book_missing,
        "pm_up_book_stale": up_book_stale,
        "pm_down_book_missing": down_book_missing,
        "pm_down_book_stale": down_book_stale,
    }
    source_cutoffs: dict[str, str] = {}
    if up_source_age is not None:
        source_cutoffs["pm_up_last_trade_source"] = _z(
            feature_at - timedelta(seconds=up_source_age)
        )
        source_cutoffs["pm_up_last_trade_received"] = _z(
            feature_at - timedelta(seconds=up_availability_age or 0.0)
        )
    if down_source_age is not None:
        source_cutoffs["pm_down_last_trade_source"] = _z(
            feature_at - timedelta(seconds=down_source_age)
        )
        source_cutoffs["pm_down_last_trade_received"] = _z(
            feature_at - timedelta(seconds=down_availability_age or 0.0)
        )
    if up_book_age is not None:
        source_cutoffs["pm_up_book_state"] = _z(feature_at - timedelta(seconds=up_book_age))
    if down_book_age is not None:
        source_cutoffs["pm_down_book_state"] = _z(
            feature_at - timedelta(seconds=down_book_age)
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
        "feature_version": V2_FEATURE_VERSION,
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
            up_source_age=1.0,
            up_availability_age=0.5,
            down_source_age=None,
            down_availability_age=None,
            up_book_missing=False,
            up_book_stale=False,
            up_book_age=2.0,
            down_book_missing=True,
            down_book_stale=True,
            down_book_age=12.0,
            input_char="1",
            feature_char="a",
        ),
        _feature_row(
            condition_id="market-a",
            offset=120,
            up_source_age=3.0,
            up_availability_age=2.0,
            down_source_age=4.0,
            down_availability_age=3.0,
            up_book_missing=True,
            up_book_stale=False,
            up_book_age=None,
            down_book_missing=False,
            down_book_stale=False,
            down_book_age=5.0,
            input_char="2",
            feature_char="b",
            invalid_value="bad",
        ),
        _feature_row(
            condition_id="market-b",
            offset=60,
            up_source_age=10.0,
            up_availability_age=9.0,
            down_source_age=8.0,
            down_availability_age=7.0,
            up_book_missing=False,
            up_book_stale=False,
            up_book_age=1.0,
            down_book_missing=False,
            down_book_stale=False,
            down_book_age=2.0,
            input_char="3",
            feature_char="c",
            invalid_value=float("nan"),
            future_cutoff=True,
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
                "condition_id": "ignored-v1",
                "slug": "ignored-v1",
                "feature_version": "core-v1",
                "input_fingerprint": "f" * 64,
                "feature_hash": "e" * 64,
            },
        )
        return v2_coverage.build_v2_coverage_report(connection)


def test_v2_coverage_report_is_deterministic_and_descriptive_only() -> None:
    first = _report(_rows())
    second = _report(list(reversed(_rows())))

    assert first == second
    assert first["feature_version"] == V2_FEATURE_VERSION
    assert first["row_count"] == 3
    assert first["market_count"] == 2
    assert first["offsets"] == [60, 120]
    assert first["by_offset"] == {
        "60": {"row_count": 2, "market_count": 2},
        "120": {"row_count": 1, "market_count": 1},
    }
    assert first["last_trade"]["up"]["available_count"] == 3
    assert first["last_trade"]["up"]["missing_count"] == 0
    assert first["last_trade"]["down"]["available_count"] == 2
    assert first["last_trade"]["down"]["missing_count"] == 1
    assert first["last_trade"]["up"]["source_age_s"] == {
        "count": 3,
        "min": 1.0,
        "median": 3.0,
        "p90": 10.0,
        "max": 10.0,
    }
    assert first["last_trade"]["down"]["source_age_s"] == {
        "count": 2,
        "min": 4.0,
        "median": 6.0,
        "p90": 8.0,
        "max": 8.0,
    }
    assert first["book"]["up"]["available_count"] == 2
    assert first["book"]["up"]["missing_count"] == 1
    assert first["book"]["up"]["stale_count"] == 0
    assert first["book"]["up"]["age_s"] == {
        "count": 2,
        "min": 1.0,
        "median": 1.5,
        "p90": 2.0,
        "max": 2.0,
    }
    assert first["book"]["down"]["available_count"] == 2
    assert first["book"]["down"]["missing_count"] == 1
    assert first["book"]["down"]["stale_count"] == 1
    assert first["book"]["down"]["age_s"] == {
        "count": 3,
        "min": 2.0,
        "median": 5.0,
        "p90": 12.0,
        "max": 12.0,
    }
    assert first["invalid_nonfinite_value_count"] == 2
    assert first["future_cutoff_violation_count"] == 1
    assert len(first["coverage_input_sha256"]) == 64
    int(first["coverage_input_sha256"], 16)
    assert first["policy_selected"] is False
    assert first["automatic_promotion"] is False

    text = repr(first).lower()
    for forbidden in (
        "max_last_trade_age_seconds",
        "timing_choice",
        "accuracy",
        "profit",
        "loss",
        "edge_threshold",
    ):
        assert forbidden not in text


def test_v2_coverage_empty_report_is_explicit_not_optimistic() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        report = v2_coverage.build_v2_coverage_report(connection)

    assert report["row_count"] == 0
    assert report["market_count"] == 0
    assert report["offsets"] == []
    assert report["by_offset"] == {}
    assert report["last_trade"]["up"]["source_age_s"] == {
        "count": 0,
        "min": None,
        "median": None,
        "p90": None,
        "max": None,
    }
    assert report["policy_selected"] is False
    assert report["automatic_promotion"] is False


def test_v2_coverage_source_is_outcome_and_policy_isolated() -> None:
    source = inspect.getsource(v2_coverage).lower()
    for forbidden in (
        "market_labels",
        "official_outcome",
        "live_prediction",
        "paper_settlement",
        "paper_order",
        "paper_fill",
        "pnl",
        "calibration",
        "min_edge",
        "phase9",
    ):
        assert forbidden not in source


def test_age_summary_rejects_nonfinite_values_from_summary_population() -> None:
    summary = v2_coverage.summarize_finite((1.0, float("nan"), float("inf"), 5.0))

    assert summary == {
        "count": 2,
        "min": 1.0,
        "median": 3.0,
        "p90": 5.0,
        "max": 5.0,
    }
    assert math.isfinite(summary["median"])
