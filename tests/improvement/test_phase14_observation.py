import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from bp_engine import phase14_observation, phase14_observation_cli
from bp_engine.config import Settings, TradingMode
from bp_engine.features.v4_forward import V4_FORWARD_EPOCH


def _settings(tmp_path, **updates):
    values = {
        "database_url": "sqlite://",
        "storage_health_path": str(tmp_path),
        "mode": TradingMode.RESEARCH,
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_phase14_observation_composes_v3_v4_and_storage_without_redefining_metrics(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    now = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
    settings = _settings(tmp_path)
    observed = {}

    def fake_v3(engine_arg, *, recent_limit):
        observed["v3_engine"] = engine_arg
        observed["recent_limit"] = recent_limit
        return {
            "prediction_version": "v3-frozen-paper-v1",
            "summary": {
                "trade_signal_count": 31,
                "settled_order_count": 24,
                "current_cash": Decimal("165.29"),
            },
        }

    def fake_v4(connection, *, epoch_start, epoch_end=None):
        observed["v4_connection"] = connection
        observed["epoch_start"] = epoch_start
        observed["epoch_end"] = epoch_end
        return {
            "feature_version": "core-v4-regime-aware",
            "market_count": 269,
            "row_count": 1076,
            "regime": {
                "bull": {"market_count": 10},
                "bear": {"market_count": 8},
                "sideways_mixed": {"market_count": 251},
                "unknown": {"market_count": 0},
            },
            "future_cutoff_violation_count": 0,
            "polymarket_predictor_key_count": 0,
            "regime_invariant_violation_count": 0,
            "policy_selected": False,
            "training_run": False,
            "automatic_promotion": False,
        }

    def fake_storage(engine_arg, path, settings_arg, *, now):
        observed["storage_engine"] = engine_arg
        observed["storage_path"] = path
        observed["storage_settings"] = settings_arg
        observed["storage_now"] = now
        return {
            "status": "ok",
            "storage_mode": "partitioned",
            "guards": {
                "maintenance_fresh": True,
                "current_partition_present": True,
                "retention_current": True,
            },
        }

    monkeypatch.setattr(phase14_observation, "build_v3_paper_report", fake_v3)
    monkeypatch.setattr(phase14_observation, "build_v4_coverage_report", fake_v4)
    monkeypatch.setattr(
        phase14_observation,
        "build_composite_storage_health",
        fake_storage,
    )

    try:
        report = phase14_observation.build_phase14_observation_report(
            engine,
            settings,
            now=now,
            recent_limit=7,
        )
    finally:
        engine.dispose()

    assert observed["v3_engine"] is engine
    assert observed["recent_limit"] == 7
    assert observed["epoch_start"] == V4_FORWARD_EPOCH
    assert observed["epoch_end"] is None
    assert observed["storage_engine"] is engine
    assert observed["storage_path"] == tmp_path
    assert observed["storage_settings"] is settings
    assert observed["storage_now"] == now

    assert report["generated_at"] == now
    assert report["mode"] == "phase14_observation_only"
    assert report["v3_paper"]["summary"]["trade_signal_count"] == 31
    assert report["v4_forward_coverage"]["market_count"] == 269
    assert report["storage"]["status"] == "ok"
    assert report["integrity"]["research_zero_money"] is True
    assert report["integrity"]["v4"]["ok"] is True
    assert report["integrity"]["storage_ok"] is True
    assert report["integrity"]["all_observation_guards_ok"] is True


def test_phase14_observation_reports_unsafe_settings_instead_of_masking_them(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    settings = _settings(
        tmp_path,
        mode=TradingMode.PAPER,
        live_trading_enabled=True,
        max_trade_size_usd=5,
        max_daily_loss_usd=10,
    )

    monkeypatch.setattr(
        phase14_observation,
        "build_v3_paper_report",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        phase14_observation,
        "build_v4_coverage_report",
        lambda *args, **kwargs: {
            "future_cutoff_violation_count": 0,
            "polymarket_predictor_key_count": 0,
            "regime_invariant_violation_count": 0,
            "policy_selected": False,
            "training_run": False,
            "automatic_promotion": False,
        },
    )
    monkeypatch.setattr(
        phase14_observation,
        "build_composite_storage_health",
        lambda *args, **kwargs: {
            "status": "ok",
            "storage_mode": "partitioned",
            "guards": {
                "maintenance_fresh": True,
                "current_partition_present": True,
                "retention_current": True,
            },
        },
    )

    try:
        report = phase14_observation.build_phase14_observation_report(
            engine,
            settings,
        )
    finally:
        engine.dispose()

    assert report["safety"] == {
        "mode": "paper",
        "live_trading_enabled": True,
        "max_trade_size_usd": 5.0,
        "max_daily_loss_usd": 10.0,
        "research_zero_money": False,
    }
    assert report["integrity"]["all_observation_guards_ok"] is False


def test_phase14_observation_refuses_to_create_missing_storage_path(
    tmp_path,
    monkeypatch,
) -> None:
    missing = tmp_path / "does-not-exist"
    settings = _settings(tmp_path, storage_health_path=str(missing))
    engine = create_engine("sqlite://")

    def unexpected(*args, **kwargs):
        raise AssertionError("observation builder touched downstream data before path guard")

    monkeypatch.setattr(phase14_observation, "build_v3_paper_report", unexpected)
    monkeypatch.setattr(phase14_observation, "build_v4_coverage_report", unexpected)
    monkeypatch.setattr(
        phase14_observation,
        "build_composite_storage_health",
        unexpected,
    )

    try:
        with pytest.raises(FileNotFoundError, match="must already exist"):
            phase14_observation.build_phase14_observation_report(
                engine,
                settings,
            )
    finally:
        engine.dispose()

    assert missing.exists() is False


def test_phase14_observation_marks_v4_or_storage_integrity_failures(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine("sqlite://")
    settings = _settings(tmp_path)

    monkeypatch.setattr(
        phase14_observation,
        "build_v3_paper_report",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        phase14_observation,
        "build_v4_coverage_report",
        lambda *args, **kwargs: {
            "future_cutoff_violation_count": 1,
            "polymarket_predictor_key_count": 0,
            "regime_invariant_violation_count": 0,
            "policy_selected": False,
            "training_run": False,
            "automatic_promotion": False,
        },
    )
    monkeypatch.setattr(
        phase14_observation,
        "build_composite_storage_health",
        lambda *args, **kwargs: {
            "status": "critical",
            "storage_mode": "partitioned",
            "guards": {
                "maintenance_fresh": True,
                "current_partition_present": True,
                "retention_current": False,
            },
        },
    )

    try:
        report = phase14_observation.build_phase14_observation_report(
            engine,
            settings,
        )
    finally:
        engine.dispose()

    assert report["integrity"]["research_zero_money"] is True
    assert report["integrity"]["v4"]["ok"] is False
    assert report["integrity"]["storage_ok"] is False
    assert report["integrity"]["all_observation_guards_ok"] is False


def test_phase14_observation_cli_serializes_decimal_and_datetime() -> None:
    payload = {
        "cash": Decimal("165.290000"),
        "generated_at": datetime(2026, 9, 21, 13, 30, tzinfo=UTC),
    }

    rendered = phase14_observation_cli._json_value(payload)

    assert rendered == {
        "cash": "165.290000",
        "generated_at": "2026-09-21T13:30:00+00:00",
    }


def test_phase14_observation_source_truth_preserves_read_only_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    state = json.loads((root / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    start = (root / "START-HERE.md").read_text(encoding="utf-8")
    build = (root / "docs/BUILD-ORDER.md").read_text(encoding="utf-8")

    observation = state["phase_14_observation_report"]
    assert observation["status"] == "MERGED_MAIN_READ_ONLY_OBSERVATION_AVAILABLE_NOT_PRODUCTION_RUN"
    assert observation["database_writes"] is False
    assert observation["filesystem_creation"] is False
    assert observation["training_performed"] is False
    assert observation["tuning_performed"] is False
    assert observation["policy_selection_performed"] is False
    assert observation["service_or_timer_mutation"] is False
    assert observation["production_checkout_mutation"] is False
    assert observation["production_run_performed"] is False
    assert observation["merge_commit"] == "767cac3b78c20c9162b20c74eff770d4b2e8d1d8"
    assert observation["post_merge_ci_run_id"] == 35606952694
    assert observation["post_merge_ci_passed"] is True
    assert observation["live_trading_enabled"] is False
    assert observation["max_trade_size_usd"] == 0
    assert observation["max_daily_loss_usd"] == 0
    assert observation["automatic_promotion"] is False

    command = "python -m bp_engine.phase14_observation_cli --env-file /etc/bp/bp.env"
    assert observation["command"] == command
    assert command in start
    assert command in build
    assert "prospective observation only" in start.lower()
    assert "do not tune v3 from paper results" in build.lower()
    assert "no further authorized v3/v4 model or trading build step" in observation["next_action"].lower()
    assert "separate explicit sha-bound authorization boundary" in observation["next_action"].lower()
