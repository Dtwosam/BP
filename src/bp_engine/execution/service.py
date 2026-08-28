from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, select

from bp_engine.execution.book import (
    BookReplayError,
    PolymarketBookReplayReader,
    ReplayedBook,
)
from bp_engine.execution.models import (
    ExecutionOrderRequest,
    PaperExecutionConfig,
    PaperFillRecord,
    PaperOrderRecord,
    PaperOrderTerminalEventRecord,
    PaperSettlementRecord,
)
from bp_engine.execution.paper import (
    PaperFillDraft,
    PaperOrderDraft,
    PaperTerminalDraft,
    build_paper_order,
    simulate_buy,
)
from bp_engine.execution.repository import PaperExecutionRepository
from bp_engine.features.hashing import canonical_hash
from bp_engine.storage import schema

_ZERO = Decimal("0")


class PaperCashInvariantError(RuntimeError):
    """Raised when the derived paper account would violate cash solvency."""


@dataclass(frozen=True)
class PaperRunReport:
    examined_predictions: int
    skipped_predictions: int
    created_orders: int
    existing_orders: int
    created_fills: int
    existing_fills: int
    created_terminal_events: int
    existing_terminal_events: int
    created_settlements: int
    existing_settlements: int
    current_cash: Decimal


def _decimal(value: object) -> Decimal:
    numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    if not numeric.is_finite():
        raise ValueError("paper numeric value must be finite")
    return numeric


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def derive_paper_cash(
    *,
    starting_cash: Decimal,
    fill_costs: Iterable[Decimal],
    settlement_payouts: Iterable[Decimal],
) -> Decimal:
    starting = _decimal(starting_cash)
    spent = sum((_decimal(value) for value in fill_costs), _ZERO)
    paid = sum((_decimal(value) for value in settlement_payouts), _ZERO)
    cash = starting - spent + paid
    if cash < _ZERO:
        raise PaperCashInvariantError("derived paper cash is negative")
    return cash


def settlement_payout(
    filled_shares: Decimal,
    *,
    selected_side: str,
    official_outcome: str,
) -> Decimal:
    shares = _decimal(filled_shares)
    if shares < _ZERO:
        raise ValueError("filled_shares must be non-negative")
    side = str(selected_side).lower()
    outcome = str(official_outcome).lower()
    if side not in {"up", "down"}:
        raise ValueError("selected_side must be up or down")
    if outcome not in {"up", "down"}:
        raise ValueError("official_outcome must be Up or Down")
    return shares if side == outcome else _ZERO


def _order_record(draft: PaperOrderDraft) -> PaperOrderRecord:
    request = draft.request
    paper_order_id = canonical_hash(
        {
            "prediction_id": request.prediction_id,
            "execution_version": request.execution_version,
        }
    )
    values: dict[str, Any] = {
        "paper_order_id": paper_order_id,
        **request.as_mapping(raw=True),
        "signal_selected_ask": draft.signal_selected_ask,
        "signal_fee_rate": draft.signal_fee_rate,
        "signal_slippage_buffer": draft.signal_slippage_buffer,
        "execution_config": dict(draft.execution_config),
    }
    semantic_sha256 = canonical_hash(values)
    return PaperOrderRecord(
        paper_order_id=paper_order_id,
        prediction_id=request.prediction_id,
        prediction_semantic_sha256=request.prediction_semantic_sha256,
        execution_version=request.execution_version,
        execution_config_sha256=request.execution_config_sha256,
        condition_id=request.condition_id,
        token_id=request.token_id,
        selected_side=request.selected_side,
        requested_shares=request.requested_shares,
        target_notional_usd=request.target_notional_usd,
        submitted_at=request.submitted_at,
        arrival_at=request.arrival_at,
        expires_at=request.expires_at,
        limit_price=request.limit_price,
        signal_selected_ask=draft.signal_selected_ask,
        signal_fee_rate=draft.signal_fee_rate,
        signal_slippage_buffer=draft.signal_slippage_buffer,
        execution_config=dict(draft.execution_config),
        semantic_sha256=semantic_sha256,
        created_at=request.submitted_at,
    )


