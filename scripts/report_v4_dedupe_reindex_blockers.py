from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text

from bp_engine.config import Settings

DEFAULT_SAMPLES = 80
DEFAULT_INTERVAL_SECONDS = 0.5
DEFAULT_LONG_TRANSACTION_SECONDS = 60.0

_QUERY_RELATION_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "paper_execution",
        (
            "paper_orders",
            "paper_fills",
            "paper_order_terminal_events",
            "paper_settlements",
        ),
    ),
    (
        "live_prediction",
        (
            "live_predictions",
            "live_prediction_evaluations",
        ),
    ),
    (
        "market_replay",
        (
            "raw_market_events",
            "market_state_1s",
        ),
    ),
    (
        "market_catalog",
        ("polymarket_markets",),
    ),
    (
        "labels_features",
        (
            "market_labels",
            "market_features",
        ),
    ),
    (
        "recorder_dedupe",
        ("raw_event_dedupe",),
    ),
    (
        "storage_maintenance",
        ("storage_maintenance_runs",),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only V4 long-transaction blocker attribution probe"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--long-transaction-seconds",
        type=float,
        default=DEFAULT_LONG_TRANSACTION_SECONDS,
    )
    return parser.parse_args()


def _query_class(query: str | None) -> str:
    normalized = " ".join((query or "").lower().split())
    if "insert into raw_event_dedupe" in normalized:
        return "dedupe_insert"
    if "insert into raw_market_events" in normalized:
        return "raw_insert"
    if "insert into market_state_1s" in normalized:
        return "state_upsert"
    if "delete from raw_event_dedupe" in normalized:
        return "dedupe_cleanup"
    if "delete from raw_market_events" in normalized:
        return "raw_cleanup"
    if "storage_maintenance_runs" in normalized:
        return "storage_maintenance"
    if normalized.startswith("select") or " select " in normalized:
        return "select"
    return "other"


def _query_fingerprint(query: str | None) -> str | None:
    normalized = " ".join((query or "").split())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _relation_families(query: str | None) -> tuple[str, ...]:
    normalized = " ".join((query or "").lower().split())
    if not normalized:
        return ()
    return tuple(
        family
        for family, relations in _QUERY_RELATION_FAMILIES
        if any(relation in normalized for relation in relations)
    )


def _service_signature(query: str | None) -> str | None:
    families = set(_relation_families(query))
    if "paper_execution" in families:
        return "legacy_paper_execution"
    if "storage_maintenance" in families:
        return "storage_maintenance"
    if "recorder_dedupe" in families:
        return "recorder_dedupe"
    if "labels_features" in families and "live_prediction" in families:
        return "prospective_outcomes_like"
    if "market_replay" in families and "live_prediction" in families:
        return "prediction_or_execution_read"
    if "market_replay" in families:
        return "market_replay_read"
    if "live_prediction" in families and "market_catalog" in families:
        return "prediction_catalog_read"
    if "live_prediction" in families:
        return "live_prediction_read"
    if "market_catalog" in families:
        return "market_catalog_read"
    return None


