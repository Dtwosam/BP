from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from bp_engine.storage.schema import metadata

improvement_experiments = Table(
    "improvement_experiments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("experiment_id", String(128), nullable=False),
    Column("experiment_version", String(64), nullable=False),
    Column("horizon_seconds", Integer, nullable=False),
    Column("change_family", String(32), nullable=False),
    Column("champion_calibration_run_id", String(128), nullable=False),
    Column("champion_calibration_semantic_sha256", String(64), nullable=False),
    Column("hypothesis", Text, nullable=False),
    Column("spec", JSON, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "horizon_seconds > 0",
        name="ck_improvement_experiments_positive_horizon",
    ),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_improvement_experiments_semantic_sha256",
    ),
    CheckConstraint(
        "length(champion_calibration_semantic_sha256) = 64",
        name="ck_improvement_experiments_champion_sha256",
    ),
    UniqueConstraint(
        "experiment_id",
        name="uq_improvement_experiments_experiment_id",
    ),
)

Index(
    "ix_improvement_experiments_horizon_created",
    improvement_experiments.c.horizon_seconds,
    improvement_experiments.c.created_at,
)

improvement_evaluations = Table(
    "improvement_evaluations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("evaluation_id", String(128), nullable=False),
    Column("evaluation_version", String(64), nullable=False),
    Column("experiment_id", String(128), nullable=False),
    Column("challenger_id", String(128), nullable=False),
    Column("challenger_semantic_sha256", String(64), nullable=False),
    Column("evidence_manifest", JSON, nullable=False),
    Column("champion_metrics", JSON, nullable=False),
    Column("challenger_metrics", JSON, nullable=False),
    Column("comparison", JSON, nullable=False),
    Column("promotion_eligible", Boolean, nullable=False),
    Column("ineligibility_reasons", JSON, nullable=False),
    Column("report", JSON, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_improvement_evaluations_semantic_sha256",
    ),
    CheckConstraint(
        "length(challenger_semantic_sha256) = 64",
        name="ck_improvement_evaluations_challenger_sha256",
    ),
    UniqueConstraint(
        "evaluation_id",
        name="uq_improvement_evaluations_evaluation_id",
    ),
)

Index(
    "ix_improvement_evaluations_experiment_created",
    improvement_evaluations.c.experiment_id,
    improvement_evaluations.c.created_at,
)
Index(
    "ix_improvement_evaluations_promotion_created",
    improvement_evaluations.c.promotion_eligible,
    improvement_evaluations.c.created_at,
)

improvement_promotion_decisions = Table(
    "improvement_promotion_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("decision_id", String(128), nullable=False),
    Column("decision_version", String(64), nullable=False),
    Column("evaluation_id", String(128), nullable=False),
    Column("experiment_id", String(128), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("resulting_champion", JSON, nullable=False),
    Column("decision_record", JSON, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_improvement_decisions_semantic_sha256",
    ),
    CheckConstraint(
        "decision IN ('reject_challenger', 'keep_champion', 'promote_challenger')",
        name="ck_improvement_decisions_decision",
    ),
    UniqueConstraint(
        "decision_id",
        name="uq_improvement_decisions_decision_id",
    ),
)

Index(
    "ix_improvement_decisions_evaluation_created",
    improvement_promotion_decisions.c.evaluation_id,
    improvement_promotion_decisions.c.created_at,
)
Index(
    "ix_improvement_decisions_experiment_created",
    improvement_promotion_decisions.c.experiment_id,
    improvement_promotion_decisions.c.created_at,
)

adaptive_learning_cycles = Table(
    "adaptive_learning_cycles",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cycle_id", String(128), nullable=False, unique=True),
    Column("cycle_version", String(64), nullable=False),
    Column("horizon_seconds", Integer, nullable=False),
    Column("feature_version", String(128), nullable=False),
    Column("label_version", String(128), nullable=False),
    Column("trigger_count", Integer, nullable=False),
    Column("since_at", DateTime(timezone=True), nullable=False),
    Column("cutoff_at", DateTime(timezone=True), nullable=False),
    Column("eligible_resolved_market_count", Integer, nullable=False),
    Column("readiness_semantic_sha256", String(64), nullable=False),
    Column("training_start_at", DateTime(timezone=True), nullable=False),
    Column("training_run_id", String(128), nullable=False),
    Column("training_semantic_sha256", String(64), nullable=False),
    Column("summary", JSON, nullable=False),
    Column("semantic_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "horizon_seconds > 0",
        name="ck_adaptive_learning_cycles_positive_horizon",
    ),
    CheckConstraint(
        "trigger_count > 0",
        name="ck_adaptive_learning_cycles_positive_trigger",
    ),
    CheckConstraint(
        "eligible_resolved_market_count >= trigger_count",
        name="ck_adaptive_learning_cycles_trigger_satisfied",
    ),
    CheckConstraint(
        "length(readiness_semantic_sha256) = 64",
        name="ck_adaptive_learning_cycles_readiness_sha256",
    ),
    CheckConstraint(
        "length(training_semantic_sha256) = 64",
        name="ck_adaptive_learning_cycles_training_sha256",
    ),
    CheckConstraint(
        "length(semantic_sha256) = 64",
        name="ck_adaptive_learning_cycles_semantic_sha256",
    ),
)

Index(
    "ix_adaptive_learning_cycles_stream_cutoff",
    adaptive_learning_cycles.c.horizon_seconds,
    adaptive_learning_cycles.c.feature_version,
    adaptive_learning_cycles.c.label_version,
    adaptive_learning_cycles.c.cutoff_at,
)
