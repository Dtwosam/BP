from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RawExclusion:
    start: datetime
    end: datetime
    reason: str


RAW_EXCLUSIONS = (
    RawExclusion(
        start=datetime(2026, 8, 22, 20, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 21, 0, tzinfo=UTC),
        reason="phase3_known_damaged_raw_interval",
    ),
    RawExclusion(
        start=datetime(2026, 8, 23, 21, 0, tzinfo=UTC),
        end=datetime(2026, 8, 24, 10, 18, 16, 692122, tzinfo=UTC),
        reason="phase3_rollout_raw_coverage_unproven",
    ),
)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def raw_window_exclusion(start: datetime, end: datetime) -> RawExclusion | None:
    """Return the first exclusion overlapping the half-open interval [start, end)."""
    normalized_start = _utc(start, "start")
    normalized_end = _utc(end, "end")
    if normalized_end <= normalized_start:
        raise ValueError("end must be after start")
    for exclusion in RAW_EXCLUSIONS:
        if normalized_start < exclusion.end and normalized_end > exclusion.start:
            return exclusion
    return None
