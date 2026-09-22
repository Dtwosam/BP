import json
import os
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


def test_phase14_observation_cli_enforces_postgres_session_read_only(
    monkeypatch,
) -> None:
    captured = {}
    sentinel = object()
    database_url = "postgresql+psycopg://bp:test@localhost:5432/bp"

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(phase14_observation_cli, "create_engine", fake_create_engine)

    engine = phase14_observation_cli._create_observation_engine(database_url)

    assert engine is sentinel
    assert captured == {
        "url": database_url,
        "kwargs": {
            "pool_pre_ping": True,
            "connect_args": {
                "options": "-c default_transaction_read_only=on",
            },
        },
    }


def test_phase14_observation_cli_leaves_sqlite_connection_args_unchanged(
    monkeypatch,
) -> None:
    captured = {}
    sentinel = object()

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(phase14_observation_cli, "create_engine", fake_create_engine)

    engine = phase14_observation_cli._create_observation_engine("sqlite://")

    assert engine is sentinel
    assert captured == {
        "url": "sqlite://",
        "kwargs": {
            "pool_pre_ping": True,
        },
    }


def test_phase14_observation_postgres_engine_defaults_to_read_only() -> None:
    database_url = os.getenv("BP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage")

    engine = phase14_observation_cli._create_observation_engine(database_url)
    try:
        with engine.connect() as connection:
            read_only = connection.exec_driver_sql(
                "SHOW default_transaction_read_only"
            ).scalar_one()
    finally:
        engine.dispose()

    assert read_only == "on"


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
    assert observation["status"] == (
        "CLOUDSHELL_RUNTIME_BRIDGE_PENDING_VALIDATION_NOT_PRODUCTION_RUN"
    )
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
    assert (
        observation["database_read_only_hardening_status"]
        == "MERGED_MAIN_POST_MERGE_CI_GREEN_NOT_PRODUCTION_RUN"
    )
    assert observation["database_read_only_hardening_pr"] == 219
    assert (
        observation["database_read_only_hardening_validation_head"]
        == "3747f4bbf13ab3fec12b4b08c10df52c0b0dbbec"
    )
    assert observation["database_read_only_hardening_ci_run_id"] == 35608740215
    assert observation["database_read_only_hardening_test_count"] == 1266
    assert (
        observation["database_read_only_hardening_historical_backfill_smoke_run_id"]
        == 35608740066
    )
    assert (
        observation["database_read_only_hardening_live_recorder_smoke_run_id"]
        == 35608740154
    )
    assert (
        observation["database_read_only_hardening_recorder_short_soak_run_id"]
        == 35608740253
    )
    assert (
        observation["database_read_only_hardening_merge_commit"]
        == "cc646933c82c4a0b9a613d98a769c07aca9cfe49"
    )
    assert observation["database_read_only_hardening_post_merge_ci_run_id"] == 35609529018
    assert observation["database_read_only_hardening_post_merge_ci_passed"] is True
    assert observation["postgres_session_default_read_only"] is True
    assert (
        observation["postgres_session_read_only_option"]
        == "-c default_transaction_read_only=on"
    )
    assert observation["postgres_session_read_only_verified_in_ci"] is True
    assert observation["sqlite_connection_args_unchanged"] is True
    assert observation["live_trading_enabled"] is False
    assert observation["max_trade_size_usd"] == 0
    assert observation["max_daily_loss_usd"] == 0
    assert observation["automatic_promotion"] is False

    command = "bash scripts/deploy/phase14_observation_cloudshell.sh"
    module_command = "python -m bp_engine.phase14_observation_cli --env-file /etc/bp/bp.env"
    assert observation["command"] == command
    assert observation["repository_module_command"] == module_command
    assert observation["deployed_checkout_contains_unified_observation_cli"] is False
    assert observation["production_runtime_bridge_required"] is True
    assert observation["production_runtime_bridge_postgres_read_only"] is True
    assert observation["production_runtime_bridge_filesystem_creation"] is False
    assert observation["production_runtime_bridge_service_or_timer_mutation"] is False
    assert observation["production_runtime_bridge_checkout_mutation"] is False
    assert command in start
    assert command in build
    assert module_command in build
    assert "prospective observation only" in start.lower()
    assert "do not tune v3 from paper results" in build.lower()
    next_action = observation["next_action"].lower()
    assert "validate and merge the read-only cloud shell runtime bridge" in next_action
    assert "run one production observation" in next_action
    assert "no report output authorizes tuning" in next_action
