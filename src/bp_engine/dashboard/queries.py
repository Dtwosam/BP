from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.engine import Connection, Engine

from bp_engine.storage.schema import (
    feed_incidents,
    feed_status,
    live_prediction_evaluations,
    live_predictions,
    market_state_1s,
    polymarket_markets,
)

VERIFIED_HORIZONS = (300, 900)
_MAX_LIMIT = 100
_MAX_INCIDENT_WINDOW = timedelta(hours=24)
_SQLITE_LEDGER_QUANTUM = Decimal("0.000000000000000001")


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError("limit must be between 1 and 100")
    return limit


def _validate_horizon(horizon_seconds: int | None) -> int | None:
    if horizon_seconds is not None and horizon_seconds not in VERIFIED_HORIZONS:
        raise ValueError("horizon must be one of 300 or 900 seconds")
    return horizon_seconds


def _restore_sqlite_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return Decimal(str(float(value))).quantize(_SQLITE_LEDGER_QUANTUM)
    if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
        return value.replace(tzinfo=UTC)
    if isinstance(value, dict):
        return {key: _restore_sqlite_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_sqlite_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_restore_sqlite_value(item) for item in value)
    return value


def _compat_value(connection: Connection, value: Any) -> Any:
    if connection.dialect.name == "sqlite":
        return _restore_sqlite_value(value)
    return value


def _row_dict(connection: Connection, row: Any) -> dict[str, Any]:
    values = dict(row._mapping)
    if connection.dialect.name == "sqlite":
        return {key: _restore_sqlite_value(value) for key, value in values.items()}
    return values


