from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.live_prediction.models import LivePolicySpec
from bp_engine.storage import schema


def _module():
    return importlib.import_module("bp_engine.live_prediction.cli")


def _policy() -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id="phase9-300-test",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id="phase8-300-test",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id="phase7-300-test",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        selected_offset_seconds=240,
        calibration_fit=CalibrationFit(
            method="identity",
            intercept=None,
            coefficient=None,
        ),
        edge_config=EdgeConfig(
            fee_rate=0.07,
            slippage_buffer=0.01,
            min_edge_grid=(0.02,),
            min_validation_trades=1,
        ),
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def _market_values(condition_id: str, *, start_at: datetime) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{condition_id}",
        "event_id": f"event-{condition_id}",
        "condition_id": condition_id,
        "slug": f"btc-updown-{condition_id}",
        "question": "Will BTC be up?",
        "horizon_seconds": 300,
        "start_at": start_at,
        "end_at": start_at + timedelta(seconds=300),
        "up_token_id": f"up-{condition_id}",
        "down_token_id": f"down-{condition_id}",
        "resolution_source": "official-rules",
        "rules_text": "Up if final reference exceeds start reference.",
        "rules_hash": f"rules-{condition_id}",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "resolved_outcome": None,
        "discovered_at": start_at - timedelta(minutes=5),
        "updated_at": start_at,
    }


def _all_option_strings(parser) -> set[str]:
    options: set[str] = set()
    for action in parser._actions:
        options.update(action.option_strings)
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for child in choices.values():
                options.update(_all_option_strings(child))
    return options


def test_service_parser_requires_explicit_sources_and_safe_defaults() -> None:
    module = _module()
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "run",
            "--source-calibration-run-id",
            "phase9-300-a",
            "--source-calibration-run-id",
            "phase9-900-b",
        ]
    )

    assert args.source_calibration_run_id == ["phase9-300-a", "phase9-900-b"]
    assert args.poll_interval_seconds == 1.0
    assert args.max_lateness_seconds == 10

    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_duplicate_policy_sources_are_rejected_before_runtime() -> None:
    module = _module()
    with pytest.raises(ValueError, match="unique"):
        module.validate_source_run_ids(("phase9-300-a", "phase9-300-a"))


def test_cli_exposes_no_money_wallet_private_key_or_order_options() -> None:
    module = _module()
    options = _all_option_strings(module.build_parser())
    rendered = " ".join(sorted(options)).lower()

    for forbidden in (
        "trade-size",
        "wallet",
        "private-key",
        "signing",
        "allowance",
        "order",
    ):
        assert forbidden not in rendered


def test_integrity_report_is_read_only_and_counts_missed_coverage() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 26, 18, 0, tzinfo=UTC)
    now = start + timedelta(seconds=251)

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets).values(
                **_market_values("missing", start_at=start)
            )
        )
        before_predictions = connection.execute(
            select(schema.live_predictions.c.prediction_id)
        ).all()
        before_evaluations = connection.execute(
            select(schema.live_prediction_evaluations.c.prediction_id)
        ).all()

        report = module.build_integrity_report(
            connection,
            policies={300: _policy()},
            now=now,
            max_lateness_seconds=10,
        )

        after_predictions = connection.execute(
            select(schema.live_predictions.c.prediction_id)
        ).all()
        after_evaluations = connection.execute(
            select(schema.live_prediction_evaluations.c.prediction_id)
        ).all()

    assert before_predictions == after_predictions == []
    assert before_evaluations == after_evaluations == []
    assert report == {
        "scheduled_eligible_markets": 1,
        "prediction_count": 0,
        "late_or_missed_coverage": 1,
        "pre_outcome_timing_violations": 0,
        "duplicate_natural_keys": 0,
        "semantic_hash_violations": 0,
        "evaluation_count": 0,
        "prediction_mutation_violations": 0,
    }
