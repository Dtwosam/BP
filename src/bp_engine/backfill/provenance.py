from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, insert, select, update

from bp_engine.storage.historical import StoreResult
from bp_engine.storage.schema import historical_backfill_artifacts, historical_backfill_runs


@dataclass(frozen=True)
class BackfillStats:
    rows_inserted: int = 0
    rows_existing: int = 0
    chunks_fetched: int = 0

    def __add__(self, other: BackfillStats) -> BackfillStats:
        return BackfillStats(
            rows_inserted=self.rows_inserted + other.rows_inserted,
            rows_existing=self.rows_existing + other.rows_existing,
            chunks_fetched=self.chunks_fetched + other.chunks_fetched,
        )


@dataclass(frozen=True)
class BackfillRun:
    run_id: str
    dataset: str
    source: str
    requested_start: datetime
    requested_end: datetime
    parameters: Mapping[str, Any]
    started_at: datetime


@dataclass(frozen=True)
class BackfillArtifact:
    run_id: str
    artifact_key: str
    source: str
    dataset: str
    request_params: Mapping[str, Any]
    downloaded_at: datetime
    response_sha256: str
    row_count: int


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cannot hash naive datetime")
        return value.isoformat()
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")


def canonical_json_sha256(payload: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def artifact_key(source: str, dataset: str, request_params: Mapping[str, Any]) -> str:
    return canonical_json_sha256(
        {
            "source": source,
            "dataset": dataset,
            "request_params": dict(request_params),
        }
    )


class ProvenanceRepository:
    def start_run(self, connection: Connection, run: BackfillRun) -> None:
        self._require_aware(run.started_at, "started_at")
        self._require_aware(run.requested_start, "requested_start")
        self._require_aware(run.requested_end, "requested_end")
        if run.requested_start >= run.requested_end:
            raise ValueError("requested_start must be before requested_end")

        connection.execute(
            insert(historical_backfill_runs).values(
                run_id=run.run_id,
                dataset=run.dataset,
                source=run.source,
                requested_start=run.requested_start,
                requested_end=run.requested_end,
                parameters=dict(run.parameters),
                started_at=run.started_at,
                completed_at=None,
                status="running",
                rows_inserted=0,
                rows_existing=0,
                chunks_fetched=0,
                error=None,
            )
        )

    def record_artifact(
        self,
        connection: Connection,
        artifact: BackfillArtifact,
    ) -> StoreResult:
        self._require_aware(artifact.downloaded_at, "downloaded_at")
        if artifact.row_count < 0:
            raise ValueError("row_count must be non-negative")

        existing = connection.execute(
            select(historical_backfill_artifacts).where(
                historical_backfill_artifacts.c.run_id == artifact.run_id,
                historical_backfill_artifacts.c.artifact_key == artifact.artifact_key,
                historical_backfill_artifacts.c.response_sha256 == artifact.response_sha256,
            )
        ).mappings().one_or_none()
        if existing is not None:
            return StoreResult(created=False)

        connection.execute(
            insert(historical_backfill_artifacts).values(
                run_id=artifact.run_id,
                artifact_key=artifact.artifact_key,
                source=artifact.source,
                dataset=artifact.dataset,
                request_params=dict(artifact.request_params),
                downloaded_at=artifact.downloaded_at,
                response_sha256=artifact.response_sha256,
                row_count=artifact.row_count,
            )
        )
        return StoreResult(created=True)

    def finish_run(
        self,
        connection: Connection,
        run_id: str,
        completed_at: datetime,
        stats: BackfillStats,
    ) -> None:
        self._require_aware(completed_at, "completed_at")
        result = connection.execute(
            update(historical_backfill_runs)
            .where(historical_backfill_runs.c.run_id == run_id)
            .values(
                completed_at=completed_at,
                status="success",
                rows_inserted=stats.rows_inserted,
                rows_existing=stats.rows_existing,
                chunks_fetched=stats.chunks_fetched,
                error=None,
            )
        )
        if result.rowcount != 1:
            raise KeyError(f"unknown historical backfill run: {run_id}")

    def mark_unavailable(
        self,
        connection: Connection,
        run_id: str,
        completed_at: datetime,
        reason: str,
    ) -> None:
        self._require_aware(completed_at, "completed_at")
        result = connection.execute(
            update(historical_backfill_runs)
            .where(historical_backfill_runs.c.run_id == run_id)
            .values(
                completed_at=completed_at,
                status="unavailable",
                rows_inserted=0,
                rows_existing=0,
                chunks_fetched=0,
                error=reason,
            )
        )
        if result.rowcount != 1:
            raise KeyError(f"unknown historical backfill run: {run_id}")

    def fail_run(
        self,
        connection: Connection,
        run_id: str,
        completed_at: datetime,
        error: str,
        stats: BackfillStats | None = None,
    ) -> None:
        self._require_aware(completed_at, "completed_at")
        if stats is None:
            stats = BackfillStats()
        result = connection.execute(
            update(historical_backfill_runs)
            .where(historical_backfill_runs.c.run_id == run_id)
            .values(
                completed_at=completed_at,
                status="failed",
                rows_inserted=stats.rows_inserted,
                rows_existing=stats.rows_existing,
                chunks_fetched=stats.chunks_fetched,
                error=error,
            )
        )
        if result.rowcount != 1:
            raise KeyError(f"unknown historical backfill run: {run_id}")

    @staticmethod
    def _require_aware(value: datetime, name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