class DashboardQueries:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def read_only_connection(self) -> Iterator[Connection]:
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                yield connection
            finally:
                transaction.rollback()

    def health(self) -> dict[str, str]:
        with self.read_only_connection() as connection:
            connection.execute(select(1)).scalar_one()
        return {"database": "ok"}

    def active_markets(
        self,
        *,
        now: datetime,
        horizon_seconds: int | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        _validate_limit(limit)
        _validate_horizon(horizon_seconds)

        clauses = [
            polymarket_markets.c.active.is_(True),
            polymarket_markets.c.closed.is_(False),
            polymarket_markets.c.end_at > now,
            polymarket_markets.c.horizon_seconds.in_(VERIFIED_HORIZONS),
        ]
        if horizon_seconds is not None:
            clauses.append(polymarket_markets.c.horizon_seconds == horizon_seconds)

        statement = (
            select(polymarket_markets)
            .where(*clauses)
            .order_by(
                polymarket_markets.c.horizon_seconds.asc(),
                polymarket_markets.c.end_at.asc(),
                polymarket_markets.c.id.asc(),
            )
            .limit(limit)
        )

        with self.read_only_connection() as connection:
            markets = [_row_dict(connection, row) for row in connection.execute(statement)]
            return [self._hydrate_market(connection, market) for market in markets]

    def predictions(
        self,
        *,
        horizon_seconds: int | None = None,
        evaluation_state: str | None = None,
        trade: bool | None = None,
        limit: int = 50,
        before_recorded_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        _validate_limit(limit)
        _validate_horizon(horizon_seconds)
        if evaluation_state not in (None, "pending", "evaluated"):
            raise ValueError("evaluation_state must be pending or evaluated")

        evaluation_exists = exists(
            select(live_prediction_evaluations.c.id).where(
                live_prediction_evaluations.c.prediction_id
                == live_predictions.c.prediction_id
            )
        )
        clauses = [live_predictions.c.horizon_seconds.in_(VERIFIED_HORIZONS)]
        if horizon_seconds is not None:
            clauses.append(live_predictions.c.horizon_seconds == horizon_seconds)
        if evaluation_state == "pending":
            clauses.append(~evaluation_exists)
        elif evaluation_state == "evaluated":
            clauses.append(evaluation_exists)
        if trade is not None:
            clauses.append(live_predictions.c.trade.is_(trade))
        if before_recorded_at is not None:
            clauses.append(live_predictions.c.recorded_at < before_recorded_at)

        statement = (
            select(live_predictions)
            .where(*clauses)
            .order_by(live_predictions.c.recorded_at.desc(), live_predictions.c.id.desc())
            .limit(limit)
        )

        with self.read_only_connection() as connection:
            rows: list[dict[str, Any]] = []
            for row in connection.execute(statement):
                item = _row_dict(connection, row)
                item["evaluation"] = self._evaluation_for_prediction(
                    connection, item["prediction_id"]
                )
                rows.append(item)
            return rows

    def feed_health(
        self,
        *,
        now: datetime,
        incident_window: timedelta = timedelta(hours=1),
    ) -> list[dict[str, Any]]:
        if incident_window <= timedelta(0) or incident_window > _MAX_INCIDENT_WINDOW:
            raise ValueError("incident window must be within 24 hours")
        since = now - incident_window

        statement = (
            select(feed_status)
            .order_by(feed_status.c.source.asc(), feed_status.c.stream.asc())
            .limit(_MAX_LIMIT)
        )
        with self.read_only_connection() as connection:
            rows: list[dict[str, Any]] = []
            for row in connection.execute(statement):
                item = _row_dict(connection, row)
                incident_filter = and_(
                    feed_incidents.c.source == item["source"],
                    feed_incidents.c.stream == item["stream"],
                    feed_incidents.c.observed_at >= since,
                    feed_incidents.c.observed_at <= now,
                )
                item["recent_incident_count"] = connection.execute(
                    select(func.count(feed_incidents.c.id)).where(incident_filter)
                ).scalar_one()
                latest_incident = connection.execute(
                    select(feed_incidents.c.incident_type, feed_incidents.c.observed_at)
                    .where(incident_filter)
                    .order_by(feed_incidents.c.observed_at.desc(), feed_incidents.c.id.desc())
                    .limit(1)
                ).one_or_none()
                item["most_recent_incident_type"] = (
                    latest_incident.incident_type if latest_incident is not None else None
                )
                item["most_recent_incident_at"] = (
                    _compat_value(connection, latest_incident.observed_at)
                    if latest_incident is not None
                    else None
                )
                rows.append(item)
            return rows

    def evaluation_rows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        _validate_limit(limit)
        statement = (
            select(
                live_predictions.c.horizon_seconds,
                live_predictions.c.calibrated_probability,
                live_prediction_evaluations.c.official_target,
                live_prediction_evaluations.c.correct,
                live_prediction_evaluations.c.calibrated_brier,
                live_prediction_evaluations.c.calibrated_log_loss,
                live_prediction_evaluations.c.hypothetical_assumed_cost_pnl,
                live_prediction_evaluations.c.evaluated_at,
            )
            .select_from(
                live_prediction_evaluations.join(
                    live_predictions,
                    live_prediction_evaluations.c.prediction_id
                    == live_predictions.c.prediction_id,
                )
            )
            .where(live_predictions.c.horizon_seconds.in_(VERIFIED_HORIZONS))
            .order_by(
                live_prediction_evaluations.c.evaluated_at.desc(),
                live_prediction_evaluations.c.id.desc(),
            )
            .limit(limit)
        )
        with self.read_only_connection() as connection:
            return [_row_dict(connection, row) for row in connection.execute(statement)]

    def _hydrate_market(
        self,
        connection: Connection,
        market: dict[str, Any],
    ) -> dict[str, Any]:
        up_state = self._latest_book_state(
            connection,
            condition_id=market["condition_id"],
            asset_id=market["up_token_id"],
        )
        down_state = self._latest_book_state(
            connection,
            condition_id=market["condition_id"],
            asset_id=market["down_token_id"],
        )
        prediction = connection.execute(
            select(live_predictions)
            .where(
                live_predictions.c.condition_id == market["condition_id"],
                live_predictions.c.horizon_seconds == market["horizon_seconds"],
            )
            .order_by(live_predictions.c.recorded_at.desc(), live_predictions.c.id.desc())
            .limit(1)
        ).one_or_none()

        market["current_up_best_bid"] = self._state_value(up_state, "best_bid")
        market["current_up_best_ask"] = self._state_value(up_state, "best_ask")
        market["current_down_best_bid"] = self._state_value(down_state, "best_bid")
        market["current_down_best_ask"] = self._state_value(down_state, "best_ask")
        market["current_up_book_at"] = up_state["last_event_at"] if up_state else None
        market["current_down_book_at"] = down_state["last_event_at"] if down_state else None
        market["prediction"] = (
            _row_dict(connection, prediction) if prediction is not None else None
        )
        return market

    @staticmethod
    def _state_value(state_row: dict[str, Any] | None, key: str) -> Any:
        if state_row is None:
            return None
        state = state_row.get("state")
        return state.get(key) if isinstance(state, dict) else None

    @staticmethod
    def _latest_book_state(
        connection: Connection,
        *,
        condition_id: str,
        asset_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            select(market_state_1s)
            .where(
                market_state_1s.c.source == "polymarket",
                market_state_1s.c.stream == "market",
                market_state_1s.c.market_id == condition_id,
                market_state_1s.c.asset_id == asset_id,
            )
            .order_by(market_state_1s.c.bucket_at.desc(), market_state_1s.c.id.desc())
            .limit(1)
        ).one_or_none()
        return _row_dict(connection, row) if row is not None else None

    @staticmethod
    def _evaluation_for_prediction(
        connection: Connection,
        prediction_id: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            select(live_prediction_evaluations)
            .where(live_prediction_evaluations.c.prediction_id == prediction_id)
            .order_by(
                live_prediction_evaluations.c.evaluated_at.desc(),
                live_prediction_evaluations.c.id.desc(),
            )
            .limit(1)
        ).one_or_none()
        return _row_dict(connection, row) if row is not None else None
