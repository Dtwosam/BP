from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, inspect
from sqlalchemy.dialects.postgresql import JSONB

from bp_engine.live_readiness.models import LiveRiskDecision, RuleResult
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


def _modules():
    storage_module = importlib.import_module("bp_engine.storage.live_readiness_schema")
    repo_module = importlib.import_module("bp_engine.live_readiness.repository")
    return storage_module, repo_module


def _setup():
    assert DATABASE_URL is not None
    storage_module, repo_module = _modules()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    tables = (
        storage_module.live_order_events,
        storage_module.live_order_intents,
        storage_module.live_risk_decisions,
        storage_module.live_reconciliation_runs,
        storage_module.live_readiness_checks,
    )
    with engine.begin() as connection:
        for table in tables:
            connection.execute(delete(table))
    return storage_module, repo_module, engine


def _eligible_decision() -> LiveRiskDecision:
    return LiveRiskDecision(
        eligible=True,
        reasons=(),
        rules=(RuleResult(rule="live_interlock", passed=True, reason="passed"),),
        policy_sha256=SHA_A,
        semantic_sha256=SHA_B,
    )


def test_migration_declares_all_append_only_live_tables_and_jsonb() -> None:
    sql = Path("migrations/0014_live_readiness.sql").read_text(encoding="utf-8")
    for table_name in (
        "live_readiness_checks",
        "live_risk_decisions",
        "live_order_intents",
        "live_order_events",
        "live_reconciliation_runs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql
    assert "JSONB" in sql
    assert "UNIQUE (prediction_id, policy_version)" in sql
    assert "UNIQUE (event_key)" in sql


def test_schema_registers_tables_jsonb_evidence_and_unique_natural_keys() -> None:
    storage_module, _, engine = _setup()
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert {
        "live_readiness_checks",
        "live_risk_decisions",
        "live_order_intents",
        "live_order_events",
        "live_reconciliation_runs",
    } <= names

    for table in (
        storage_module.live_readiness_checks,
        storage_module.live_risk_decisions,
        storage_module.live_order_intents,
        storage_module.live_order_events,
        storage_module.live_reconciliation_runs,
    ):
        assert isinstance(table.c.evidence.type, JSONB)
        assert "semantic_sha256" in table.c

    intent_uniques = {
        tuple(constraint.columns.keys())
        for constraint in storage_module.live_order_intents.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    event_uniques = {
        tuple(constraint.columns.keys())
        for constraint in storage_module.live_order_events.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("prediction_id", "policy_version") in intent_uniques
    assert ("event_key",) in event_uniques


def test_readiness_check_store_is_idempotent_and_conflicting_check_id_fails_closed() -> None:
    _, module, engine = _setup()
    repo = module.LiveReadinessRepository()
    kwargs = {
        "candidate_git_sha": SHA_A,
        "observed_at": NOW,
        "eligible": False,
        "reasons": ("activation_manifest_missing",),
        "evidence": {"mode": "research", "live_trading_enabled": False},
    }
    with engine.begin() as connection:
        first = repo.store_readiness_check(connection, **kwargs)
        second = repo.store_readiness_check(connection, **kwargs)
        assert first.created is True
        assert second.created is False
        assert first.record == second.record
        assert first.record["created_at"].tzinfo is not None
        assert len(first.record["semantic_sha256"]) == 64

        with pytest.raises(module.LiveReadinessConflict):
            repo.store_readiness_check(
                connection,
                **kwargs,
                check_id=first.record["check_id"],
                evidence_override={"mode": "live"},
            )


def test_risk_decision_store_is_idempotent_and_preserves_source_evidence() -> None:
    _, module, engine = _setup()
    repo = module.LiveReadinessRepository()
    decision = _eligible_decision()
    kwargs = {
        "prediction_id": "prediction-1",
        "prediction_semantic_sha256": SHA_A,
        "policy_version": "live-risk-v1",
        "decision": decision,
        "account_snapshot": {"total_exposure_usd": "0"},
        "evidence": {"requested_notional_usd": "5"},
        "created_at": NOW,
    }
    with engine.begin() as connection:
        first = repo.store_risk_decision(connection, **kwargs)
        second = repo.store_risk_decision(connection, **kwargs)
        assert first.created is True
        assert second.created is False
        assert first.record["prediction_id"] == "prediction-1"
        assert first.record["policy_sha256"] == decision.policy_sha256
        assert first.record["evidence"]["requested_notional_usd"] == "5"


def test_order_intent_natural_key_is_idempotent_and_semantic_drift_conflicts() -> None:
    _, module, engine = _setup()
    repo = module.LiveReadinessRepository()
    kwargs = {
        "prediction_id": "prediction-1",
        "policy_version": "live-risk-v1",
        "request_id": "request-1",
        "risk_decision_id": "risk-1",
        "token_id": "token-up",
        "side": "BUY",
        "size": Decimal("4.25"),
        "limit_price": Decimal("0.47"),
        "pre_submit_at": NOW,
        "evidence": {"source": "immutable_prediction"},
    }
    with engine.begin() as connection:
        first = repo.store_order_intent(connection, **kwargs)
        second = repo.store_order_intent(connection, **kwargs)
        assert first.created is True
        assert second.created is False
        assert first.record["intent_id"] == second.record["intent_id"]
        assert first.record["size"] == Decimal("4.25")

        with pytest.raises(module.LiveOrderIntentConflict):
            repo.store_order_intent(connection, **{**kwargs, "size": Decimal("4.50")})


def test_order_event_event_key_is_idempotent_and_conflicting_event_fails_closed() -> None:
    _, module, engine = _setup()
    repo = module.LiveReadinessRepository()
    with engine.begin() as connection:
        intent = repo.store_order_intent(
            connection,
            prediction_id="prediction-1",
            policy_version="live-risk-v1",
            request_id="request-1",
            risk_decision_id="risk-1",
            token_id="token-up",
            side="BUY",
            size=Decimal("4.25"),
            limit_price=Decimal("0.47"),
            pre_submit_at=NOW,
            evidence={},
        ).record
        kwargs = {
            "event_key": "external-order-1:accepted",
            "intent_id": intent["intent_id"],
            "event_type": "accepted",
            "observed_at": NOW,
            "external_order_id": "external-order-1",
            "external_trade_id": None,
            "evidence": {"status": "accepted"},
        }
        first = repo.store_order_event(connection, **kwargs)
        second = repo.store_order_event(connection, **kwargs)
        assert first.created is True
        assert second.created is False
        assert first.record == second.record

        with pytest.raises(module.LiveOrderEventConflict):
            repo.store_order_event(
                connection,
                **{**kwargs, "event_type": "filled", "evidence": {"status": "filled"}},
            )


def test_reconciliation_store_is_idempotent_and_records_critical_count() -> None:
    _, module, engine = _setup()
    repo = module.LiveReadinessRepository()
    kwargs = {
        "observed_at": NOW,
        "unresolved_count": 2,
        "critical_count": 1,
        "evidence": {"checks": ["intent_without_result"]},
    }
    with engine.begin() as connection:
        first = repo.store_reconciliation_run(connection, **kwargs)
        second = repo.store_reconciliation_run(connection, **kwargs)
        assert first.created is True
        assert second.created is False
        assert first.record["critical_count"] == 1
        assert first.record["created_at"].tzinfo is not None


def test_repository_exposes_no_update_or_delete_mutators() -> None:
    _, module, _ = _setup()
    forbidden = {
        name
        for name in dir(module.LiveReadinessRepository)
        if name.startswith("update") or name.startswith("delete")
    }
    assert forbidden == set()
