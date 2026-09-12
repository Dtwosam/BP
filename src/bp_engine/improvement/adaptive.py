from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, exists, select

from bp_engine.improvement.hashing import semantic_sha256
from bp_engine.storage.schema import market_features, market_labels

DEFAULT_ADAPTIVE_TRIGGER_COUNT = 50
ADAPTIVE_READINESS_VERSION = "adaptive-readiness-v1"


@dataclass(frozen=True)
class AdaptiveReadinessReport:
    readiness_version: str
    horizon_seconds: int
    feature_version: str
    label_version: str
    since_at: datetime
    cutoff_at: datetime
    trigger_count: int
    eligible_resolved_market_count: int
    first_label_generated_at: datetime | None
    last_label_generated_at: datetime | None
    condition_ids_sha256: str
    ready: bool
    semantic_sha256: str


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _nonblank(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be nonblank")
    return normalized


def build_adaptive_readiness_report(
    connection: Connection,
    *,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
    since_at: datetime,
    cutoff_at: datetime,
    trigger_count: int = DEFAULT_ADAPTIVE_TRIGGER_COUNT,
) -> AdaptiveReadinessReport:
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if trigger_count <= 0:
        raise ValueError("trigger_count must be positive")

    feature = _nonblank(feature_version, "feature_version")
    label = _nonblank(label_version, "label_version")
    since = _utc(since_at, "since_at")
    cutoff = _utc(cutoff_at, "cutoff_at")
    if cutoff <= since:
        raise ValueError("cutoff_at must be after since_at")

    matching_feature = exists(
        select(1).where(
            market_features.c.condition_id == market_labels.c.condition_id,
            market_features.c.horizon_seconds == horizon_seconds,
            market_features.c.feature_version == feature,
        )
    )
    rows = connection.execute(
        select(
            market_labels.c.condition_id,
            market_labels.c.generated_at,
        )
        .where(
            market_labels.c.horizon_seconds == horizon_seconds,
            market_labels.c.label_version == label,
            market_labels.c.generated_at > since,
            market_labels.c.generated_at <= cutoff,
            matching_feature,
        )
        .order_by(market_labels.c.condition_id, market_labels.c.generated_at)
    ).mappings()

    generated_by_condition: dict[str, datetime] = {}
    for row in rows:
        condition_id = str(row["condition_id"])
        generated_at = _stored_utc(row["generated_at"])
        previous = generated_by_condition.get(condition_id)
        if previous is None or generated_at < previous:
            generated_by_condition[condition_id] = generated_at

    condition_ids = tuple(sorted(generated_by_condition))
    generated_times = tuple(generated_by_condition.values())
    first_generated_at = min(generated_times) if generated_times else None
    last_generated_at = max(generated_times) if generated_times else None
    condition_ids_sha256 = semantic_sha256(condition_ids)
    eligible_count = len(condition_ids)
    ready = eligible_count >= trigger_count

    semantics = {
        "readiness_version": ADAPTIVE_READINESS_VERSION,
        "horizon_seconds": horizon_seconds,
        "feature_version": feature,
        "label_version": label,
        "since_at": since,
        "cutoff_at": cutoff,
        "trigger_count": trigger_count,
        "eligible_resolved_market_count": eligible_count,
        "first_label_generated_at": first_generated_at,
        "last_label_generated_at": last_generated_at,
        "condition_ids_sha256": condition_ids_sha256,
        "ready": ready,
    }
    return AdaptiveReadinessReport(
        readiness_version=ADAPTIVE_READINESS_VERSION,
        horizon_seconds=horizon_seconds,
        feature_version=feature,
        label_version=label,
        since_at=since,
        cutoff_at=cutoff,
        trigger_count=trigger_count,
        eligible_resolved_market_count=eligible_count,
        first_label_generated_at=first_generated_at,
        last_label_generated_at=last_generated_at,
        condition_ids_sha256=condition_ids_sha256,
        ready=ready,
        semantic_sha256=semantic_sha256(semantics),
    )
