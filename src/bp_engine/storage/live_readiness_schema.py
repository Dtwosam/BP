from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from bp_engine.storage.schema import metadata

_JSONB_DOCUMENT = JSONB().with_variant(JSON(), "sqlite")

live_readiness_checks = Table(
    "live_readiness_checks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("check_id", String(128), nullable=False),
    Column("candidate_git_sha", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("eligible", Boolean, nullable=False),
    Column("reasons", _JSONB_DOCUMENT, nullable=False),
    Column("evidence", _JSONB_DOCUMENT, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "length(candidate_git_sha) = 64",
        name="ck_live_readiness_candidate_sha",
    ),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_live_readiness_semantic_sha",
    ),
    UniqueConstraint("check_id", name="uq_live_readiness_check_id"),
)
Index("ix_live_readiness_observed_at", live_readiness_checks.c.observed_at)
Index("ix_live_readiness_eligible", live_readiness_checks.c.eligible)

live_risk_decisions = Table(
    "live_risk_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("decision_id", String(128), nullable=False),
    Column("prediction_id", String(128), nullable=False),
    Column("prediction_semantic_sha256", String(64), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("policy_sha256", String(64), nullable=False),
    Column("eligible", Boolean, nullable=False),
    Column("reasons", _JSONB_DOCUMENT, nullable=False),
    Column("rules", _JSONB_DOCUMENT, nullable=False),
    Column("account_snapshot", _JSONB_DOCUMENT, nullable=False),
    Column("evidence", _JSONB_DOCUMENT, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "length(prediction_semantic_sha256) = 64",
        name="ck_live_risk_prediction_sha",
    ),
    CheckConstraint("length(policy_sha256) = 64", name="ck_live_risk_policy_sha"),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_live_risk_semantic_sha",
    ),
    UniqueConstraint("decision_id", name="uq_live_risk_decision_id"),
)
Index("ix_live_risk_prediction_id", live_risk_decisions.c.prediction_id)
Index("ix_live_risk_created_at", live_risk_decisions.c.created_at)

live_order_intents = Table(
    "live_order_intents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("intent_id", String(128), nullable=False),
    Column("prediction_id", String(128), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("risk_decision_id", String(128), nullable=False),
    Column("token_id", Text, nullable=False),
    Column("side", String(8), nullable=False),
    Column("size", Numeric(38, 18), nullable=False),
    Column("limit_price", Numeric(38, 18), nullable=False),
    Column("pre_submit_at", DateTime(timezone=True), nullable=False),
    Column("evidence", _JSONB_DOCUMENT, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("side = 'BUY'", name="ck_live_intent_buy_only"),
    CheckConstraint("size > 0", name="ck_live_intent_positive_size"),
    CheckConstraint(
        "limit_price > 0 AND limit_price <= 1",
        name="ck_live_intent_limit_price",
    ),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_live_intent_semantic_sha",
    ),
    UniqueConstraint("intent_id", name="uq_live_intent_id"),
    UniqueConstraint(
        "prediction_id",
        "policy_version",
        name="uq_live_intent_prediction_policy",
    ),
)
Index("ix_live_intent_prediction_id", live_order_intents.c.prediction_id)
Index("ix_live_intent_pre_submit_at", live_order_intents.c.pre_submit_at)

live_order_events = Table(
    "live_order_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_key", String(192), nullable=False),
    Column("intent_id", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("external_order_id", String(256), nullable=True),
    Column("external_trade_id", String(256), nullable=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("evidence", _JSONB_DOCUMENT, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_live_event_semantic_sha",
    ),
    UniqueConstraint("event_key", name="uq_live_event_key"),
)
Index(
    "ix_live_event_intent_time",
    live_order_events.c.intent_id,
    live_order_events.c.observed_at,
)
Index("ix_live_event_external_order", live_order_events.c.external_order_id)

live_reconciliation_runs = Table(
    "live_reconciliation_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reconciliation_id", String(128), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("unresolved_count", Integer, nullable=False),
    Column("critical_count", Integer, nullable=False),
    Column("evidence", _JSONB_DOCUMENT, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "unresolved_count >= 0",
        name="ck_live_reconciliation_unresolved_nonnegative",
    ),
    CheckConstraint(
        "critical_count >= 0 AND critical_count <= unresolved_count",
        name="ck_live_reconciliation_critical_count",
    ),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_live_reconciliation_semantic_sha",
    ),
    UniqueConstraint("reconciliation_id", name="uq_live_reconciliation_id"),
)
Index("ix_live_reconciliation_observed_at", live_reconciliation_runs.c.observed_at)
Index("ix_live_reconciliation_critical", live_reconciliation_runs.c.critical_count)
