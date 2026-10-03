from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, or_, select

from bp_engine.features.calculators import FeatureGroup, time_geometry
from bp_engine.features.v4_calculators import (
    BTCRegimeAnchorSet,
    BTCShortAnchorSet,
    cross_venue_group,
    regime_consensus_group,
    regime_return_group,
    short_return_group,
)
from bp_engine.features.v4_models import BTCStateObservation, V4FeatureTarget
from bp_engine.storage.schema import raw_market_events

V4_SOURCE_TIME_FEATURE_VERSION = "v4-source-time-features-v2"
MAX_SOURCE_AGE_SECONDS = 2.0
MAX_FUTURE_SKEW_SECONDS = 1.0
_QUERY_PADDING_SECONDS = 1.0
V4_CORE_SOURCE_REQUIRED_FLAGS = (
    "coinbase_market_start_missing",
    "coinbase_market_start_stale",
    "coinbase_current_missing",
    "coinbase_current_stale",
    "bybit_spot_market_start_missing",
    "bybit_spot_market_start_stale",
    "bybit_spot_current_missing",
    "bybit_spot_current_stale",
    "bybit_linear_market_start_missing",
    "bybit_linear_market_start_stale",
    "bybit_linear_current_missing",
    "bybit_linear_current_stale",
)


class V4SourceTimeFeatureError(RuntimeError):
    """Raised when prospective V4 source-time feature construction is invalid."""


@dataclass(frozen=True)
class SourceTimeEventEvidence:
    row_id: int
    source: str
    stream: str
    instrument: str
    event_type: str
    requested_at: datetime
    source_at: datetime
    received_at: datetime
    source_age_seconds: float
    transport_lag_seconds: float

    def as_mapping(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "source": self.source,
            "stream": self.stream,
            "instrument": self.instrument,
            "event_type": self.event_type,
            "requested_at": self.requested_at.isoformat(),
            "source_at": self.source_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "source_age_seconds": self.source_age_seconds,
            "transport_lag_seconds": self.transport_lag_seconds,
        }


@dataclass(frozen=True)
class SourceTimeV4Features:
    feature_version: str
    condition_id: str
    decision_at: datetime
    predictors: dict[str, float | int | None]
    missing_flags: dict[str, bool]
    source_cutoffs: dict[str, datetime]
    source_evidence: dict[str, SourceTimeEventEvidence]

    def evidence_mapping(self) -> dict[str, object]:
        return {
            key: value.as_mapping()
            for key, value in sorted(self.source_evidence.items())
        }

    def core_source_ineligible_reasons(self) -> tuple[str, ...]:
        return tuple(
            flag
            for flag in V4_CORE_SOURCE_REQUIRED_FLAGS
            if self.missing_flags.get(flag, True)
        )

    @property
    def core_source_ready(self) -> bool:
        return not self.core_source_ineligible_reasons()


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V4SourceTimeFeatureError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: object) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _coinbase_price(row: dict[str, Any]) -> Decimal | None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    events = payload.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        return None
    first = events[0]
    event_type = str(row.get("event_type") or "")
    if event_type.startswith("ticker_"):
        tickers = first.get("tickers")
        if isinstance(tickers, list) and tickers and isinstance(tickers[0], dict):
            return _decimal(tickers[0].get("price"))
    if event_type.startswith("market_trades_"):
        trades = first.get("trades")
        if isinstance(trades, list) and trades and isinstance(trades[-1], dict):
            return _decimal(trades[-1].get("price"))
    return None


def _bybit_price(row: dict[str, Any]) -> Decimal | None:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    event_type = str(row.get("event_type") or "")
    if event_type == "ticker" and isinstance(data, dict):
        return _decimal(data.get("lastPrice"))
    if event_type == "trade" and isinstance(data, list) and data:
        trade = data[-1]
        if isinstance(trade, dict):
            return _decimal(trade.get("p"))
    return None


