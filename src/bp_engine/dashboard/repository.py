from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, func, select

from bp_engine.storage import schema


class PostgresDashboardRepository:
    """Read-only PostgreSQL access for the Phase 11 dashboard."""

    def __init__(self, engine: Engine | str) -> None:
        self.engine = create_engine(engine) if isinstance(engine, str) else engine

    @staticmethod
    def _latest_predictions():
        predictions = schema.live_predictions
        return (
            select(
                predictions.c.prediction_id,
                predictions.c.condition_id,
                predictions.c.prediction_version,
                predictions.c.scheduled_at,
                predictions.c.recorded_at,
                predictions.c.calibrated_probability,
                predictions.c.raw_probability,
                predictions.c.predicted_side,
                predictions.c.market_probability,
                predictions.c.up_best_bid,
                predictions.c.up_best_ask,
                predictions.c.down_best_bid,
                predictions.c.down_best_ask,
                predictions.c.selected_side,
                predictions.c.selected_ask,
                predictions.c.selected_bid,
                predictions.c.selected_spread,
                predictions.c.raw_edge,
                predictions.c.cost_adjusted_edge,
                predictions.c.decision_reason,
                predictions.c.executable,
                predictions.c.trade,
                func.row_number()
                .over(
                    partition_by=predictions.c.condition_id,
                    order_by=(
                        predictions.c.scheduled_at.desc(),
                        predictions.c.recorded_at.desc(),
                        predictions.c.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .subquery("dashboard_latest_predictions")
        )

    @staticmethod
    def _latest_evaluations():
        evaluations = schema.live_prediction_evaluations
        return (
            select(
                evaluations.c.prediction_id,
                evaluations.c.label_version,
                evaluations.c.official_outcome,
                evaluations.c.official_target,
                evaluations.c.label_source,
                evaluations.c.label_source_observed_at,
                evaluations.c.evaluated_at,
                evaluations.c.correct,
                evaluations.c.raw_log_loss,
                evaluations.c.raw_brier,
                evaluations.c.calibrated_log_loss,
                evaluations.c.calibrated_brier,
                func.row_number()
                .over(
                    partition_by=evaluations.c.prediction_id,
                    order_by=(evaluations.c.evaluated_at.desc(), evaluations.c.id.desc()),
                )
                .label("row_number"),
            )
            .subquery("dashboard_latest_evaluations")
        )

    def list_active_markets(self, now: datetime) -> list[dict[str, Any]]:
        markets = schema.polymarket_markets
        latest = self._latest_predictions()
        query = (
            select(
                markets.c.condition_id,
                markets.c.slug,
                markets.c.question,
                markets.c.horizon_seconds,
                markets.c.start_at,
                markets.c.end_at,
                markets.c.accepting_orders,
                latest.c.prediction_id,
                latest.c.prediction_version,
                latest.c.scheduled_at,
                latest.c.recorded_at,
                latest.c.calibrated_probability,
                latest.c.raw_probability,
                latest.c.predicted_side,
                latest.c.market_probability,
                latest.c.up_best_bid,
                latest.c.up_best_ask,
                latest.c.down_best_bid,
                latest.c.down_best_ask,
                latest.c.selected_side,
                latest.c.selected_ask,
                latest.c.selected_bid,
                latest.c.selected_spread,
                latest.c.raw_edge,
                latest.c.cost_adjusted_edge,
                latest.c.decision_reason,
                latest.c.executable,
                latest.c.trade,
            )
            .outerjoin(
                latest,
                (latest.c.condition_id == markets.c.condition_id)
                & (latest.c.row_number == 1),
            )
            .where(
                markets.c.active.is_(True),
                markets.c.closed.is_(False),
                markets.c.end_at > now,
            )
            .order_by(markets.c.end_at.asc(), markets.c.horizon_seconds.asc())
        )
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings().all()]

    def list_feed_health(self, now: datetime) -> list[dict[str, Any]]:
        status = schema.feed_status
        query = select(status).order_by(status.c.source.asc(), status.c.stream.asc())
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(query).mappings().all()]
        for row in rows:
            last_received_at = row.get("last_received_at")
            row["age_seconds"] = (
                (now - last_received_at).total_seconds() if last_received_at is not None else None
            )
        return rows

    def list_predictions(self, limit: int = 100) -> list[dict[str, Any]]:
        predictions = schema.live_predictions
        latest_evaluations = self._latest_evaluations()
        query = (
            select(
                predictions.c.prediction_id,
                predictions.c.condition_id,
                predictions.c.slug,
                predictions.c.horizon_seconds,
                predictions.c.prediction_version,
                predictions.c.scheduled_at,
                predictions.c.recorded_at,
                predictions.c.calibrated_probability,
                predictions.c.raw_probability,
                predictions.c.predicted_side,
                predictions.c.market_probability,
                predictions.c.selected_side,
                predictions.c.selected_ask,
                predictions.c.selected_bid,
                predictions.c.selected_spread,
                predictions.c.raw_edge,
                predictions.c.cost_adjusted_edge,
                predictions.c.decision_reason,
                predictions.c.executable,
                predictions.c.trade,
                latest_evaluations.c.label_version,
                latest_evaluations.c.official_outcome,
                latest_evaluations.c.official_target,
                latest_evaluations.c.label_source,
                latest_evaluations.c.label_source_observed_at,
                latest_evaluations.c.evaluated_at,
                latest_evaluations.c.correct,
                latest_evaluations.c.calibrated_log_loss,
                latest_evaluations.c.calibrated_brier,
            )
            .outerjoin(
                latest_evaluations,
                (latest_evaluations.c.prediction_id == predictions.c.prediction_id)
                & (latest_evaluations.c.row_number == 1),
            )
            .order_by(
                predictions.c.scheduled_at.desc(),
                predictions.c.recorded_at.desc(),
                predictions.c.id.desc(),
            )
            .limit(max(1, min(int(limit), 500)))
        )
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings().all()]

    def list_performance_predictions(self) -> list[dict[str, Any]]:
        predictions = schema.live_predictions
        query = select(
            predictions.c.prediction_id,
            predictions.c.condition_id,
            predictions.c.horizon_seconds,
            predictions.c.calibrated_probability,
        ).order_by(predictions.c.scheduled_at.asc(), predictions.c.id.asc())
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings().all()]

    def list_evaluations(self) -> list[dict[str, Any]]:
        latest = self._latest_evaluations()
        query = (
            select(
                latest.c.prediction_id,
                latest.c.label_version,
                latest.c.official_outcome,
                latest.c.official_target,
                latest.c.label_source,
                latest.c.label_source_observed_at,
                latest.c.evaluated_at,
                latest.c.correct,
                latest.c.raw_log_loss,
                latest.c.raw_brier,
                latest.c.calibrated_log_loss,
                latest.c.calibrated_brier,
            )
            .where(latest.c.row_number == 1)
            .order_by(latest.c.evaluated_at.asc(), latest.c.prediction_id.asc())
        )
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings().all()]
