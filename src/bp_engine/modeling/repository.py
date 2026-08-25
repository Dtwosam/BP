from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select

from bp_engine.modeling.models import TrainingRunReport
from bp_engine.storage.schema import model_training_runs


class TrainingRunConflict(RuntimeError):
    """Raised when an immutable model-training run would be rewritten."""


@dataclass(frozen=True)
class TrainingRunStoreResult:
    created: bool
    existing: bool


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _semantic_report(report: TrainingRunReport) -> dict[str, Any]:
    payload = asdict(report)
    payload.pop("created_at", None)
    return _jsonable(payload)


class ModelTrainingRunRepository:
    def get(
        self, connection: Connection, run_id: str
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(model_training_runs).where(model_training_runs.c.run_id == run_id)
        ).mappings().one_or_none()

    def store(
        self, connection: Connection, report: TrainingRunReport
    ) -> TrainingRunStoreResult:
        self._validate(report)
        existing = connection.execute(
            select(model_training_runs).where(
                model_training_runs.c.run_id == report.run_id
            )
        ).mappings().one_or_none()
        semantic_report = _semantic_report(report)
        if existing is not None:
            if (
                existing["semantic_sha256"] != report.semantic_sha256
                or existing["report"] != semantic_report
            ):
                raise TrainingRunConflict(
                    f"conflicting model-training run_id={report.run_id}"
                )
            return TrainingRunStoreResult(created=False, existing=True)

        connection.execute(
            insert(model_training_runs).values(
                run_id=report.run_id,
                dataset_version=report.dataset_version,
                split_version=report.split_version,
                feature_version=report.feature_version,
                label_version=report.label_version,
                horizon_seconds=report.horizon_seconds,
                requested_start=report.start,
                requested_end=report.end,
                dataset_sha256=report.dataset_sha256,
                split_sha256=report.split_sha256,
                predictor_names=list(report.predictor_names),
                dropped_all_missing=list(report.dropped_all_missing),
                model_configs=_jsonable(report.model_configs),
                validation_champion=report.validation_champion,
                report=semantic_report,
                artifact_manifest=_jsonable(report.artifacts),
                semantic_sha256=report.semantic_sha256,
                created_at=report.created_at,
            )
        )
        return TrainingRunStoreResult(created=True, existing=False)

    @staticmethod
    def _validate(report: TrainingRunReport) -> None:
        for name, value in (
            ("start", report.start),
            ("end", report.end),
            ("created_at", report.created_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if report.end <= report.start:
            raise ValueError("start must be before end")
        if report.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if not report.run_id:
            raise ValueError("run_id must not be empty")
        for name, digest in (
            ("dataset_sha256", report.dataset_sha256),
            ("split_sha256", report.split_sha256),
            ("semantic_sha256", report.semantic_sha256),
        ):
            if len(digest) != 64:
                raise ValueError(f"{name} must be a 64-character SHA-256 digest")
