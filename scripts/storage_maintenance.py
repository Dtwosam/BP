from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.storage.maintenance import (
    archive_interval,
    build_storage_report,
    delete_verified_interval,
    disk_health,
    prune_expired_archives,
    prune_expired_state,
)
from bp_engine.storage.schema import metadata, raw_market_events


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
    path = Path(args.path or settings.storage_archive_dir)
    path.mkdir(parents=True, exist_ok=True)
    report = disk_health(
        path,
        warning_free_gib=settings.storage_warning_free_gib,
        critical_free_gib=settings.storage_critical_free_gib,
    )
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
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)

    removed_archives = prune_expired_archives(
        engine,
        archive_dir,
        now=now,
        retention_hours=(
            settings.storage_hot_raw_hours + settings.storage_archive_retention_hours
        ),
    )
    health = disk_health(
        archive_dir,
        warning_free_gib=settings.storage_warning_free_gib,
        critical_free_gib=settings.storage_critical_free_gib,
    )
    if health["status"] == "critical":
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

    with engine.connect() as connection:
        earliest = connection.execute(
            select(func.min(raw_market_events.c.received_at))
        ).scalar_one()

    raw_intervals: list[dict[str, object]] = []
    hot_cutoff = now - timedelta(hours=settings.storage_hot_raw_hours)
    eligible_end = _floor_hour(hot_cutoff)
    if earliest is not None:
        interval_start = _floor_hour(
            earliest.replace(tzinfo=UTC) if earliest.tzinfo is None else earliest
        )
        while interval_start + timedelta(hours=1) <= eligible_end:
            health = disk_health(
                archive_dir,
                warning_free_gib=settings.storage_warning_free_gib,
                critical_free_gib=settings.storage_critical_free_gib,
            )
            if health["status"] == "critical":
                break

            interval_end = interval_start + timedelta(hours=1)
            manifest = archive_interval(engine, archive_dir, interval_start, interval_end)
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
    final_health = disk_health(
        archive_dir,
        warning_free_gib=settings.storage_warning_free_gib,
        critical_free_gib=settings.storage_critical_free_gib,
    )
    _print(
        {
            "status": final_health["status"],
            "removed_archives": removed_archives,
            "raw_intervals": raw_intervals,
            "state_rows_deleted": state_rows_deleted,
            "disk": final_health,
        }
    )
    return 1 if final_health["status"] == "critical" else 0


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
