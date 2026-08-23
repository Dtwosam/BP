from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, func, select

from bp_engine.storage.schema import feed_incidents, raw_market_events


@dataclass(frozen=True, order=True)
class FeedKey:
    source: str
    stream: str

    @property
    def label(self) -> str:
        return f"{self.source}/{self.stream}"


class FeedStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int
    first_received_at: datetime | None = None
    last_received_at: datetime | None = None


class SoakReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_at: datetime
    end_at: datetime
    duration_seconds: int
    minimum_duration_seconds: int
    feeds: dict[str, FeedStats]
    incidents: dict[str, dict[str, int]]
    passed: bool
    failures: list[str]


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def build_soak_report(
    connection: Connection,
    *,
    start_at: datetime,
    end_at: datetime,
    required_feeds: list[FeedKey],
    minimum_duration_seconds: int,
) -> SoakReport:
    start = _aware_utc(start_at, "start_at")
    end = _aware_utc(end_at, "end_at")
    if end <= start:
        raise ValueError("end_at must be after start_at")
    if minimum_duration_seconds < 0:
        raise ValueError("minimum_duration_seconds must be non-negative")

    event_rows = connection.execute(
        select(
            raw_market_events.c.source,
            raw_market_events.c.stream,
            func.count().label("event_count"),
            func.min(raw_market_events.c.received_at).label("first_received_at"),
            func.max(raw_market_events.c.received_at).label("last_received_at"),
        )
        .where(raw_market_events.c.received_at >= start)
        .where(raw_market_events.c.received_at < end)
        .group_by(raw_market_events.c.source, raw_market_events.c.stream)
    ).mappings()
    feeds = {
        f"{row['source']}/{row['stream']}": FeedStats(
            event_count=int(row["event_count"]),
            first_received_at=row["first_received_at"],
            last_received_at=row["last_received_at"],
        )
        for row in event_rows
    }

    incident_rows = connection.execute(
        select(
            feed_incidents.c.source,
            feed_incidents.c.stream,
            feed_incidents.c.incident_type,
            func.count().label("incident_count"),
        )
        .where(feed_incidents.c.observed_at >= start)
        .where(feed_incidents.c.observed_at < end)
        .group_by(
            feed_incidents.c.source,
            feed_incidents.c.stream,
            feed_incidents.c.incident_type,
        )
    ).mappings()
    incidents: dict[str, dict[str, int]] = {}
    for row in incident_rows:
        label = f"{row['source']}/{row['stream']}"
        incidents.setdefault(label, {})[str(row["incident_type"])] = int(
            row["incident_count"]
        )

    stale_rows = connection.execute(
        select(
            feed_incidents.c.source,
            feed_incidents.c.stream,
            feed_incidents.c.incident_type,
            feed_incidents.c.observed_at,
        )
        .where(feed_incidents.c.observed_at >= start)
        .where(feed_incidents.c.observed_at < end)
        .where(feed_incidents.c.incident_type.in_(["stale", "recovered"]))
        .order_by(feed_incidents.c.observed_at)
    ).mappings()
    latest_stale_state: dict[str, str] = {}
    latest_stale_at: dict[str, datetime] = {}
    for row in stale_rows:
        label = f"{row['source']}/{row['stream']}"
        incident_type = str(row["incident_type"])
        latest_stale_state[label] = incident_type
        if incident_type == "stale":
            latest_stale_at[label] = row["observed_at"]

    duration_seconds = int((end - start).total_seconds())
    failures: list[str] = []
    if duration_seconds < minimum_duration_seconds:
        failures.append(
            f"window duration {duration_seconds}s is below required "
            f"{minimum_duration_seconds}s"
        )

    for feed in required_feeds:
        label = feed.label
        stats = feeds.get(label)
        if stats is None or stats.event_count == 0:
            failures.append(f"{label} has no events")
        counts = incidents.get(label, {})
        if counts.get("backpressure", 0):
            failures.append(f"{label} recorded backpressure")
        if counts.get("clock_skew", 0):
            failures.append(f"{label} recorded clock skew")
        if latest_stale_state.get(label) == "stale":
            stale_at = latest_stale_at[label]
            last_event_at = stats.last_received_at if stats is not None else None
            if last_event_at is None or last_event_at <= stale_at:
                failures.append(f"{label} has unresolved stale state")

    return SoakReport(
        start_at=start,
        end_at=end,
        duration_seconds=duration_seconds,
        minimum_duration_seconds=minimum_duration_seconds,
        feeds=feeds,
        incidents=incidents,
        passed=not failures,
        failures=failures,
    )
