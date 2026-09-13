from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.features import v3_coverage
from bp_engine.features.v3_models import V3_FEATURE_VERSION
from bp_engine.storage import schema
from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG
from bp_engine.v3_research.exclusions import (
    ExclusionManifestError,
    build_exclusion_manifest,
)

try:
    from bp_engine.v3_research import readiness as readiness_module
except ImportError:
    readiness_module = None

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


def _market_rows(
    condition_id: str,
    market_start: datetime,
    *,
    offsets: tuple[int, ...] = (60, 120, 180, 240),
    missing_current: tuple[str, ...] = (),
    missing_returns: tuple[str, ...] = (),
    future_cutoff: bool = False,
    polymarket_key: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, offset in enumerate(offsets, start=1):
        feature_at = market_start + timedelta(seconds=offset)
        features: dict[str, object] = {
            "seconds_elapsed": offset,
            "seconds_remaining": 300 - offset,
            "fraction_elapsed": offset / 300,
            "horizon_seconds": 300,
        }
        for field in RETURN_FIELDS:
            structural = field.endswith("_return_120s") and offset == 60
            features[field] = None if structural or field in missing_returns else 0.001
        if polymarket_key:
            features["pm_up_price"] = 0.55

        missing_flags: dict[str, bool] = {}
        source_cutoffs: dict[str, str] = {}
        for prefix in PREFIXES:
            is_missing = prefix in missing_current
            missing_flags[f"{prefix}_current_missing"] = is_missing
            missing_flags[f"{prefix}_current_stale"] = False
            if not is_missing:
                source_cutoffs[f"{prefix}_current_state"] = _z(
                    feature_at - timedelta(seconds=1)
                )
        if future_cutoff:
            source_cutoffs["diagnostic_future"] = _z(feature_at + timedelta(seconds=1))

        rows.append(
            {
                "condition_id": condition_id,
                "slug": f"btc-updown-5m-{condition_id}",
                "horizon_seconds": 300,
                "market_start_at": market_start,
                "market_end_at": market_start + timedelta(seconds=300),
                "feature_at": feature_at,
                "feature_offset_seconds": offset,
                "feature_version": V3_FEATURE_VERSION,
                "features": features,
                "missing_flags": missing_flags,
                "source_cutoffs": source_cutoffs,
                "input_fingerprint": f"{index:064x}",
                "feature_hash": f"{index + 100:064x}",
                "generated_at": feature_at + timedelta(seconds=1),
            }
        )
    return rows


def _connection(rows: list[dict[str, object]]):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    connection = engine.connect()
    connection.execute(insert(schema.market_features), rows)
    connection.commit()
    return connection


def test_v3_coverage_report_supports_epoch_and_exclusion_filters() -> None:
    config = FROZEN_V3_GATE_B_CONFIG
    rows = [
        *_market_rows("eligible", config.epoch_start),
        *_market_rows("excluded", config.epoch_start + timedelta(minutes=5)),
        *_market_rows("before", config.epoch_start - timedelta(minutes=5)),
    ]
    with _connection(rows) as connection:
        default = v3_coverage.build_v3_coverage_report(connection)
        filtered = v3_coverage.build_v3_coverage_report(
            connection,
            epoch_start=config.epoch_start,
            epoch_end=config.epoch_end,
            excluded_condition_ids=("excluded",),
        )

    assert default["row_count"] == 12
    assert default["market_count"] == 3
    assert filtered["row_count"] == 4
    assert filtered["market_count"] == 1
    assert filtered["offsets"] == [60, 120, 180, 240]
    assert filtered["coverage_input_sha256"] != default["coverage_input_sha256"]


def test_v3_gate_b_readiness_passes_only_outcome_blind_coverage() -> None:
    assert readiness_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    rows = [
        *_market_rows("eligible", config.epoch_start),
        *_market_rows(
            "diagnosis-market",
            config.epoch_start - timedelta(minutes=5),
            future_cutoff=True,
            polymarket_key=True,
        ),
    ]
    diagnosis = build_exclusion_manifest(
        kind="diagnosis",
        condition_ids=("diagnosis-market",),
    )
    consumed = build_exclusion_manifest(
        kind="consumed_v2_final_holdout",
        condition_ids=("old-holdout",),
    )

    with _connection(rows) as connection:
        first = readiness_module.assess_v3_gate_b_readiness(
            connection,
            as_of=config.epoch_end,
            diagnosis_exclusions=diagnosis,
            consumed_v2_final_holdout_exclusions=consumed,
        )
        second = readiness_module.assess_v3_gate_b_readiness(
            connection,
            as_of=config.epoch_end,
            diagnosis_exclusions=diagnosis,
            consumed_v2_final_holdout_exclusions=consumed,
        )

    assert first == second
    assert first["ready"] is True
    assert first["blocking_reasons"] == ()
    assert first["coverage"]["market_count"] == 1
    assert first["coverage"]["future_cutoff_violation_count"] == 0
    assert first["coverage"]["polymarket_predictor_key_count"] == 0
    assert first["source_availability"] == {
        "bybit_linear": 1.0,
        "bybit_spot": 1.0,
        "coinbase": 1.0,
    }
    for field in RETURN_FIELDS:
        assert first["non_structural_return_availability"][field] == 1.0
    assert len(first["readiness_input_sha256"]) == 64
    int(first["readiness_input_sha256"], 16)


def test_v3_gate_b_readiness_rejects_current_epoch_exclusion_ids() -> None:
    assert readiness_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    rows = _market_rows("prospective-market", config.epoch_start)
    diagnosis = build_exclusion_manifest(
        kind="diagnosis",
        condition_ids=("prospective-market",),
    )
    consumed = build_exclusion_manifest(
        kind="consumed_v2_final_holdout",
        condition_ids=(),
    )

    with _connection(rows) as connection:
        with pytest.raises(ExclusionManifestError, match="frozen V3 epoch"):
            readiness_module.assess_v3_gate_b_readiness(
                connection,
                as_of=config.epoch_end,
                diagnosis_exclusions=diagnosis,
                consumed_v2_final_holdout_exclusions=consumed,
            )


def test_v3_gate_b_readiness_returns_sorted_blocking_reasons() -> None:
    assert readiness_module is not None
    config = FROZEN_V3_GATE_B_CONFIG
    rows = _market_rows(
        "broken",
        config.epoch_start,
        offsets=(60, 120, 180),
        missing_current=("coinbase",),
        missing_returns=("coinbase_return_30s",),
        future_cutoff=True,
        polymarket_key=True,
    )
    diagnosis = build_exclusion_manifest(kind="diagnosis", condition_ids=())
    consumed = build_exclusion_manifest(
        kind="consumed_v2_final_holdout",
        condition_ids=(),
    )

    with _connection(rows) as connection:
        result = readiness_module.assess_v3_gate_b_readiness(
            connection,
            as_of=config.epoch_end - timedelta(seconds=1),
            diagnosis_exclusions=diagnosis,
            consumed_v2_final_holdout_exclusions=consumed,
        )

    assert result["ready"] is False
    assert result["blocking_reasons"] == (
        "coinbase_current_state_availability_below_0.90",
        "coinbase_return_30s_availability_below_0.90",
        "epoch_incomplete",
        "future_cutoff_violations",
        "incomplete_market_offsets",
        "polymarket_predictor_keys_present",
    )


def test_v3_gate_b_readiness_source_is_outcome_and_training_isolated() -> None:
    assert readiness_module is not None
    source = inspect.getsource(readiness_module).lower()
    for forbidden in (
        "market_labels",
        "official_outcome",
        "live_prediction_evaluations",
        "paper_settlements",
        "realized_pnl",
        "calibration",
        "adaptive_train",
        "xgboost",
        "logisticregression",
    ):
        assert forbidden not in source