def _sample(connection, threshold_seconds: float) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                activity.pid,
                activity.usename,
                activity.application_name,
                activity.state,
                EXTRACT(
                    EPOCH FROM (clock_timestamp() - activity.xact_start)
                ) AS xact_age_seconds,
                CASE
                    WHEN activity.query_start IS NULL THEN NULL
                    ELSE EXTRACT(
                        EPOCH FROM (clock_timestamp() - activity.query_start)
                    )
                END AS query_age_seconds,
                CASE
                    WHEN activity.state_change IS NULL THEN NULL
                    ELSE EXTRACT(
                        EPOCH FROM (clock_timestamp() - activity.state_change)
                    )
                END AS state_age_seconds,
                activity.wait_event_type,
                activity.wait_event,
                activity.backend_xid::text AS backend_xid,
                activity.backend_xmin::text AS backend_xmin,
                activity.query,
                COALESCE(
                    jsonb_object_agg(
                        locks.mode,
                        locks.lock_count
                    ) FILTER (WHERE locks.mode IS NOT NULL),
                    '{}'::jsonb
                ) AS lock_modes
            FROM pg_stat_activity AS activity
            LEFT JOIN LATERAL (
                SELECT
                    lock.mode,
                    COUNT(*)::bigint AS lock_count
                FROM pg_locks AS lock
                WHERE lock.pid = activity.pid
                  AND lock.granted IS TRUE
                GROUP BY lock.mode
            ) AS locks ON TRUE
            WHERE activity.datname = current_database()
              AND activity.backend_type = 'client backend'
              AND activity.pid <> pg_backend_pid()
              AND activity.xact_start IS NOT NULL
              AND clock_timestamp() - activity.xact_start
                    >= (:threshold_seconds * interval '1 second')
            GROUP BY
                activity.pid,
                activity.usename,
                activity.application_name,
                activity.state,
                activity.xact_start,
                activity.query_start,
                activity.state_change,
                activity.wait_event_type,
                activity.wait_event,
                activity.backend_xid,
                activity.backend_xmin,
                activity.query
            ORDER BY activity.xact_start, activity.pid
            """
        ),
        {"threshold_seconds": threshold_seconds},
    ).mappings()

    sampled: list[dict[str, Any]] = []
    for row in rows:
        query = str(row["query"] or "")
        sampled.append(
            {
                "pid": int(row["pid"]),
                "usename": str(row["usename"] or ""),
                "application_name": str(row["application_name"] or ""),
                "state": str(row["state"] or ""),
                "xact_age_seconds": float(row["xact_age_seconds"] or 0.0),
                "query_age_seconds": (
                    float(row["query_age_seconds"])
                    if row["query_age_seconds"] is not None
                    else None
                ),
                "state_age_seconds": (
                    float(row["state_age_seconds"])
                    if row["state_age_seconds"] is not None
                    else None
                ),
                "wait_event_type": (
                    str(row["wait_event_type"])
                    if row["wait_event_type"] is not None
                    else None
                ),
                "wait_event": (
                    str(row["wait_event"])
                    if row["wait_event"] is not None
                    else None
                ),
                "backend_xid": (
                    str(row["backend_xid"])
                    if row["backend_xid"] is not None
                    else None
                ),
                "backend_xmin": (
                    str(row["backend_xmin"])
                    if row["backend_xmin"] is not None
                    else None
                ),
                "query_class": _query_class(query),
                "query_fingerprint": _query_fingerprint(query),
                "relation_families": _relation_families(query),
                "service_signature": _service_signature(query),
                "lock_modes": dict(row["lock_modes"] or {}),
            }
        )
    return sampled


def build_report(
    connection,
    *,
    samples: int,
    interval_seconds: float,
    long_transaction_seconds: float,
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("samples must be greater than zero")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than zero")
    if long_transaction_seconds <= 0:
        raise ValueError("long_transaction_seconds must be greater than zero")

    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    started_at = datetime.now(UTC)
    observations: dict[int, dict[str, Any]] = {}
    active_counts: list[int] = []
    first_sample_pids: set[int] = set()
    last_sample_pids: set[int] = set()

    for sample_index in range(samples):
        sampled_at = datetime.now(UTC)
        rows = _sample(connection, long_transaction_seconds)
        pids = {int(row["pid"]) for row in rows}
        active_counts.append(len(rows))
        if sample_index == 0:
            first_sample_pids = set(pids)
        if sample_index == samples - 1:
            last_sample_pids = set(pids)

        for row in rows:
            pid = int(row["pid"])
            entry = observations.setdefault(
                pid,
                {
                    "pid": pid,
                    "first_seen_at": sampled_at.isoformat(),
                    "last_seen_at": sampled_at.isoformat(),
                    "samples_seen": 0,
                    "max_xact_age_seconds": 0.0,
                    "max_query_age_seconds": None,
                    "max_state_age_seconds": None,
                    "states": Counter(),
                    "waits": Counter(),
                    "query_classes": Counter(),
                    "query_fingerprints": set(),
                    "relation_families": Counter(),
                    "service_signatures": Counter(),
                    "application_names": set(),
                    "users": set(),
                    "backend_xids": set(),
                    "backend_xmins": set(),
                    "lock_modes": Counter(),
                },
            )
            entry["last_seen_at"] = sampled_at.isoformat()
            entry["samples_seen"] += 1
            entry["max_xact_age_seconds"] = max(
                float(entry["max_xact_age_seconds"]),
                float(row["xact_age_seconds"]),
            )
            if row["query_age_seconds"] is not None:
                entry["max_query_age_seconds"] = max(
                    float(entry["max_query_age_seconds"] or 0.0),
                    float(row["query_age_seconds"]),
                )
            if row["state_age_seconds"] is not None:
                entry["max_state_age_seconds"] = max(
                    float(entry["max_state_age_seconds"] or 0.0),
                    float(row["state_age_seconds"]),
                )

            entry["states"][str(row["state"])] += 1
            wait_key = (
                f"{row['wait_event_type'] or 'none'}:"
                f"{row['wait_event'] or 'none'}"
            )
            entry["waits"][wait_key] += 1
            entry["query_classes"][str(row["query_class"])] += 1
            for family in row["relation_families"]:
                entry["relation_families"][str(family)] += 1
            if row["service_signature"] is not None:
                entry["service_signatures"][str(row["service_signature"])] += 1
            if row["query_fingerprint"] is not None:
                entry["query_fingerprints"].add(
                    str(row["query_fingerprint"])
                )
            entry["application_names"].add(
                str(row["application_name"])
            )
            entry["users"].add(str(row["usename"]))
            if row["backend_xid"] is not None:
                entry["backend_xids"].add(str(row["backend_xid"]))
            if row["backend_xmin"] is not None:
                entry["backend_xmins"].add(str(row["backend_xmin"]))
            for mode, count in dict(row["lock_modes"]).items():
                entry["lock_modes"][str(mode)] += int(count)

        if sample_index + 1 < samples:
            time.sleep(interval_seconds)

    completed_at = datetime.now(UTC)
    persistent_pids = sorted(first_sample_pids & last_sample_pids)
    observed_pids = sorted(observations)
    cleared_pids = sorted(first_sample_pids - last_sample_pids)
    appeared_pids = sorted(last_sample_pids - first_sample_pids)

    rendered_observations: list[dict[str, Any]] = []
    for pid in observed_pids:
        entry = observations[pid]
        rendered_observations.append(
            {
                "pid": pid,
                "first_seen_at": entry["first_seen_at"],
                "last_seen_at": entry["last_seen_at"],
                "samples_seen": int(entry["samples_seen"]),
                "sample_fraction": int(entry["samples_seen"]) / samples,
                "persistent_first_to_last_sample": pid in persistent_pids,
                "max_xact_age_seconds": float(
                    entry["max_xact_age_seconds"]
                ),
                "max_query_age_seconds": entry["max_query_age_seconds"],
                "max_state_age_seconds": entry["max_state_age_seconds"],
                "states": dict(sorted(entry["states"].items())),
                "waits": dict(sorted(entry["waits"].items())),
                "query_classes": dict(
                    sorted(entry["query_classes"].items())
                ),
                "query_fingerprint_count": len(
                    entry["query_fingerprints"]
                ),
                "relation_families": dict(
                    sorted(entry["relation_families"].items())
                ),
                "service_signatures": dict(
                    sorted(entry["service_signatures"].items())
                ),
                "application_names": sorted(entry["application_names"]),
                "users": sorted(entry["users"]),
                "backend_xids": sorted(entry["backend_xids"]),
                "backend_xmins": sorted(entry["backend_xmins"]),
                "lock_modes": dict(sorted(entry["lock_modes"].items())),
            }
        )

    persistent_writer_like = [
        entry
        for entry in rendered_observations
        if entry["persistent_first_to_last_sample"]
        and any(
            query_class in entry["query_classes"]
            for query_class in (
                "dedupe_insert",
                "raw_insert",
                "state_upsert",
            )
        )
    ]

    return {
        "report": "v4_dedupe_reindex_blockers_v1",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "samples": samples,
        "interval_seconds": interval_seconds,
        "long_transaction_seconds": long_transaction_seconds,
        "active_long_transaction_count": {
            "minimum": min(active_counts) if active_counts else 0,
            "maximum": max(active_counts) if active_counts else 0,
            "nonzero_sample_count": sum(
                1 for count in active_counts if count > 0
            ),
        },
        "pid_summary": {
            "observed_pids": observed_pids,
            "persistent_pids": persistent_pids,
            "cleared_pids": cleared_pids,
            "appeared_pids": appeared_pids,
        },
        "observations": rendered_observations,
        "signals": {
            "persistent_long_transaction_count": len(persistent_pids),
            "persistent_writer_like_count": len(persistent_writer_like),
            "writer_quiesce_likely_required_for_bounded_reindex": (
                len(persistent_writer_like) > 0
            ),
        },
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
            "raw_query_text_emitted": False,
        },
    }


def main() -> int:
    args = parse_args()
    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-dedupe-reindex-blockers"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(
                connection,
                samples=args.samples,
                interval_seconds=args.interval_seconds,
                long_transaction_seconds=args.long_transaction_seconds,
            )
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DEDUPE_REINDEX_BLOCKERS_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    print("RAW_QUERY_TEXT_EMITTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
