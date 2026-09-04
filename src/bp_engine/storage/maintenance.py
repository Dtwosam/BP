from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    drop_raw_partition,
    list_raw_partitions,
    raw_storage_mode,
)
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import (
    market_state_1s,
    raw_market_events,
    storage_maintenance_runs,
)

GIB = 1024**3
REQUIRED_COMPACT_FEEDS = (
    ("bybit", "spot"),
    ("bybit", "linear"),
    ("coinbase", "spot"),
    ("polymarket", "market"),
)


class ArchiveVerificationError(RuntimeError):
    """Raised when an archive or its manifest cannot be verified exactly."""


@dataclass(frozen=True)
class ArchiveManifest:
    archive_name: str
    start_at: datetime
    end_at: datetime
    row_count: int
    compressed_bytes: int
    sha256: str


@dataclass(frozen=True)
class PartitionRetirementResult:
    partition_name: str
    archived_rows: int
    dedupe_rows_removed: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _archive_name(start_at: datetime, end_at: datetime) -> str:
    start = start_at.strftime("%Y%m%dT%H%M%SZ")
    end = end_at.strftime("%Y%m%dT%H%M%SZ")
    return f"raw-{start}-{end}.jsonl.gz"


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _serialize_row(row: dict[str, Any]) -> bytes:
    record = dict(row)
    record["source_timestamp"] = _iso_utc(record.get("source_timestamp"))
    record["received_at"] = _iso_utc(record["received_at"])
    return (
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def _manifest_payload(manifest: ArchiveManifest) -> dict[str, Any]:
    payload = asdict(manifest)
    payload["start_at"] = _iso_utc(manifest.start_at)
    payload["end_at"] = _iso_utc(manifest.end_at)
    return payload


def _manifest_from_payload(payload: dict[str, Any]) -> ArchiveManifest:
    try:
        start_at = datetime.fromisoformat(str(payload["start_at"]).replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(str(payload["end_at"]).replace("Z", "+00:00"))
        return ArchiveManifest(
            archive_name=str(payload["archive_name"]),
            start_at=_require_aware_utc(start_at, field="manifest start_at"),
            end_at=_require_aware_utc(end_at, field="manifest end_at"),
            row_count=int(payload["row_count"]),
            compressed_bytes=int(payload["compressed_bytes"]),
            sha256=str(payload["sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveVerificationError("archive manifest is invalid") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_row_count(path: Path) -> int:
    try:
        with gzip.open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except (OSError, EOFError) as exc:
        raise ArchiveVerificationError("archive gzip payload is unreadable") from exc


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def verify_archive(archive_path: Path | str, manifest_path: Path | str) -> ArchiveManifest:
    archive = Path(archive_path)
    manifest_file = Path(manifest_path)
    if not archive.is_file():
        raise ArchiveVerificationError("archive file is missing")
    if not manifest_file.is_file():
        raise ArchiveVerificationError("archive manifest is missing")

    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveVerificationError("archive manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ArchiveVerificationError("archive manifest is invalid")

    manifest = _manifest_from_payload(payload)
    if manifest.archive_name != archive.name:
        raise ArchiveVerificationError("archive name does not match manifest")
    if _sha256(archive) != manifest.sha256:
        raise ArchiveVerificationError("archive SHA-256 does not match manifest")
    if archive.stat().st_size != manifest.compressed_bytes:
        raise ArchiveVerificationError("archive compressed byte count does not match manifest")
    if _gzip_row_count(archive) != manifest.row_count:
        raise ArchiveVerificationError("archive row count does not match manifest")
    return manifest


def archive_interval(
    engine: Engine,
    archive_dir: Path | str,
    start: datetime,
    end: datetime,
) -> ArchiveManifest:
    start_at = _require_aware_utc(start, field="start")
    end_at = _require_aware_utc(end, field="end")
    if end_at <= start_at:
        raise ValueError("end must be after start")

    directory = Path(archive_dir)
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / _archive_name(start_at, end_at)
    manifest_path = directory / f"{archive_path.name}.manifest.json"

    if archive_path.exists() and manifest_path.exists():
        manifest = verify_archive(archive_path, manifest_path)
        if manifest.start_at != start_at or manifest.end_at != end_at:
            raise ArchiveVerificationError("existing archive interval does not match request")
        return manifest

    statement = (
        select(raw_market_events)
        .where(raw_market_events.c.received_at >= start_at)
        .where(raw_market_events.c.received_at < end_at)
        .order_by(raw_market_events.c.received_at, raw_market_events.c.id)
    )

    descriptor, temp_name = tempfile.mkstemp(prefix=f".{archive_path.name}.", dir=directory)
    os.close(descriptor)
    temp_archive = Path(temp_name)
    row_count = 0
    try:
        with engine.connect() as connection, temp_archive.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename="",
                fileobj=raw_handle,
                mode="wb",
                mtime=0,
            ) as compressed:
                for row in connection.execute(statement).mappings():
                    compressed.write(_serialize_row(dict(row)))
                    row_count += 1
            raw_handle.flush()
            os.fsync(raw_handle.fileno())

        if _gzip_row_count(temp_archive) != row_count:
            raise ArchiveVerificationError("archive row count verification failed")

        compressed_bytes = temp_archive.stat().st_size
        sha256 = _sha256(temp_archive)
        os.replace(temp_archive, archive_path)

        manifest = ArchiveManifest(
            archive_name=archive_path.name,
            start_at=start_at,
            end_at=end_at,
            row_count=row_count,
            compressed_bytes=compressed_bytes,
            sha256=sha256,
        )
        manifest_bytes = (
            json.dumps(
                _manifest_payload(manifest),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        _write_atomic(manifest_path, manifest_bytes)
        return verify_archive(archive_path, manifest_path)
    finally:
        temp_archive.unlink(missing_ok=True)


def delete_verified_interval(
    engine: Engine,
    archive_path: Path | str,
    manifest_path: Path | str,
    *,
    batch_size: int,
) -> int:
    manifest = verify_archive(archive_path, manifest_path)
    repository = RecorderRepository()
    deleted_total = 0

    while True:
        with engine.begin() as connection:
            deleted = repository.delete_raw_interval_batch(
                connection,
                start_at=manifest.start_at,
                end_at=manifest.end_at,
                batch_size=batch_size,
            )
        deleted_total += deleted
        if deleted < batch_size:
            return deleted_total


def _compact_feeds_advanced(
    engine: Engine,
    end_at: datetime,
    required_feeds: tuple[tuple[str, str], ...],
) -> bool:
    with engine.connect() as connection:
        for source, stream in required_feeds:
            latest = connection.execute(
                select(func.max(market_state_1s.c.last_event_at)).where(
                    market_state_1s.c.source == source,
                    market_state_1s.c.stream == stream,
                )
            ).scalar_one()
            if latest is None or _utc(latest) <= end_at:
                return False
    return True


def retire_verified_partition(
    engine: Engine,
    archive_path: Path | str,
    manifest_path: Path | str,
    *,
    batch_size: int = 50_000,
    required_feeds: tuple[tuple[str, str], ...] = REQUIRED_COMPACT_FEEDS,
) -> PartitionRetirementResult:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if engine.dialect.name != "postgresql":
        raise ValueError("partition retirement requires PostgreSQL")

    manifest = verify_archive(archive_path, manifest_path)
    if manifest.end_at != manifest.start_at + timedelta(hours=1):
        raise ArchiveVerificationError("partition archive must cover exactly one hour")
    if manifest.start_at.minute or manifest.start_at.second or manifest.start_at.microsecond:
        raise ArchiveVerificationError("partition archive must start on a UTC hour")

    if not _compact_feeds_advanced(engine, manifest.end_at, required_feeds):
        raise RuntimeError("compact state has not advanced beyond archived partition")

    with engine.begin() as connection:
        if raw_storage_mode(connection) is not RawStorageMode.PARTITIONED:
            raise RuntimeError("raw_market_events is not partitioned")
        live_rows = int(
            connection.execute(
                select(func.count(raw_market_events.c.id)).where(
                    raw_market_events.c.received_at >= manifest.start_at,
                    raw_market_events.c.received_at < manifest.end_at,
                )
            ).scalar_one()
        )
        if live_rows != manifest.row_count:
            raise ArchiveVerificationError(
                "verified archive row count does not match live partition"
            )
        partition_name = drop_raw_partition(
            connection,
            start_at=manifest.start_at,
            end_at=manifest.end_at,
        )
        if partition_name is None:
            raise RuntimeError("raw partition is missing")

    dedupe_rows_removed = 0
    while True:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT dedupe_key
                        FROM raw_event_dedupe
                        WHERE received_at >= :start_at
                          AND received_at < :end_at
                        ORDER BY received_at, dedupe_key
                        LIMIT :batch_size
                    )
                    DELETE FROM raw_event_dedupe AS target
                    USING doomed
                    WHERE target.dedupe_key = doomed.dedupe_key
                    """
                ),
                {
                    "start_at": manifest.start_at,
                    "end_at": manifest.end_at,
                    "batch_size": batch_size,
                },
            )
        deleted = int(result.rowcount or 0)
        dedupe_rows_removed += deleted
        if deleted < batch_size:
            break

    return PartitionRetirementResult(
        partition_name=partition_name,
        archived_rows=manifest.row_count,
        dedupe_rows_removed=dedupe_rows_removed,
    )


def _raw_interval_is_empty(engine: Engine, start_at: datetime, end_at: datetime) -> bool:
    with engine.connect() as connection:
        remaining = connection.execute(
            select(raw_market_events.c.id)
            .where(raw_market_events.c.received_at >= start_at)
            .where(raw_market_events.c.received_at < end_at)
            .limit(1)
        ).first()
    return remaining is None


def prune_expired_archives(
    engine: Engine,
    archive_dir: Path | str,
    *,
    now: datetime,
    retention_hours: int,
    required_feeds: tuple[tuple[str, str], ...] = REQUIRED_COMPACT_FEEDS,
) -> list[str]:
    if retention_hours < 0:
        raise ValueError("retention_hours must be zero or greater")
    now_at = _require_aware_utc(now, field="now")
    cutoff = now_at - timedelta(hours=retention_hours)
    directory = Path(archive_dir)
    if not directory.exists():
        return []

    removed: list[str] = []
    for manifest_path in sorted(directory.glob("*.jsonl.gz.manifest.json")):
        archive_path = directory / manifest_path.name.removesuffix(".manifest.json")
        try:
            manifest = verify_archive(archive_path, manifest_path)
        except ArchiveVerificationError:
            continue
        if manifest.end_at > cutoff:
            continue
        if not _raw_interval_is_empty(engine, manifest.start_at, manifest.end_at):
            continue
        if not _compact_feeds_advanced(engine, manifest.end_at, required_feeds):
            continue
        manifest_path.unlink()
        archive_path.unlink()
        removed.append(manifest.archive_name)
    return removed


def prune_expired_state(
    engine: Engine,
    *,
    now: datetime,
    retention_days: int,
    batch_size: int,
) -> int:
    if retention_days < 0:
        raise ValueError("retention_days must be zero or greater")
    now_at = _require_aware_utc(now, field="now")
    cutoff = now_at - timedelta(days=retention_days)
    repository = RecorderRepository()
    deleted_total = 0

    while True:
        with engine.begin() as connection:
            deleted = repository.delete_state_before_batch(
                connection,
                cutoff_at=cutoff,
                batch_size=batch_size,
            )
        deleted_total += deleted
        if deleted < batch_size:
            return deleted_total


def _disk_health_from_usage(
    path: Path | str,
    usage: object,
    *,
    warning_free_gib: int | float,
    critical_free_gib: int | float,
) -> dict[str, Any]:
    if warning_free_gib < 0 or critical_free_gib < 0:
        raise ValueError("disk thresholds must be non-negative")
    if warning_free_gib < critical_free_gib:
        raise ValueError("warning threshold must be greater than or equal to critical threshold")

    warning_free_bytes = int(warning_free_gib * GIB)
    critical_free_bytes = int(critical_free_gib * GIB)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    if free <= critical_free_bytes:
        status = "critical"
    elif free <= warning_free_bytes:
        status = "warning"
    else:
        status = "ok"

    return {
        "path": str(Path(path)),
        "status": status,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "free_percent": (free / total * 100.0) if total else 0.0,
        "warning_free_bytes": warning_free_bytes,
        "critical_free_bytes": critical_free_bytes,
    }


def disk_health(
    path: Path | str,
    warning_free_gib: int | float,
    critical_free_gib: int | float,
) -> dict[str, Any]:
    return _disk_health_from_usage(
        path,
        shutil.disk_usage(path),
        warning_free_gib=warning_free_gib,
        critical_free_gib=critical_free_gib,
    )


def project_raw_bytes_per_day(
    *,
    recent_event_count: int,
    recent_window_hours: float,
    average_bytes_per_event: int | float,
) -> int:
    if recent_event_count < 0 or average_bytes_per_event < 0:
        raise ValueError("event count and average bytes must be non-negative")
    if recent_window_hours <= 0:
        raise ValueError("recent_window_hours must be greater than zero")
    return int((recent_event_count / recent_window_hours) * 24 * average_bytes_per_event)


def _floor_hour(value: datetime) -> datetime:
    return _require_aware_utc(value, field="timestamp").replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _latest_successful_maintenance(engine: Engine) -> datetime | None:
    try:
        with engine.connect() as connection:
            value = connection.execute(
                select(func.max(storage_maintenance_runs.c.completed_at)).where(
                    storage_maintenance_runs.c.status == "success"
                )
            ).scalar_one()
    except SQLAlchemyError:
        return None
    if value is None:
        return None
    return _utc(value)


def _partition_relation_bytes(engine: Engine) -> int | None:
    if engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT COALESCE(sum(pg_total_relation_size(child.oid)), 0)
                    FROM pg_inherits
                    JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    WHERE parent_ns.nspname = current_schema()
                      AND parent.relname = 'raw_market_events'
                    """
                )
            ).scalar_one()
    except SQLAlchemyError:
        return None
    return int(value or 0)


def _dedupe_relation_bytes(engine: Engine) -> int | None:
    if engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT COALESCE(sum(pg_total_relation_size(child.oid)), 0)
                    FROM pg_inherits
                    JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    WHERE parent_ns.nspname = current_schema()
                      AND parent.relname = 'raw_event_dedupe'
                    """
                )
            ).scalar_one()
    except SQLAlchemyError:
        return None
    return int(value or 0)


def build_composite_storage_health(
    engine: Engine,
    path: Path | str,
    settings: object,
    *,
    now: datetime | None = None,
    disk_usage_fn: Callable[[Path | str], object] = shutil.disk_usage,
) -> dict[str, Any]:
    now_at = datetime.now(UTC) if now is None else _require_aware_utc(now, field="now")
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    disk = _disk_health_from_usage(
        target,
        disk_usage_fn(target),
        warning_free_gib=settings.storage_warning_free_gib,
        critical_free_gib=settings.storage_critical_free_gib,
    )

    mode = RawStorageMode.LEGACY
    if engine.dialect.name == "postgresql":
        try:
            with engine.connect() as connection:
                mode = raw_storage_mode(connection)
        except SQLAlchemyError:
            mode = RawStorageMode.LEGACY

    if mode is not RawStorageMode.PARTITIONED:
        return {
            **disk,
            "storage_mode": mode.value,
            "guards": {
                "maintenance_fresh": True,
                "current_partition_present": True,
                "retention_current": True,
            },
            "maintenance": {
                "last_success_at": None,
                "age_hours": None,
                "max_age_hours": int(
                    getattr(settings, "storage_maintenance_max_age_hours", 2)
                ),
            },
            "raw_partitions": {
                "count": 0,
                "oldest_start_at": None,
                "newest_end_at": None,
                "oldest_age_hours": None,
                "retention_lag_hours": 0.0,
                "bytes": None,
                "dedupe_bytes": None,
            },
        }

    partitions = list_raw_partitions(engine)
    current_hour = _floor_hour(now_at)
    current_present = any(
        partition.start_at <= now_at < partition.end_at
        for partition in partitions
    )
    eligible_end = _floor_hour(
        now_at - timedelta(hours=int(settings.storage_hot_raw_hours))
    )
    expired = [
        partition
        for partition in partitions
        if partition.end_at <= eligible_end
    ]
    retention_lag_hours = 0.0
    if expired:
        retention_lag_hours = max(
            0.0,
            (eligible_end - min(item.start_at for item in expired)).total_seconds()
            / 3600.0,
        )

    latest_success = _latest_successful_maintenance(engine)
    age_hours = (
        None
        if latest_success is None
        else max(0.0, (now_at - latest_success).total_seconds() / 3600.0)
    )
    max_age_hours = int(getattr(settings, "storage_maintenance_max_age_hours", 2))
    maintenance_fresh = age_hours is not None and age_hours <= max_age_hours
    retention_current = retention_lag_hours <= 1.0

    guards = {
        "maintenance_fresh": maintenance_fresh,
        "current_partition_present": current_present,
        "retention_current": retention_current,
    }
    status = disk["status"]
    if not all(guards.values()):
        status = "critical"

    oldest = min(partitions, key=lambda item: item.start_at) if partitions else None
    newest = max(partitions, key=lambda item: item.end_at) if partitions else None
    oldest_age_hours = (
        None
        if oldest is None
        else max(0.0, (now_at - oldest.start_at).total_seconds() / 3600.0)
    )

    return {
        **disk,
        "status": status,
        "storage_mode": mode.value,
        "guards": guards,
        "maintenance": {
            "last_success_at": _iso_utc(latest_success),
            "age_hours": age_hours,
            "max_age_hours": max_age_hours,
        },
        "raw_partitions": {
            "count": len(partitions),
            "current_hour_start_at": _iso_utc(current_hour),
            "oldest_start_at": _iso_utc(oldest.start_at) if oldest else None,
            "newest_end_at": _iso_utc(newest.end_at) if newest else None,
            "oldest_age_hours": oldest_age_hours,
            "retention_lag_hours": retention_lag_hours,
            "bytes": _partition_relation_bytes(engine),
            "dedupe_bytes": _dedupe_relation_bytes(engine),
        },
    }


def _postgres_raw_total_bytes(engine: Engine) -> int | None:
    if engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as connection:
            mode = raw_storage_mode(connection)
            if mode is RawStorageMode.PARTITIONED:
                return _partition_relation_bytes(engine)
            value = connection.execute(
                text("SELECT pg_total_relation_size('raw_market_events')")
            ).scalar_one()
    except SQLAlchemyError:
        return None
    return int(value) if value is not None else None


def build_storage_report(
    engine: Engine,
    archive_dir: Path | str,
    settings: object,
    *,
    disk_usage_fn: Callable[[Path | str], object] = shutil.disk_usage,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_at = datetime.now(UTC) if now is None else _require_aware_utc(now, field="now")
    archive_path = Path(archive_dir)
    archive_path.mkdir(parents=True, exist_ok=True)

    with engine.connect() as connection:
        raw_count, first_received_at, last_received_at = connection.execute(
            select(
                func.count(raw_market_events.c.id),
                func.min(raw_market_events.c.received_at),
                func.max(raw_market_events.c.received_at),
            )
        ).one()
        recent_24h_count = connection.execute(
            select(func.count(raw_market_events.c.id)).where(
                raw_market_events.c.received_at >= now_at - timedelta(hours=24),
                raw_market_events.c.received_at <= now_at,
            )
        ).scalar_one()
        state_count, first_bucket_at, last_bucket_at = connection.execute(
            select(
                func.count(market_state_1s.c.id),
                func.min(market_state_1s.c.bucket_at),
                func.max(market_state_1s.c.bucket_at),
            )
        ).one()

    total_bytes = _postgres_raw_total_bytes(engine)
    average_bytes_per_event: float | None = None
    projected_bytes_per_day: int | None = None
    if total_bytes is not None and raw_count:
        average_bytes_per_event = total_bytes / int(raw_count)
        projected_bytes_per_day = project_raw_bytes_per_day(
            recent_event_count=int(recent_24h_count),
            recent_window_hours=24,
            average_bytes_per_event=average_bytes_per_event,
        )

    archive_files = list(archive_path.glob("*.jsonl.gz"))
    archive_bytes = sum(path.stat().st_size for path in archive_files)
    health_path = Path(getattr(settings, "storage_health_path", None) or archive_path)
    composite = build_composite_storage_health(
        engine,
        health_path,
        settings,
        now=now_at,
        disk_usage_fn=disk_usage_fn,
    )
    disk = {
        key: composite[key]
        for key in (
            "path",
            "status",
            "total_bytes",
            "used_bytes",
            "free_bytes",
            "free_percent",
            "warning_free_bytes",
            "critical_free_bytes",
        )
    }
    projected_hours_to_critical: float | None = None
    if projected_bytes_per_day and projected_bytes_per_day > 0:
        hourly_growth = projected_bytes_per_day / 24.0
        projected_hours_to_critical = max(
            0.0,
            (disk["free_bytes"] - disk["critical_free_bytes"]) / hourly_growth,
        )

    return {
        "generated_at": _iso_utc(now_at),
        "raw": {
            "count": int(raw_count),
            "first_received_at": _iso_utc(first_received_at),
            "last_received_at": _iso_utc(last_received_at),
            "recent_24h_count": int(recent_24h_count),
            "total_bytes": total_bytes,
            "average_bytes_per_event": average_bytes_per_event,
            "projected_bytes_per_day": projected_bytes_per_day,
        },
        "state": {
            "count": int(state_count),
            "first_bucket_at": _iso_utc(first_bucket_at),
            "last_bucket_at": _iso_utc(last_bucket_at),
        },
        "archives": {
            "count": len(archive_files),
            "bytes": archive_bytes,
        },
        "disk": disk,
        "storage_mode": composite["storage_mode"],
        "maintenance": composite["maintenance"],
        "raw_partitions": {
            **composite["raw_partitions"],
            "projected_hours_to_critical": projected_hours_to_critical,
        },
        "retention": {
            "hot_raw_hours": settings.storage_hot_raw_hours,
            "archive_retention_hours": settings.storage_archive_retention_hours,
            "state_retention_days": settings.storage_state_retention_days,
        },
    }
