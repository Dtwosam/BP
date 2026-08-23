from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select

from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import market_state_1s, raw_market_events

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
            with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as compressed:
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