def _bybit_ticker_state(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    mapping = {
        "fundingRate": "funding_rate",
        "openInterest": "open_interest",
    }
    result: dict[str, Any] = {}
    for source_name, state_name in mapping.items():
        value = data.get(source_name)
        if value not in (None, ""):
            result[state_name] = str(value)
    return result


class V4SourceTimeReader:
    def __init__(
        self,
        *,
        max_source_age_seconds: float = MAX_SOURCE_AGE_SECONDS,
        max_future_skew_seconds: float = MAX_FUTURE_SKEW_SECONDS,
    ) -> None:
        if max_source_age_seconds <= 0:
            raise ValueError("max_source_age_seconds must be positive")
        if max_future_skew_seconds < 0:
            raise ValueError("max_future_skew_seconds must be non-negative")
        self.max_source_age_seconds = float(max_source_age_seconds)
        self.max_future_skew_seconds = float(max_future_skew_seconds)

    def _candidate_rows(
        self,
        connection: Connection,
        *,
        source: str,
        stream: str,
        instrument: str,
        requested_at: datetime,
        ticker_only: bool = False,
    ) -> list[dict[str, Any]]:
        requested = _utc(requested_at, "requested_at")
        window = (
            self.max_source_age_seconds
            + self.max_future_skew_seconds
            + _QUERY_PADDING_SECONDS
        )
        statement = select(raw_market_events).where(
            raw_market_events.c.source == source,
            raw_market_events.c.stream == stream,
            raw_market_events.c.instrument == instrument,
            raw_market_events.c.source_timestamp.is_not(None),
            raw_market_events.c.received_at >= requested - timedelta(seconds=window),
            raw_market_events.c.received_at <= requested,
            raw_market_events.c.source_timestamp
            >= requested - timedelta(seconds=self.max_source_age_seconds),
            raw_market_events.c.source_timestamp
            <= requested + timedelta(seconds=self.max_future_skew_seconds),
        )
        if source == "coinbase":
            if ticker_only:
                statement = statement.where(
                    raw_market_events.c.event_type.like("ticker_%")
                )
            else:
                statement = statement.where(
                    or_(
                        raw_market_events.c.event_type.like("ticker_%"),
                        raw_market_events.c.event_type.like("market_trades_%"),
                    )
                )
        elif source == "bybit":
            if ticker_only:
                statement = statement.where(raw_market_events.c.event_type == "ticker")
            else:
                statement = statement.where(
                    raw_market_events.c.event_type.in_(("ticker", "trade"))
                )
        else:
            raise V4SourceTimeFeatureError(f"unsupported V4 BTC source: {source}")

        return [
            dict(row)
            for row in connection.execute(
                statement.order_by(
                    raw_market_events.c.received_at.desc(),
                    raw_market_events.c.id.desc(),
                )
            ).mappings()
        ]

    def _evidence(
        self,
        row: dict[str, Any],
        requested_at: datetime,
    ) -> SourceTimeEventEvidence | None:
        requested = _utc(requested_at, "requested_at")
        source_at = _db_utc(row["source_timestamp"])
        received_at = _db_utc(row["received_at"])
        source_age = (requested - source_at).total_seconds()
        transport_lag = (received_at - source_at).total_seconds()
        if source_age < -self.max_future_skew_seconds:
            return None
        if source_age > self.max_source_age_seconds:
            return None
        if transport_lag < -self.max_future_skew_seconds:
            return None
        if received_at > requested:
            return None
        return SourceTimeEventEvidence(
            row_id=int(row["id"]),
            source=str(row["source"]),
            stream=str(row["stream"]),
            instrument=str(row["instrument"]),
            event_type=str(row["event_type"]),
            requested_at=requested,
            source_at=source_at,
            received_at=received_at,
            source_age_seconds=source_age,
            transport_lag_seconds=transport_lag,
        )

    @staticmethod
    def _source_time_rank(
        evidence: SourceTimeEventEvidence,
    ) -> tuple[float, float, float, int]:
        return (
            abs(evidence.source_age_seconds),
            -evidence.source_at.timestamp(),
            -evidence.received_at.timestamp(),
            -evidence.row_id,
        )

    def latest_price(
        self,
        connection: Connection,
        *,
        source: str,
        stream: str,
        instrument: str,
        requested_at: datetime,
        include_linear_ticker_state: bool = False,
    ) -> tuple[BTCStateObservation | None, tuple[SourceTimeEventEvidence, ...]]:
        requested = _utc(requested_at, "requested_at")
        price_candidates: list[
            tuple[dict[str, Any], SourceTimeEventEvidence, Decimal]
        ] = []
        for row in self._candidate_rows(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=requested,
        ):
            evidence = self._evidence(row, requested)
            if evidence is None:
                continue
            parsed = (
                _coinbase_price(row)
                if source == "coinbase"
                else _bybit_price(row)
            )
            if parsed is None:
                continue
            price_candidates.append((row, evidence, parsed))

        if not price_candidates:
            return None, ()

        chosen_row, chosen_evidence, price = min(
            price_candidates,
            key=lambda item: self._source_time_rank(item[1]),
        )

        state: dict[str, Any] = {"last_price": str(price)}
        evidence_items = [chosen_evidence]
        if include_linear_ticker_state:
            ticker_candidates: list[
                tuple[dict[str, Any], SourceTimeEventEvidence]
            ] = []
            for ticker_row in self._candidate_rows(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                requested_at=requested,
                ticker_only=True,
            ):
                ticker_evidence = self._evidence(ticker_row, requested)
                if ticker_evidence is None:
                    continue
                ticker_candidates.append((ticker_row, ticker_evidence))

            if ticker_candidates:
                ticker_row, ticker_evidence = min(
                    ticker_candidates,
                    key=lambda item: self._source_time_rank(item[1]),
                )
                state.update(_bybit_ticker_state(ticker_row))
                if ticker_evidence.row_id != chosen_evidence.row_id:
                    evidence_items.append(ticker_evidence)

        observation = BTCStateObservation(
            row_id=chosen_evidence.row_id,
            bucket_at=chosen_evidence.source_at,
            last_event_at=chosen_evidence.source_at,
            source=source,
            stream=stream,
            instrument=instrument,
            price=price,
            state=state,
            fresh=True,
            age_seconds=chosen_evidence.source_age_seconds,
        )
        return observation, tuple(evidence_items)


def _merge_groups(
    groups: tuple[FeatureGroup, ...],
) -> tuple[
    dict[str, float | int | None],
    dict[str, bool],
    dict[str, datetime],
]:
    values: dict[str, float | int | None] = {}
    missing: dict[str, bool] = {}
    cutoffs: dict[str, datetime] = {}
    for group in groups:
        for key, value in group.values.items():
            if key in values:
                raise V4SourceTimeFeatureError(f"duplicate V4 feature: {key}")
            values[key] = value
        for key, value in group.missing_flags.items():
            if key in missing:
                raise V4SourceTimeFeatureError(f"duplicate V4 missing flag: {key}")
            missing[key] = bool(value)
        for key, value in group.source_cutoffs.items():
            if key in cutoffs:
                raise V4SourceTimeFeatureError(f"duplicate V4 cutoff: {key}")
            cutoffs[key] = _db_utc(value)
    return values, missing, cutoffs


def _regime_values(group: FeatureGroup, prefix: str) -> dict[str, float | None]:
    return {
        f"return_{horizon}": group.values[f"{prefix}_return_{horizon}"]
        for horizon in ("5m", "15m", "60m")
    }


def build_source_time_v4_features(
    connection: Connection,
    target: V4FeatureTarget,
    *,
    decision_at: datetime,
    reader: V4SourceTimeReader | None = None,
) -> SourceTimeV4Features:
    decision = _utc(decision_at, "decision_at")
    start = _utc(target.market_start_at, "market_start_at")
    end = _utc(target.market_end_at, "market_end_at")
    if target.horizon_seconds != 300 or int((end - start).total_seconds()) != 300:
        raise V4SourceTimeFeatureError("V4 target must be a 300-second market")
    expected_decision = start + timedelta(seconds=240)
    if decision != expected_decision:
        raise V4SourceTimeFeatureError("V4 decision_at must equal market_start_at + 240s")

    reader = reader or V4SourceTimeReader()
    venue_specs = (
        ("coinbase", "coinbase", "spot", "BTC-USD"),
        ("bybit_spot", "bybit", "spot", "BTCUSDT"),
        ("bybit_linear", "bybit", "linear", "BTCUSDT"),
    )
    source_evidence: dict[str, SourceTimeEventEvidence] = {}
    short_sets: dict[str, BTCShortAnchorSet] = {}
    regime_groups: dict[str, FeatureGroup] = {}
    groups: list[FeatureGroup] = [time_geometry(target, decision)]

    for prefix, source, stream, instrument in venue_specs:
        anchors: dict[str, BTCStateObservation | None] = {}
        short_times = {
            "market_start": start,
            "trailing_120s": decision - timedelta(seconds=120),
            "trailing_60s": decision - timedelta(seconds=60),
            "trailing_30s": decision - timedelta(seconds=30),
            "current": decision,
        }
        for name, requested in short_times.items():
            observation, evidence_items = reader.latest_price(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                requested_at=requested,
                include_linear_ticker_state=(
                    prefix == "bybit_linear" and name == "current"
                ),
            )
            anchors[name] = observation
            for index, evidence in enumerate(evidence_items):
                suffix = "" if index == 0 else "_ticker"
                source_evidence[f"{prefix}_{name}{suffix}"] = evidence

        short = BTCShortAnchorSet(
            market_start=anchors["market_start"],
            trailing_120s=anchors["trailing_120s"],
            trailing_60s=anchors["trailing_60s"],
            trailing_30s=anchors["trailing_30s"],
            current=anchors["current"],
        )
        short_sets[prefix] = short
        short_group = short_return_group(prefix, short)

        regime_anchors: dict[str, BTCStateObservation | None] = {}
        for name, seconds in (
            ("trailing_60m", 3600),
            ("trailing_15m", 900),
            ("trailing_5m", 300),
        ):
            observation, evidence_items = reader.latest_price(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                requested_at=decision - timedelta(seconds=seconds),
            )
            regime_anchors[name] = observation
            for index, evidence in enumerate(evidence_items):
                suffix = "" if index == 0 else f"_{index}"
                source_evidence[f"{prefix}_regime_{name}{suffix}"] = evidence

        regime = BTCRegimeAnchorSet(
            trailing_60m=regime_anchors["trailing_60m"],
            trailing_15m=regime_anchors["trailing_15m"],
            trailing_5m=regime_anchors["trailing_5m"],
            current=short.current,
        )
        regime_group = regime_return_group(prefix, regime)
        regime_groups[prefix] = regime_group
        groups.extend((short_group, regime_group))

    groups.append(
        cross_venue_group(
            short_sets["coinbase"],
            short_sets["bybit_spot"],
            short_sets["bybit_linear"],
        )
    )
    groups.append(
        regime_consensus_group(
            coinbase=_regime_values(regime_groups["coinbase"], "coinbase"),
            bybit_spot=_regime_values(regime_groups["bybit_spot"], "bybit_spot"),
            bybit_linear=_regime_values(
                regime_groups["bybit_linear"], "bybit_linear"
            ),
        )
    )

    predictors, missing_flags, source_cutoffs = _merge_groups(tuple(groups))
    return SourceTimeV4Features(
        feature_version=V4_SOURCE_TIME_FEATURE_VERSION,
        condition_id=target.condition_id,
        decision_at=decision,
        predictors=predictors,
        missing_flags=missing_flags,
        source_cutoffs=source_cutoffs,
        source_evidence=source_evidence,
    )