def _draft_from_rows(
    order: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> PaperOrderDraft:
    request = ExecutionOrderRequest(
        prediction_id=str(order["prediction_id"]),
        prediction_semantic_sha256=str(order["prediction_semantic_sha256"]),
        condition_id=str(order["condition_id"]),
        token_id=str(order["token_id"]),
        selected_side=str(order["selected_side"]),
        action="BUY",
        requested_shares=_decimal(order["requested_shares"]),
        target_notional_usd=_decimal(order["target_notional_usd"]),
        submitted_at=_utc(order["submitted_at"]),
        arrival_at=_utc(order["arrival_at"]),
        expires_at=_utc(order["expires_at"]),
        limit_price=_decimal(order["limit_price"]),
        execution_version=str(order["execution_version"]),
        execution_config_sha256=str(order["execution_config_sha256"]),
    )
    return PaperOrderDraft(
        request=request,
        signal_selected_ask=_decimal(order["signal_selected_ask"]),
        signal_fee_rate=_decimal(order["signal_fee_rate"]),
        signal_slippage_buffer=_decimal(order["signal_slippage_buffer"]),
        execution_config=dict(order["execution_config"]),
        market_end_at=_utc(prediction["market_end_at"]),
    )


def _fill_record(
    *,
    paper_order_id: str,
    fill: PaperFillDraft,
) -> PaperFillRecord:
    fill_values: dict[str, Any] = {
        "paper_order_id": paper_order_id,
        "fill_at": fill.fill_at,
        "shares": fill.shares,
        "price": fill.price,
        "gross_cost": fill.gross_cost,
        "fee": fill.fee,
        "total_cost": fill.total_cost,
        "signal_ask_slippage": fill.signal_ask_slippage,
        "book_anchor_event_id": fill.book_anchor_event_id,
        "book_anchor_dedupe_key": fill.book_anchor_dedupe_key,
        "book_applied_event_ids": fill.book_applied_event_ids,
        "book_applied_dedupe_keys": fill.book_applied_dedupe_keys,
        "replay_cutoff_at": fill.replay_cutoff_at,
    }
    fill_key = canonical_hash(fill_values)
    semantic_sha256 = canonical_hash({**fill_values, "fill_key": fill_key})
    return PaperFillRecord(
        paper_order_id=paper_order_id,
        fill_key=fill_key,
        fill_at=fill.fill_at,
        shares=fill.shares,
        price=fill.price,
        gross_cost=fill.gross_cost,
        fee=fill.fee,
        total_cost=fill.total_cost,
        signal_ask_slippage=fill.signal_ask_slippage,
        book_anchor_event_id=fill.book_anchor_event_id,
        book_anchor_dedupe_key=fill.book_anchor_dedupe_key,
        book_applied_event_ids=fill.book_applied_event_ids,
        book_applied_dedupe_keys=fill.book_applied_dedupe_keys,
        replay_cutoff_at=fill.replay_cutoff_at,
        semantic_sha256=semantic_sha256,
        created_at=fill.fill_at,
    )


def _terminal_record(
    *,
    paper_order_id: str,
    terminal: PaperTerminalDraft,
) -> PaperOrderTerminalEventRecord:
    if terminal.event_at is None:
        raise ValueError("persisted paper terminal event requires event_at")
    values = {
        "paper_order_id": paper_order_id,
        "terminal_status": terminal.status,
        "remaining_shares": terminal.remaining_shares,
        "event_at": terminal.event_at,
        "reason": terminal.reason,
    }
    return PaperOrderTerminalEventRecord(
        **values,
        semantic_sha256=canonical_hash(values),
        created_at=terminal.event_at,
    )


def _settlement_record(
    *,
    order: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    fills: tuple[Mapping[str, Any], ...],
) -> PaperSettlementRecord:
    filled_shares = sum((_decimal(row["shares"]) for row in fills), _ZERO)
    total_fill_cost = sum((_decimal(row["total_cost"]) for row in fills), _ZERO)
    total_fees = sum((_decimal(row["fee"]) for row in fills), _ZERO)
    payout = settlement_payout(
        filled_shares,
        selected_side=str(order["selected_side"]),
        official_outcome=str(evaluation["official_outcome"]),
    )
    realized_pnl = payout - total_fill_cost
    settled_at = _utc(evaluation["evaluated_at"])
    values: dict[str, Any] = {
        "paper_order_id": str(order["paper_order_id"]),
        "label_version": str(evaluation["label_version"]),
        "official_outcome": str(evaluation["official_outcome"]),
        "official_target": int(evaluation["official_target"]),
        "label_source": str(evaluation["label_source"]),
        "label_source_snapshot_sha256": str(evaluation["label_source_snapshot_sha256"]),
        "label_source_observed_at": _utc(evaluation["label_source_observed_at"]),
        "filled_shares": filled_shares,
        "total_fill_cost": total_fill_cost,
        "total_fees": total_fees,
        "payout": payout,
        "realized_pnl": realized_pnl,
        "settled_at": settled_at,
    }
    return PaperSettlementRecord(
        **values,
        semantic_sha256=canonical_hash(values),
        created_at=settled_at,
    )


class PaperExecutionService:
    """Process immutable live signals into deterministic, paper-only execution ledgers."""

    def __init__(
        self,
        *,
        engine: Engine,
        config: PaperExecutionConfig | None = None,
        repository: PaperExecutionRepository | None = None,
        book_reader: PolymarketBookReplayReader | None = None,
    ) -> None:
        self._engine = engine
        self._config = config or PaperExecutionConfig()
        self._repository = repository or PaperExecutionRepository()
        self._book_reader = book_reader or PolymarketBookReplayReader()

    def _current_cash(self, connection: Connection) -> Decimal:
        fill_costs = connection.execute(select(schema.paper_fills.c.total_cost)).scalars().all()
        payouts = connection.execute(select(schema.paper_settlements.c.payout)).scalars().all()
        return derive_paper_cash(
            starting_cash=self._config.starting_cash_usd,
            fill_costs=(_decimal(value) for value in fill_costs),
            settlement_payouts=(_decimal(value) for value in payouts),
        )

    def _order_for_prediction(
        self,
        connection: Connection,
        prediction_id: str,
    ) -> Mapping[str, Any] | None:
        return connection.execute(
            select(schema.paper_orders).where(
                schema.paper_orders.c.prediction_id == prediction_id,
                schema.paper_orders.c.execution_version == self._config.execution_version,
            )
        ).mappings().one_or_none()

    def _books_for_order(
        self,
        connection: Connection,
        draft: PaperOrderDraft,
    ) -> tuple[ReplayedBook, ...]:
        request = draft.request
        event_times = connection.execute(
            select(schema.raw_market_events.c.received_at)
            .where(
                schema.raw_market_events.c.source == "polymarket",
                schema.raw_market_events.c.stream == "market",
                schema.raw_market_events.c.instrument == request.condition_id,
                schema.raw_market_events.c.received_at > request.arrival_at,
                schema.raw_market_events.c.received_at <= request.expires_at,
                schema.raw_market_events.c.event_type.in_(("book", "price_change")),
            )
            .order_by(schema.raw_market_events.c.received_at, schema.raw_market_events.c.id)
        ).scalars().all()
        observed_times = [request.arrival_at]
        observed_times.extend(_utc(value) for value in event_times)

        books: list[ReplayedBook] = []
        previous_cutoff: datetime | None = None
        for observed_at in observed_times:
            if previous_cutoff is not None and observed_at == previous_cutoff:
                continue
            book = self._book_reader.book_at(
                connection,
                condition_id=request.condition_id,
                asset_id=request.token_id,
                observed_at=observed_at,
            )
            previous_cutoff = observed_at
            if book is not None:
                books.append(book)
        return tuple(books)

    def _process_order(
        self,
        connection: Connection,
        *,
        order: Mapping[str, Any],
        prediction: Mapping[str, Any],
        now: datetime,
    ) -> tuple[int, int, int, int]:
        terminal = connection.execute(
            select(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().one_or_none()
        if terminal is not None:
            return 0, 0, 0, 1
        if now < _utc(order["expires_at"]):
            return 0, 0, 0, 0

        draft = _draft_from_rows(order, prediction)
        try:
            books = self._books_for_order(connection, draft)
        except BookReplayError as exc:
            terminal_result = self._repository.insert_terminal_event(
                connection,
                _terminal_record(
                    paper_order_id=str(order["paper_order_id"]),
                    terminal=PaperTerminalDraft(
                        status="EXPIRED",
                        remaining_shares=draft.request.requested_shares,
                        event_at=draft.request.expires_at,
                        reason=f"causal book replay failed closed: {exc}"[:128],
                    ),
                ),
            )
            return (
                0,
                0,
                int(terminal_result.created),
                int(terminal_result.existing),
            )

        simulation = simulate_buy(draft, books)
        created_fills = 0
        existing_fills = 0
        for fill in simulation.fills:
            result = self._repository.insert_fill(
                connection,
                _fill_record(
                    paper_order_id=str(order["paper_order_id"]),
                    fill=fill,
                ),
            )
            created_fills += int(result.created)
            existing_fills += int(result.existing)

        terminal_result = self._repository.insert_terminal_event(
            connection,
            _terminal_record(
                paper_order_id=str(order["paper_order_id"]),
                terminal=simulation.terminal,
            ),
        )
        return (
            created_fills,
            existing_fills,
            int(terminal_result.created),
            int(terminal_result.existing),
        )

    def _settle_ready(
        self,
        connection: Connection,
        *,
        now: datetime,
    ) -> tuple[int, int]:
        created = 0
        existing = 0
        orders = connection.execute(
            select(schema.paper_orders).order_by(schema.paper_orders.c.submitted_at, schema.paper_orders.c.id)
        ).mappings().all()
        for order in orders:
            terminal = connection.execute(
                select(schema.paper_order_terminal_events.c.id).where(
                    schema.paper_order_terminal_events.c.paper_order_id == order["paper_order_id"]
                )
            ).scalar_one_or_none()
            if terminal is None:
                continue
            evaluation = connection.execute(
                select(schema.live_prediction_evaluations)
                .where(
                    schema.live_prediction_evaluations.c.prediction_id == order["prediction_id"],
                    schema.live_prediction_evaluations.c.evaluated_at <= now,
                )
                .order_by(
                    schema.live_prediction_evaluations.c.evaluated_at.desc(),
                    schema.live_prediction_evaluations.c.id.desc(),
                )
                .limit(1)
            ).mappings().one_or_none()
            if evaluation is None:
                continue
            prior = connection.execute(
                select(schema.paper_settlements.c.id).where(
                    schema.paper_settlements.c.paper_order_id == order["paper_order_id"],
                    schema.paper_settlements.c.label_version == evaluation["label_version"],
                )
            ).scalar_one_or_none()
            if prior is not None:
                existing += 1
                continue
            fills = tuple(
                connection.execute(
                    select(schema.paper_fills)
                    .where(schema.paper_fills.c.paper_order_id == order["paper_order_id"])
                    .order_by(schema.paper_fills.c.fill_at, schema.paper_fills.c.id)
                ).mappings().all()
            )
            if not fills:
                continue
            result = self._repository.insert_settlement(
                connection,
                _settlement_record(order=order, evaluation=evaluation, fills=fills),
            )
            created += int(result.created)
            existing += int(result.existing)
        return created, existing

    def run_once(self, *, now: datetime) -> PaperRunReport:
        current_now = _utc(now)
        examined = 0
        skipped = 0
        created_orders = 0
        existing_orders = 0
        created_fills = 0
        existing_fills = 0
        created_terminals = 0
        existing_terminals = 0
        created_settlements = 0
        existing_settlements = 0

        with self._engine.begin() as connection:
            first_created, first_existing = self._settle_ready(connection, now=current_now)
            created_settlements += first_created
            existing_settlements += first_existing
            available_cash = self._current_cash(connection)

            predictions = connection.execute(
                select(schema.live_predictions)
                .where(schema.live_predictions.c.recorded_at <= current_now)
                .order_by(schema.live_predictions.c.recorded_at, schema.live_predictions.c.id)
            ).mappings().all()
            for prediction in predictions:
                examined += 1
                order = self._order_for_prediction(connection, str(prediction["prediction_id"]))
                if order is None:
                    draft_or_terminal = build_paper_order(
                        prediction,
                        self._config,
                        available_cash,
                    )
                    if isinstance(draft_or_terminal, PaperTerminalDraft):
                        skipped += 1
                        continue
                    order_result = self._repository.insert_order(
                        connection,
                        _order_record(draft_or_terminal),
                    )
                    created_orders += int(order_result.created)
                    existing_orders += int(order_result.existing)
                    order = self._order_for_prediction(
                        connection,
                        str(prediction["prediction_id"]),
                    )
                    if order is None:
                        raise RuntimeError("paper order insert did not become visible")
                else:
                    existing_orders += 1

                new_fills, old_fills, new_terminal, old_terminal = self._process_order(
                    connection,
                    order=order,
                    prediction=prediction,
                    now=current_now,
                )
                created_fills += new_fills
                existing_fills += old_fills
                created_terminals += new_terminal
                existing_terminals += old_terminal
                available_cash = self._current_cash(connection)

            second_created, second_existing = self._settle_ready(connection, now=current_now)
            created_settlements += second_created
            existing_settlements += second_existing
            current_cash = self._current_cash(connection)

        return PaperRunReport(
            examined_predictions=examined,
            skipped_predictions=skipped,
            created_orders=created_orders,
            existing_orders=existing_orders,
            created_fills=created_fills,
            existing_fills=existing_fills,
            created_terminal_events=created_terminals,
            existing_terminal_events=existing_terminals,
            created_settlements=created_settlements,
            existing_settlements=existing_settlements,
            current_cash=current_cash,
        )
