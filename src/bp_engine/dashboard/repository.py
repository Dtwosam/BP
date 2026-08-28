from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, create_engine, func, select

from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.storage import schema

_REQUIRED_COMPACT_FEEDS = (
    ("bybit", "spot"),
    ("bybit", "linear"),
    ("coinbase", "spot"),
    ("polymarket", "market"),
)
_ZERO = Decimal("0")


def _decimal(value: object | None) -> Decimal:
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


class PostgresDashboardRepository:
    """Read-only PostgreSQL access for the research and paper-execution dashboard."""

    def __init__(self, engine: Engine | str, *, stale_after_seconds: float = 10.0) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than zero")
        self.engine = create_engine(engine) if isinstance(engine, str) else engine
        self.stale_after_seconds = float(stale_after_seconds)

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

    @staticmethod
    def _latest_compact_feed_state_query(source: str, stream: str):
        state = schema.market_state_1s
        return (
            select(
                state.c.source,
                state.c.stream,
                state.c.last_event_at.label("last_received_at"),
                state.c.bucket_at.label("updated_at"),
            )
            .where(state.c.source == source, state.c.stream == stream)
            .order_by(state.c.bucket_at.desc(), state.c.id.desc())
            .limit(1)
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
        state = schema.market_state_1s
        status_query = select(status).order_by(status.c.source.asc(), status.c.stream.asc())
        recent_cutoff = now - timedelta(seconds=max(60.0, self.stale_after_seconds * 6.0))
        recent_keys_query = (
            select(state.c.source, state.c.stream)
            .where(state.c.bucket_at >= recent_cutoff)
            .distinct()
            .order_by(state.c.source.asc(), state.c.stream.asc())
        )

        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(status_query).mappings().all()]
            recent_keys = {
                (str(row["source"]), str(row["stream"]))
                for row in connection.execute(recent_keys_query).mappings().all()
            }
            known = {(str(row["source"]), str(row["stream"])) for row in rows}
            targets = set(_REQUIRED_COMPACT_FEEDS) | recent_keys
            compact_rows = []
            for source, stream in sorted(targets - known):
                compact = connection.execute(
                    self._latest_compact_feed_state_query(source, stream)
                ).mappings().first()
                if compact is not None:
                    compact_rows.append(dict(compact))

        for compact in compact_rows:
            last_received_at = compact.get("last_received_at")
            age_seconds = (
                (now - last_received_at).total_seconds() if last_received_at is not None else None
            )
            rows.append(
                {
                    "source": compact["source"],
                    "stream": compact["stream"],
                    "status": (
                        "connected"
                        if age_seconds is not None and age_seconds <= self.stale_after_seconds
                        else "stale"
                    ),
                    "last_received_at": last_received_at,
                    "last_source_timestamp": None,
                    "updated_at": compact.get("updated_at"),
                    "details": {"derived_from": "market_state_1s"},
                    "age_seconds": age_seconds,
                }
            )

        for row in rows:
            if "age_seconds" in row:
                continue
            last_received_at = row.get("last_received_at")
            row["age_seconds"] = (
                (now - last_received_at).total_seconds() if last_received_at is not None else None
            )
        return sorted(rows, key=lambda row: (str(row["source"]), str(row["stream"])))

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

    def get_paper_execution_evidence(self, *, history_limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(int(history_limit), 500))
        starting_cash = PaperExecutionConfig().starting_cash_usd

        with self.engine.connect() as connection:
            predictions = [
                dict(row)
                for row in connection.execute(
                    select(
                        schema.live_predictions.c.prediction_id,
                        schema.live_predictions.c.semantic_sha256,
                        schema.live_predictions.c.recorded_at,
                        schema.live_predictions.c.up_token_id,
                        schema.live_predictions.c.down_token_id,
                        schema.live_predictions.c.selected_side,
                        schema.live_predictions.c.selected_ask,
                        schema.live_predictions.c.executable,
                        schema.live_predictions.c.trade,
                    )
                ).mappings().all()
            ]
            orders = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_orders).order_by(
                        schema.paper_orders.c.submitted_at.asc(),
                        schema.paper_orders.c.id.asc(),
                    )
                ).mappings().all()
            ]
            fills = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_fills).order_by(
                        schema.paper_fills.c.fill_at.asc(),
                        schema.paper_fills.c.id.asc(),
                    )
                ).mappings().all()
            ]
            terminals = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_order_terminal_events).order_by(
                        schema.paper_order_terminal_events.c.event_at.asc(),
                        schema.paper_order_terminal_events.c.id.asc(),
                    )
                ).mappings().all()
            ]
            settlements = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_settlements).order_by(
                        schema.paper_settlements.c.settled_at.asc(),
                        schema.paper_settlements.c.id.asc(),
                    )
                ).mappings().all()
            ]

        prediction_by_id = {str(row["prediction_id"]): row for row in predictions}
        order_by_id = {str(row["paper_order_id"]): row for row in orders}
        terminal_by_order = {str(row["paper_order_id"]): row for row in terminals}
        settlement_by_order = {str(row["paper_order_id"]): row for row in settlements}
        fills_by_order: dict[str, list[dict[str, Any]]] = {}
        for fill in fills:
            fills_by_order.setdefault(str(fill["paper_order_id"]), []).append(fill)

        violation_counts = {
            "missing_source_prediction": 0,
            "prediction_semantic_mismatch": 0,
            "ineligible_source_signal": 0,
            "side_mismatch": 0,
            "token_mismatch": 0,
            "signal_ask_mismatch": 0,
            "order_time_mismatch": 0,
            "orphan_fill": 0,
            "fill_quantity_exceeded": 0,
            "fill_price_limit_exceeded": 0,
            "fill_time_violation": 0,
            "fill_slippage_mismatch": 0,
            "terminal_quantity_mismatch": 0,
            "orphan_terminal": 0,
            "orphan_settlement": 0,
            "settlement_math_mismatch": 0,
            "negative_cash": 0,
        }

        for order in orders:
            prediction = prediction_by_id.get(str(order["prediction_id"]))
            if prediction is None:
                violation_counts["missing_source_prediction"] += 1
                continue
            if str(order["prediction_semantic_sha256"]) != str(
                prediction["semantic_sha256"]
            ):
                violation_counts["prediction_semantic_mismatch"] += 1
            if prediction["trade"] is not True or prediction["executable"] is not True:
                violation_counts["ineligible_source_signal"] += 1
            selected_side = str(prediction.get("selected_side") or "").lower()
            if str(order["selected_side"]).lower() != selected_side:
                violation_counts["side_mismatch"] += 1
            expected_token = (
                prediction.get("up_token_id")
                if selected_side == "up"
                else prediction.get("down_token_id")
            )
            if str(order["token_id"]) != str(expected_token):
                violation_counts["token_mismatch"] += 1
            if _decimal(order["signal_selected_ask"]) != _decimal(
                prediction.get("selected_ask")
            ):
                violation_counts["signal_ask_mismatch"] += 1
            if order["submitted_at"] != prediction["recorded_at"]:
                violation_counts["order_time_mismatch"] += 1

        for fill in fills:
            order = order_by_id.get(str(fill["paper_order_id"]))
            if order is None:
                violation_counts["orphan_fill"] += 1
                continue
            if _decimal(fill["price"]) > _decimal(order["limit_price"]):
                violation_counts["fill_price_limit_exceeded"] += 1
            fill_at = fill["fill_at"]
            if fill_at < order["arrival_at"] or fill_at > order["expires_at"]:
                violation_counts["fill_time_violation"] += 1
            terminal = terminal_by_order.get(str(order["paper_order_id"]))
            if (
                terminal is not None
                and terminal["terminal_status"] == "CANCELLED"
                and fill_at > terminal["event_at"]
            ):
                violation_counts["fill_time_violation"] += 1
            expected_slippage = _decimal(fill["price"]) - _decimal(
                order["signal_selected_ask"]
            )
            if _decimal(fill["signal_ask_slippage"]) != expected_slippage:
                violation_counts["fill_slippage_mismatch"] += 1

        for order_id, order in order_by_id.items():
            order_fills = fills_by_order.get(order_id, [])
            filled_shares = sum((_decimal(row["shares"]) for row in order_fills), _ZERO)
            requested_shares = _decimal(order["requested_shares"])
            if filled_shares > requested_shares:
                violation_counts["fill_quantity_exceeded"] += 1
            terminal = terminal_by_order.get(order_id)
            if terminal is not None:
                expected_remaining = requested_shares - filled_shares
                if _decimal(terminal["remaining_shares"]) != expected_remaining:
                    violation_counts["terminal_quantity_mismatch"] += 1

        for terminal in terminals:
            if str(terminal["paper_order_id"]) not in order_by_id:
                violation_counts["orphan_terminal"] += 1

        for settlement in settlements:
            order_id = str(settlement["paper_order_id"])
            order = order_by_id.get(order_id)
            if order is None:
                violation_counts["orphan_settlement"] += 1
                continue
            order_fills = fills_by_order.get(order_id, [])
            filled_shares = sum((_decimal(row["shares"]) for row in order_fills), _ZERO)
            fill_cost = sum((_decimal(row["total_cost"]) for row in order_fills), _ZERO)
            fees = sum((_decimal(row["fee"]) for row in order_fills), _ZERO)
            payout = _decimal(settlement["payout"])
            if (
                _decimal(settlement["filled_shares"]) != filled_shares
                or _decimal(settlement["total_fill_cost"]) != fill_cost
                or _decimal(settlement["total_fees"]) != fees
                or _decimal(settlement["realized_pnl"]) != payout - fill_cost
            ):
                violation_counts["settlement_math_mismatch"] += 1

        total_fill_cost = sum((_decimal(row["total_cost"]) for row in fills), _ZERO)
        total_payout = sum((_decimal(row["payout"]) for row in settlements), _ZERO)
        current_cash = starting_cash - total_fill_cost + total_payout
        if current_cash < _ZERO:
            violation_counts["negative_cash"] += 1

        settled_order_ids = set(settlement_by_order)
        filled_order_ids = {
            order_id for order_id, rows in fills_by_order.items() if len(rows) > 0
        }
        open_order_ids = filled_order_ids - settled_order_ids
        open_capital = sum(
            (
                _decimal(fill["total_cost"])
                for order_id in open_order_ids
                for fill in fills_by_order.get(order_id, [])
            ),
            _ZERO,
        )
        realized_pnl = sum((_decimal(row["realized_pnl"]) for row in settlements), _ZERO)
        total_fees = sum((_decimal(row["fee"]) for row in fills), _ZERO)
        total_slippage_cost = sum(
            (_decimal(row["shares"]) * _decimal(row["signal_ask_slippage"]) for row in fills),
            _ZERO,
        )
        no_fill_expired_count = sum(
            1
            for terminal in terminals
            if terminal["terminal_status"] in {"EXPIRED", "MARKET_ENDED_UNFILLED"}
            and not fills_by_order.get(str(terminal["paper_order_id"]))
        )

        equity = starting_cash
        peak_equity = starting_cash
        max_drawdown = _ZERO
        for settlement in settlements:
            equity += _decimal(settlement["realized_pnl"])
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

        violation_count = sum(violation_counts.values())
        reconciliation = {
            "status": "OK" if violation_count == 0 else "VIOLATION",
            "violation_count": violation_count,
            "violations": violation_counts,
            "paper_order_count": len(orders),
            "trade_signal_count": sum(1 for row in predictions if row["trade"] is True),
            "no_trade_signal_count": sum(1 for row in predictions if row["trade"] is False),
        }
        paper_pnl = {
            "status": "AVAILABLE",
            "starting_cash": starting_cash,
            "current_cash": current_cash,
            "open_capital": open_capital,
            "unrealized_value": None,
            "realized_pnl": realized_pnl,
            "return_on_starting_cash": realized_pnl / starting_cash,
            "max_realized_equity_drawdown": max_drawdown,
            "settled_trade_count": len(settled_order_ids),
            "open_position_count": len(open_order_ids),
            "fill_count": len(fills),
            "no_fill_expired_count": no_fill_expired_count,
            "total_fees": total_fees,
            "total_slippage_cost": total_slippage_cost,
            "reconciliation": reconciliation,
        }

        order_history = []
        for row in reversed(orders[-limit:]):
            item = dict(row)
            terminal = terminal_by_order.get(str(row["paper_order_id"]))
            settlement = settlement_by_order.get(str(row["paper_order_id"]))
            item["terminal_status"] = terminal.get("terminal_status") if terminal else None
            item["remaining_shares"] = terminal.get("remaining_shares") if terminal else None
            item["terminal_event_at"] = terminal.get("event_at") if terminal else None
            item["realized_pnl"] = settlement.get("realized_pnl") if settlement else None
            order_history.append(item)

        return {
            "paper_pnl": paper_pnl,
            "paper_orders": order_history,
            "paper_fills": list(reversed(fills[-limit:])),
            "paper_settlements": list(reversed(settlements[-limit:])),
        }
