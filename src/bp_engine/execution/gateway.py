from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import Connection, select

from bp_engine.execution.models import (
    ExecutionCancelAck,
    ExecutionOrderAck,
    ExecutionOrderRequest,
)
from bp_engine.execution.paper import PaperOrderDraft, simulate_buy
from bp_engine.execution.service import (
    PaperExecutionService,
    _decimal,
    _draft_from_rows,
    _fill_record,
    _order_record,
    _terminal_record,
    _utc,
)
from bp_engine.features.hashing import canonical_hash
from bp_engine.storage import schema


class PaperExecutionGateway(PaperExecutionService):
    """Paper-only implementation of the shared execution gateway contract."""

    def _paper_order_id(self, request: ExecutionOrderRequest) -> str:
        return canonical_hash(
            {
                "prediction_id": request.prediction_id,
                "execution_version": request.execution_version,
            }
        )

    def _prediction_by_id(
        self,
        connection: Connection,
        prediction_id: str,
    ) -> Mapping[str, object] | None:
        return connection.execute(
            select(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == prediction_id
            )
        ).mappings().one_or_none()

    def _draft_for_request(
        self,
        request: ExecutionOrderRequest,
        prediction: Mapping[str, object],
    ) -> PaperOrderDraft:
        expected_config_sha256 = canonical_hash(self._config.as_mapping())
        if request.execution_version != self._config.execution_version:
            raise ValueError("execution version does not match paper gateway config")
        if request.execution_config_sha256 != expected_config_sha256:
            raise ValueError("execution config SHA-256 does not match paper gateway config")

        selected_ask = prediction.get("selected_ask")
        if selected_ask is None:
            raise ValueError("source prediction selected_ask is required")
        edge_config = prediction.get("edge_config")
        if not isinstance(edge_config, Mapping) or edge_config.get("fee_rate") is None:
            raise ValueError("source prediction edge_config fee_rate is required")

        return PaperOrderDraft(
            request=request,
            signal_selected_ask=_decimal(selected_ask),
            signal_fee_rate=_decimal(edge_config["fee_rate"]),
            signal_slippage_buffer=_decimal(prediction["slippage_buffer"]),
            execution_config=self._config.as_mapping(),
            market_end_at=_utc(prediction["market_end_at"]),
        )

    def submit_order(self, request: ExecutionOrderRequest) -> ExecutionOrderAck:
        paper_order_id = self._paper_order_id(request)
        with self._engine.begin() as connection:
            prediction = self._prediction_by_id(connection, request.prediction_id)
            if prediction is None:
                return ExecutionOrderAck(
                    order_id=paper_order_id,
                    accepted=False,
                    observed_at=request.submitted_at,
                    reason="source_prediction_missing",
                )
            try:
                draft = self._draft_for_request(request, prediction)
                result = self._repository.insert_order(connection, _order_record(draft))
            except (KeyError, TypeError, ValueError):
                return ExecutionOrderAck(
                    order_id=paper_order_id,
                    accepted=False,
                    observed_at=request.submitted_at,
                    reason="source_validation_failed",
                )

        return ExecutionOrderAck(
            order_id=paper_order_id,
            accepted=True,
            observed_at=request.submitted_at,
            reason="accepted" if result.created else "existing",
        )

    def _books_until(
        self,
        connection: Connection,
        draft: PaperOrderDraft,
        *,
        until_at: datetime,
    ) -> tuple:
        request = draft.request
        cutoff = min(_utc(until_at), request.expires_at)
        if cutoff < request.arrival_at:
            return ()

        event_times = connection.execute(
            select(schema.raw_market_events.c.received_at)
            .where(
                schema.raw_market_events.c.source == "polymarket",
                schema.raw_market_events.c.stream == "market",
                schema.raw_market_events.c.instrument == request.condition_id,
                schema.raw_market_events.c.received_at > request.arrival_at,
                schema.raw_market_events.c.received_at <= cutoff,
                schema.raw_market_events.c.event_type.in_(("book", "price_change")),
            )
            .order_by(
                schema.raw_market_events.c.received_at,
                schema.raw_market_events.c.id,
            )
        ).scalars().all()

        observed_times = [request.arrival_at]
        observed_times.extend(_utc(value) for value in event_times)
        books = []
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

    def _persist_simulation(
        self,
        connection: Connection,
        *,
        order: Mapping[str, object],
        draft: PaperOrderDraft,
        cancel_at: datetime | None,
    ) -> str:
        books = (
            self._books_until(connection, draft, until_at=cancel_at)
            if cancel_at is not None
            else self._books_for_order(connection, draft)
        )
        simulation = simulate_buy(draft, books, cancel_at=cancel_at)
        paper_order_id = str(order["paper_order_id"])
        for fill in simulation.fills:
            self._repository.insert_fill(
                connection,
                _fill_record(paper_order_id=paper_order_id, fill=fill),
            )
        self._repository.insert_terminal_event(
            connection,
            _terminal_record(
                paper_order_id=paper_order_id,
                terminal=simulation.terminal,
            ),
        )
        return simulation.terminal.status

    def cancel_order(self, order_id: str, observed_at: datetime) -> ExecutionCancelAck:
        current = _utc(observed_at)
        with self._engine.begin() as connection:
            order = connection.execute(
                select(schema.paper_orders).where(
                    schema.paper_orders.c.paper_order_id == order_id
                )
            ).mappings().one_or_none()
            if order is None:
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=False,
                    observed_at=current,
                    reason="unknown_order",
                )

            terminal = connection.execute(
                select(schema.paper_order_terminal_events).where(
                    schema.paper_order_terminal_events.c.paper_order_id == order_id
                )
            ).mappings().one_or_none()
            if terminal is not None:
                status = str(terminal["terminal_status"]).upper()
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=status == "CANCELLED",
                    observed_at=current,
                    reason=(
                        "already_cancelled"
                        if status == "CANCELLED"
                        else f"already_{status.lower()}"
                    ),
                )

            submitted_at = _utc(order["submitted_at"])
            expires_at = _utc(order["expires_at"])
            if current < submitted_at:
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=False,
                    observed_at=current,
                    reason="cancel_before_submission",
                )

            prediction = self._prediction_by_id(connection, str(order["prediction_id"]))
            if prediction is None:
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=False,
                    observed_at=current,
                    reason="source_prediction_missing",
                )
            draft = _draft_from_rows(order, prediction)

            if current >= expires_at:
                status = self._persist_simulation(
                    connection,
                    order=order,
                    draft=draft,
                    cancel_at=None,
                )
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=False,
                    observed_at=current,
                    reason=f"already_{status.lower()}",
                )

            books = self._books_until(connection, draft, until_at=current)
            simulation = simulate_buy(draft, books, cancel_at=current)
            for fill in simulation.fills:
                self._repository.insert_fill(
                    connection,
                    _fill_record(paper_order_id=order_id, fill=fill),
                )
            self._repository.insert_terminal_event(
                connection,
                _terminal_record(
                    paper_order_id=order_id,
                    terminal=simulation.terminal,
                ),
            )

            return ExecutionCancelAck(
                order_id=order_id,
                cancelled=simulation.terminal.status == "CANCELLED",
                observed_at=current,
                reason=simulation.terminal.reason,
            )
