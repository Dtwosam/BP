from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select

from bp_engine.storage.schema import backtest_runs


class BacktestRunConflict(RuntimeError):
    """Raised when an immutable backtest run would be rewritten."""


@dataclass(frozen=True)
class BacktestStoreResult:
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


def _semantic_report(report: Any) -> dict[str, Any]:
    payload = asdict(report)
    payload.pop("created_at", None)
    return _jsonable(payload)


class BacktestRunRepository:
    def get(
        self, connection: Connection, run_id: str
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(backtest_runs).where(backtest_runs.c.run_id == run_id)
        ).mappings().one_or_none()

    def store(self, connection: Connection, report: Any) -> BacktestStoreResult:
        self._validate(report)
        existing = self.get(connection, report.run_id)
        semantic_report = _semantic_report(report)
        if existing is not None:
            if (
                existing["semantic_sha256"] != report.semantic_sha256
                or existing["report"] != semantic_report
            ):
                raise BacktestRunConflict(
                    f"conflicting backtest run_id={report.run_id}"
                )
            return BacktestStoreResult(created=False, existing=True)

        connection.execute(
            insert(backtest_runs).values(
                run_id=report.run_id,
                backtest_version=report.backtest_version,
                source_training_run_id=report.source_training_run_id,
                source_training_semantic_sha256=(
                    report.source_training_semantic_sha256
                ),
                dataset_version=report.dataset_version,
                feature_version=report.feature_version,
                label_version=report.label_version,
                horizon_seconds=report.horizon_seconds,
                requested_start=report.start,
                requested_end=report.end,
                dataset_sha256=report.dataset_sha256,
                config=_jsonable(report.config),
                config_sha256=report.config_sha256,
                plan_sha256=report.plan_sha256,
                fold_membership_sha256=list(report.fold_membership_sha256),
                report=semantic_report,
                semantic_sha256=report.semantic_sha256,
                created_at=report.created_at,
            )
        )
        return BacktestStoreResult(created=True, existing=False)

    @staticmethod
    def _validate(report: Any) -> None:
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

        digests = (
            ("source_training_semantic_sha256", report.source_training_semantic_sha256),
            ("dataset_sha256", report.dataset_sha256),
            ("config_sha256", report.config_sha256),
            ("plan_sha256", report.plan_sha256),
            ("semantic_sha256", report.semantic_sha256),
        )
        for name, digest in digests:
            if len(digest) != 64:
                raise ValueError(f"{name} must be a 64-character SHA-256 digest")
        for digest in report.fold_membership_sha256:
            if len(digest) != 64:
                raise ValueError(
                    "fold_membership_sha256 entries must be 64-character SHA-256 digests"
                )
