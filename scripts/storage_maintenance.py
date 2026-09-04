from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.storage.maintenance import (
    archive_interval,
    build_composite_storage_health,
    build_storage_report,
    delete_verified_interval,
    disk_health,
    prune_expired_archives,
    prune_expired_state,
    retire_verified_partition,
)
from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    ensure_partitioned_raw_storage,
    list_raw_partitions,
    raw_storage_mode,
)
from bp_engine.storage.schema import (
    metadata,
    raw_market_events,
    storage_maintenance_runs,
)


def _add_storage_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--archive-dir", default=None)
    parser.add_argument("--warning-free-gib", type=float, default=None)
    parser.add_argument("--critical-free-gib", type=float, default=None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage bounded BP recorder storage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run one fail-closed storage maintenance cycle")
    _add_storage_options(run)
    run.add_argument("--hot-raw-hours", type=int, default=None)
    run.add_argument("--archive-retention-hours", type=int, default=None)
    run.add_argument("--state-retention-days", type=int, default=None)
    run.add_argument("--delete-batch-size", type=int, default=None)

    health = subparsers.add_parser("disk-health", help="Report free-space status")
    health.add_argument("--env-file", default=None)
    health.add_argument("--path", default=None)
    health.add_argument("--warning-free-gib", type=float, default=None)
    health.add_argument("--critical-free-gib", type=float, default=None)

    report = subparsers.add_parser("report", help="Emit recorder storage evidence")
    _add_storage_options(report)
    return parser.parse_args()


def _settings(args: argparse.Namespace) -> Settings:
    env_file = getattr(args, "env_file", None)
    settings = Settings(_env_file=env_file) if env_file is not None else Settings()
    updates: dict[str, object] = {}
    if getattr(args, "database_url", None) is not None:
        updates["database_url"] = args.database_url
    if getattr(args, "archive_dir", None) is not None:
        updates["storage_archive_dir"] = args.archive_dir
    if getattr(args, "warning_free_gib", None) is not None:
        updates["storage_warning_free_gib"] = args.warning_free_gib
    if getattr(args, "critical_free_gib", None) is not None:
        updates["storage_critical_free_gib"] = args.critical_free_gib
    if getattr(args, "hot_raw_hours", None) is not None:
        updates["storage_hot_raw_hours"] = args.hot_raw_hours
    if getattr(args, "archive_retention_hours", None) is not None:
        updates["storage_archive_retention_hours"] = args.archive_retention_hours
    if getattr(args, "state_retention_days", None) is not None:
        updates["storage_state_retention_days"] = args.state_retention_days
    if getattr(args, "delete_batch_size", None) is not None:
        updates["storage_delete_batch_size"] = args.delete_batch_size
    return settings.model_copy(update=updates)


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _floor_hour(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _disk_health_command(args: argparse.Namespace) -> int:
    settings = _settings(args)
    path = Path(
        args.path
        or settings.storage_health_path
        or settings.storage_archive_dir
    )
    path.mkdir(parents=True, exist_ok=True)
    report = disk_health(
        path,
        warning_free_gib=settings.storage_warning_free_gib,
        critical_free_gib=settings.storage_critical_free_gib,
    )
    if args.path is None:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                mode = raw_storage_mode(connection)
            if mode is RawStorageMode.PARTITIONED:
                report = build_composite_storage_health(
                    engine,
                    path,
                    settings,
                )
        except Exception:
            pass
        finally:
            engine.dispose()
    _print(report)
    return 1 if report["status"] == "critical" else 0


def _report_command(args: argparse.Namespace) -> int:
    settings = _settings(args)
    archive_dir = Path(settings.storage_archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    report = build_storage_report(engine, archive_dir, settings)
    _print(report)
    return 1 if report["disk"]["status"] == "critical" else 0


def _record_maintenance_start(
    engine,
    *,
    started_at: datetime,
    storage_mode: RawStorageMode,
) -> int:
    storage_maintenance_runs.create(engine, checkfirst=True)
    with engine.begin() as connection:
        return int(
            connection.execute(
                storage_maintenance_runs.insert()
                .values(
                    started_at=started_at,
                    completed_at=None,
                    status="running",
                    storage_mode=storage_mode.value,
                    partitions_retired=0,
                    dedupe_rows_removed=0,
                    disk_status=None,
                    error=None,
                )
                .returning(storage_maintenance_runs.c.id)
            ).scalar_one()
        )


def _record_maintenance_finish(
    engine,
    *,
    run_id: int,
    completed_at: datetime,
    status: str,
    partitions_retired: int,
    dedupe_rows_removed: int,
    disk_status: str | None,
    error: str | None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            storage_maintenance_runs.update()
            .where(storage_maintenance_runs.c.id == run_id)
            .values(
                completed_at=completed_at,
                status=status,
                partitions_retired=partitions_retired,
                dedupe_rows_removed=dedupe_rows_removed,
                disk_status=disk_status,
                error=error,
            )
        )


def _run_command(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if settings.storage_hot_raw_hours < 0:
        raise SystemExit("hot raw retention must be non-negative")
    if settings.storage_archive_retention_hours < 0:
        raise SystemExit("archive retention must be non-negative")
    if settings.storage_state_retention_days < 0:
        raise SystemExit("state retention must be non-negative")
    if settings.storage_delete_batch_size <= 0:
        raise SystemExit("delete batch size must be greater than zero")

    now = datetime.now(UTC)
    archive_dir = Path(settings.storage_archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    health_path = Path(settings.storage_health_path or settings.storage_archive_dir)
    health_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    metadata.create_all(engine)

    with engine.connect() as connection:
        storage_mode = raw_storage_mode(connection)
    if storage_mode is RawStorageMode.PARTITIONED:
        ensure_partitioned_raw_storage(engine, now=now)

    run_id = _record_maintenance_start(
        engine,
        started_at=now,
        storage_mode=storage_mode,
    )
    partitions_retired = 0
    dedupe_rows_removed = 0

    try:
        removed_archives = prune_expired_archives(
            engine,
            archive_dir,
            now=now,
            retention_hours=(
                settings.storage_hot_raw_hours + settings.storage_archive_retention_hours
            ),
        )
        health = disk_health(
            health_path,
            warning_free_gib=settings.storage_warning_free_gib,
            critical_free_gib=settings.storage_critical_free_gib,
        )
        if health["status"] == "critical":
            _record_maintenance_finish(
                engine,
                run_id=run_id,
                completed_at=datetime.now(UTC),
                status="failure",
                partitions_retired=0,
                dedupe_rows_removed=0,
                disk_status=health["status"],
                error="critical disk reserve reached before retention work",
            )
            _print(
                {
                    "status": "critical",
                    "removed_archives": removed_archives,
                    "raw_intervals": [],
                    "state_rows_deleted": 0,
                    "disk": health,
                }
            )
            return 1

        raw_intervals: list[dict[str, object]] = []
        hot_cutoff = now - timedelta(hours=settings.storage_hot_raw_hours)
        eligible_end = _floor_hour(hot_cutoff)

        if storage_mode is RawStorageMode.PARTITIONED:
            for partition in list_raw_partitions(engine):
                if partition.end_at > eligible_end:
                    continue
                health = disk_health(
                    health_path,
                    warning_free_gib=settings.storage_warning_free_gib,
                    critical_free_gib=settings.storage_critical_free_gib,
                )
                if health["status"] == "critical":
                    break

                manifest = archive_interval(
                    engine,
                    archive_dir,
                    partition.start_at,
                    partition.end_at,
                )
                archive_path = archive_dir / manifest.archive_name
                manifest_path = archive_dir / f"{manifest.archive_name}.manifest.json"
                retired = retire_verified_partition(
                    engine,
                    archive_path,
                    manifest_path,
                    batch_size=settings.storage_delete_batch_size,
                )
                partitions_retired += 1
                dedupe_rows_removed += retired.dedupe_rows_removed
                raw_intervals.append(
                    {
                        "start_at": partition.start_at.isoformat().replace("+00:00", "Z"),
                        "end_at": partition.end_at.isoformat().replace("+00:00", "Z"),
                        "archive": manifest.archive_name,
                        "archived_rows": manifest.row_count,
                        "partition": retired.partition_name,
                        "dedupe_rows_removed": retired.dedupe_rows_removed,
                        "sha256": manifest.sha256,
                    }
                )
        else:
            with engine.connect() as connection:
                earliest = connection.execute(
                    select(func.min(raw_market_events.c.received_at))
                ).scalar_one()

            if earliest is not None:
                interval_start = _floor_hour(
                    earliest.replace(tzinfo=UTC) if earliest.tzinfo is None else earliest
                )
                while interval_start + timedelta(hours=1) <= eligible_end:
                    health = disk_health(
                        health_path,
                        warning_free_gib=settings.storage_warning_free_gib,
                        critical_free_gib=settings.storage_critical_free_gib,
                    )
                    if health["status"] == "critical":
                        break

                    interval_end = interval_start + timedelta(hours=1)
                    manifest = archive_interval(
                        engine,
                        archive_dir,
                        interval_start,
                        interval_end,
                    )
                    archive_path = archive_dir / manifest.archive_name
                    manifest_path = archive_dir / f"{manifest.archive_name}.manifest.json"
                    deleted = delete_verified_interval(
                        engine,
                        archive_path,
                        manifest_path,
                        batch_size=settings.storage_delete_batch_size,
                    )
                    raw_intervals.append(
                        {
                            "start_at": interval_start.isoformat().replace("+00:00", "Z"),
                            "end_at": interval_end.isoformat().replace("+00:00", "Z"),
                            "archive": manifest.archive_name,
                            "archived_rows": manifest.row_count,
                            "deleted_rows": deleted,
                            "sha256": manifest.sha256,
                        }
                    )
                    interval_start = interval_end

        state_rows_deleted = prune_expired_state(
            engine,
            now=now,
            retention_days=settings.storage_state_retention_days,
            batch_size=settings.storage_delete_batch_size,
        )
        final_disk = disk_health(
            health_path,
            warning_free_gib=settings.storage_warning_free_gib,
            critical_free_gib=settings.storage_critical_free_gib,
        )

        _record_maintenance_finish(
            engine,
            run_id=run_id,
            completed_at=datetime.now(UTC),
            status="success",
            partitions_retired=partitions_retired,
            dedupe_rows_removed=dedupe_rows_removed,
            disk_status=final_disk["status"],
            error=None,
        )

        if storage_mode is RawStorageMode.PARTITIONED:
            final_health = build_composite_storage_health(
                engine,
                health_path,
                settings,
            )
            if final_health["status"] == "critical":
                _record_maintenance_finish(
                    engine,
                    run_id=run_id,
                    completed_at=datetime.now(UTC),
                    status="failure",
                    partitions_retired=partitions_retired,
                    dedupe_rows_removed=dedupe_rows_removed,
                    disk_status=final_disk["status"],
                    error="composite storage health failed after retention cycle",
                )
                final_health = build_composite_storage_health(
                    engine,
                    health_path,
                    settings,
                )
        else:
            final_health = final_disk

        _print(
            {
                "status": final_health["status"],
                "storage_mode": storage_mode.value,
                "removed_archives": removed_archives,
                "raw_intervals": raw_intervals,
                "state_rows_deleted": state_rows_deleted,
                "partitions_retired": partitions_retired,
                "dedupe_rows_removed": dedupe_rows_removed,
                "disk": final_health,
            }
        )
        return 1 if final_health["status"] == "critical" else 0
    except Exception as exc:
        try:
            current_disk = disk_health(
                health_path,
                warning_free_gib=settings.storage_warning_free_gib,
                critical_free_gib=settings.storage_critical_free_gib,
            )
            disk_status = str(current_disk["status"])
        except Exception:
            disk_status = None
        _record_maintenance_finish(
            engine,
            run_id=run_id,
            completed_at=datetime.now(UTC),
            status="failure",
            partitions_retired=partitions_retired,
            dedupe_rows_removed=dedupe_rows_removed,
            disk_status=disk_status,
            error=str(exc)[:2000],
        )
        raise


def main() -> int:
    args = parse_args()
    if args.command == "disk-health":
        return _disk_health_command(args)
    if args.command == "report":
        return _report_command(args)
    if args.command == "run":
        return _run_command(args)
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
