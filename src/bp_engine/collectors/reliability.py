from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from bp_engine.recorder.models import FeedIncident


@dataclass(frozen=True)
class ReconnectPolicy:
    initial_seconds: float = 1.0
    multiplier: float = 2.0
    maximum_seconds: float = 30.0

    def delay_for_attempt(self, attempt: int) -> float:
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        delay = self.initial_seconds * (self.multiplier**attempt)
        return min(delay, self.maximum_seconds)


@dataclass
class FeedWatchdog:
    stale_after_seconds: float
    _last_seen: dict[tuple[str, str], float] = field(default_factory=dict)
    _stale: set[tuple[str, str]] = field(default_factory=set)

    def arm(
        self,
        source: str,
        stream: str,
        *,
        monotonic_time: float,
    ) -> None:
        self._last_seen[(source, stream)] = monotonic_time

    def observe(
        self,
        source: str,
        stream: str,
        *,
        monotonic_time: float,
        observed_at: datetime,
    ) -> FeedIncident | None:
        key = (source, stream)
        self._last_seen[key] = monotonic_time
        if key not in self._stale:
            return None
        self._stale.remove(key)
        return FeedIncident(
            source=source,
            stream=stream,
            incident_type="recovered",
            observed_at=observed_at,
            details={},
        )

    def check(
        self,
        source: str,
        stream: str,
        *,
        monotonic_time: float,
        observed_at: datetime,
    ) -> FeedIncident | None:
        key = (source, stream)
        last_seen = self._last_seen.get(key)
        if last_seen is None or key in self._stale:
            return None
        age = monotonic_time - last_seen
        if age <= self.stale_after_seconds:
            return None
        self._stale.add(key)
        return FeedIncident(
            source=source,
            stream=stream,
            incident_type="stale",
            observed_at=observed_at,
            details={"age_seconds": age},
        )


@dataclass(frozen=True)
class ClockSkewGuard:
    max_abs_skew_seconds: float

    def check(
        self,
        *,
        source: str,
        stream: str,
        source_timestamp: datetime | None,
        received_at: datetime,
    ) -> FeedIncident | None:
        if source_timestamp is None:
            return None
        skew_seconds = (received_at - source_timestamp).total_seconds()
        # A positive value is ordinary event age: source event time plus server,
        # network, and local processing latency. With an NTP-synchronized host,
        # clock-skew evidence is a source timestamp materially in the future.
        if skew_seconds >= -self.max_abs_skew_seconds:
            return None
        return FeedIncident(
            source=source,
            stream=stream,
            incident_type="clock_skew",
            observed_at=received_at,
            details={"skew_seconds": skew_seconds},
        )
