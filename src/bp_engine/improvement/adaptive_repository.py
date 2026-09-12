from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, insert, select

from bp_engine.improvement.adaptive import AdaptiveLearningCycle
from bp_engine.improvement.hashing import canonical_payload
from bp_engine.improvement.repository import ImprovementStoreResult
from bp_engine.storage.improvement_schema import adaptive_learning_cycles


class AdaptiveLearningCycleConflict(ValueError):
    pass


class AdaptiveLearningCycleRepository:
    def get(self, connection: Connection, cycle_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            select(adaptive_learning_cycles).where(
                adaptive_learning_cycles.c.cycle_id == cycle_id
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def latest_completed(
        self,
        connection: Connection,
        *,
        horizon_seconds: int,
        feature_version: str,
        label_version: str,
    ) -> dict[str, Any] | None:
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        feature = feature_version.strip()
        label = label_version.strip()
        if not feature:
            raise ValueError("feature_version must be nonblank")
        if not label:
            raise ValueError("label_version must be nonblank")
        row = connection.execute(
            select(adaptive_learning_cycles)
            .where(
                adaptive_learning_cycles.c.horizon_seconds == horizon_seconds,
                adaptive_learning_cycles.c.feature_version == feature,
                adaptive_learning_cycles.c.label_version == label,
            )
            .order_by(
                adaptive_learning_cycles.c.cutoff_at.desc(),
                adaptive_learning_cycles.c.created_at.desc(),
                adaptive_learning_cycles.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    def store(
        self,
        connection: Connection,
        cycle: AdaptiveLearningCycle,
    ) -> ImprovementStoreResult:
        rebuilt = AdaptiveLearningCycle.build(
            horizon_seconds=cycle.horizon_seconds,
            feature_version=cycle.feature_version,
            label_version=cycle.label_version,
            trigger_count=cycle.trigger_count,
            since_at=cycle.since_at,
            cutoff_at=cycle.cutoff_at,
            eligible_resolved_market_count=cycle.eligible_resolved_market_count,
            readiness_semantic_sha256=cycle.readiness_semantic_sha256,
            training_start_at=cycle.training_start_at,
            training_run_id=cycle.training_run_id,
            training_semantic_sha256=cycle.training_semantic_sha256,
            summary=cycle.summary,
            created_at=cycle.created_at,
        )
        if (
            rebuilt.cycle_id != cycle.cycle_id
            or rebuilt.semantic_sha256 != cycle.semantic_sha256
            or rebuilt.cycle_version != cycle.cycle_version
        ):
            raise AdaptiveLearningCycleConflict(
                f"adaptive learning cycle semantic conflict: {cycle.cycle_id}"
            )

        existing = self.get(connection, cycle.cycle_id)
        if existing is not None:
            if (
                existing["semantic_sha256"] != rebuilt.semantic_sha256
                or existing["summary"] != canonical_payload(rebuilt.summary)
            ):
                raise AdaptiveLearningCycleConflict(
                    f"adaptive learning cycle semantic conflict: {cycle.cycle_id}"
                )
            return ImprovementStoreResult(created=False, existing=True)

        connection.execute(
            insert(adaptive_learning_cycles).values(
                cycle_id=rebuilt.cycle_id,
                cycle_version=rebuilt.cycle_version,
                horizon_seconds=rebuilt.horizon_seconds,
                feature_version=rebuilt.feature_version,
                label_version=rebuilt.label_version,
                trigger_count=rebuilt.trigger_count,
                since_at=rebuilt.since_at,
                cutoff_at=rebuilt.cutoff_at,
                eligible_resolved_market_count=rebuilt.eligible_resolved_market_count,
                readiness_semantic_sha256=rebuilt.readiness_semantic_sha256,
                training_start_at=rebuilt.training_start_at,
                training_run_id=rebuilt.training_run_id,
                training_semantic_sha256=rebuilt.training_semantic_sha256,
                summary=canonical_payload(rebuilt.summary),
                semantic_sha256=rebuilt.semantic_sha256,
                created_at=rebuilt.created_at,
            )
        )
        return ImprovementStoreResult(created=True, existing=False)
