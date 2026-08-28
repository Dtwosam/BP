from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bp_engine.execution.models import (
    PaperFillRecord,
    PaperOrderRecord,
    PaperOrderTerminalEventRecord,
    PaperSettlementRecord,
)
from bp_engine.storage import schema


class PaperLedgerConflictError(RuntimeError):
    """Raised when an append-only paper ledger natural key would be rewritten."""


@dataclass(frozen=True)
class PaperLedgerStoreResult:
    created: bool
    existing: bool


def _normalized(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, Mapping):
        return {str(key): _normalized(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalized(item) for item in value]
    return value


def _record_values(record: object) -> dict[str, Any]:
    values = asdict(record)
    for name in ("book_applied_event_ids", "book_applied_dedupe_keys"):
        if name in values:
            values[name] = list(values[name])
    if "execution_config" in values:
        values["execution_config"] = dict(values["execution_config"])
    return values


def _semantically_equal(row: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    return all(_normalized(row[name]) == _normalized(value) for name, value in values.items())


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class PaperExecutionRepository:
    def _store(
        self,
        connection: Connection,
        *,
        table: Any,
        record: object,
        natural_key: Mapping[str, object],
        conflict_target: Sequence[str],
        description: str,
    ) -> PaperLedgerStoreResult:
        values = _record_values(record)
        predicate = [table.c[name] == value for name, value in natural_key.items()]
        existing = connection.execute(select(table).where(*predicate)).mappings().one_or_none()
        if existing is not None:
            if not _semantically_equal(existing, values):
                raise PaperLedgerConflictError(f"conflicting {description}")
            return PaperLedgerStoreResult(created=False, existing=True)

        statement = (
            pg_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[table.c[name] for name in conflict_target])
        )
        result = connection.execute(statement)
        stored = connection.execute(select(table).where(*predicate)).mappings().one_or_none()
        if stored is None or not _semantically_equal(stored, values):
            raise PaperLedgerConflictError(f"conflicting {description}")
        created = result.rowcount == 1
        return PaperLedgerStoreResult(created=created, existing=not created)

    def _require_order(self, connection: Connection, paper_order_id: str) -> Mapping[str, Any]:
        row = connection.execute(
            select(schema.paper_orders).where(
                schema.paper_orders.c.paper_order_id == paper_order_id
            )
        ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"paper order does not exist: {paper_order_id}")
        return row

    def _validate_source_prediction(
        self,
        connection: Connection,
        order: PaperOrderRecord,
    ) -> None:
        source = connection.execute(
            select(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == order.prediction_id
            )
        ).mappings().one_or_none()
        if source is None:
            raise ValueError(f"source prediction does not exist: {order.prediction_id}")
        if source["semantic_sha256"] != order.prediction_semantic_sha256:
            raise ValueError("source prediction semantic SHA-256 mismatch")
        if not bool(source["trade"]) or not bool(source["executable"]):
            raise ValueError("paper order requires trade=true and executable=true")
        if source["condition_id"] != order.condition_id:
            raise ValueError("paper order condition does not match source prediction")
        if str(source["selected_side"]).lower() != order.selected_side:
            raise ValueError("paper order side does not match source prediction")
        token_column = "up_token_id" if order.selected_side == "up" else "down_token_id"
        if source[token_column] != order.token_id:
            raise ValueError("paper order token does not match selected source side")
        selected_ask = source["selected_ask"]
        if selected_ask is None or _decimal(selected_ask) != order.signal_selected_ask:
            raise ValueError("paper order signal ask does not match source prediction")
        if _decimal(source["slippage_buffer"]) != order.signal_slippage_buffer:
            raise ValueError("paper order slippage does not match source prediction")
        edge_config = source["edge_config"]
        if not isinstance(edge_config, Mapping) or "fee_rate" not in edge_config:
            raise ValueError("source prediction edge config is missing fee_rate")
        if _decimal(edge_config["fee_rate"]) != order.signal_fee_rate:
            raise ValueError("paper order fee rate does not match source prediction")

    def insert_order(
        self,
        connection: Connection,
        order: PaperOrderRecord,
    ) -> PaperLedgerStoreResult:
        self._validate_source_prediction(connection, order)
        by_id = connection.execute(
            select(schema.paper_orders).where(
                schema.paper_orders.c.paper_order_id == order.paper_order_id
            )
        ).mappings().one_or_none()
        if by_id is not None:
            values = _record_values(order)
            if not _semantically_equal(by_id, values):
                raise PaperLedgerConflictError(
                    f"conflicting paper order id={order.paper_order_id}"
                )
            return PaperLedgerStoreResult(created=False, existing=True)
        return self._store(
            connection,
            table=schema.paper_orders,
            record=order,
            natural_key={
                "prediction_id": order.prediction_id,
                "execution_version": order.execution_version,
            },
            conflict_target=("prediction_id", "execution_version"),
            description=(
                "paper order "
                f"prediction_id={order.prediction_id} "
                f"execution_version={order.execution_version}"
            ),
        )

    def insert_fill(
        self,
        connection: Connection,
        fill: PaperFillRecord,
    ) -> PaperLedgerStoreResult:
        self._require_order(connection, fill.paper_order_id)
        return self._store(
            connection,
            table=schema.paper_fills,
            record=fill,
            natural_key={
                "paper_order_id": fill.paper_order_id,
                "fill_key": fill.fill_key,
            },
            conflict_target=("paper_order_id", "fill_key"),
            description=(
                f"paper fill order_id={fill.paper_order_id} fill_key={fill.fill_key}"
            ),
        )

    def insert_terminal_event(
        self,
        connection: Connection,
        event: PaperOrderTerminalEventRecord,
    ) -> PaperLedgerStoreResult:
        self._require_order(connection, event.paper_order_id)
        return self._store(
            connection,
            table=schema.paper_order_terminal_events,
            record=event,
            natural_key={"paper_order_id": event.paper_order_id},
            conflict_target=("paper_order_id",),
            description=f"paper terminal event order_id={event.paper_order_id}",
        )

    def insert_settlement(
        self,
        connection: Connection,
        settlement: PaperSettlementRecord,
    ) -> PaperLedgerStoreResult:
        self._require_order(connection, settlement.paper_order_id)
        return self._store(
            connection,
            table=schema.paper_settlements,
            record=settlement,
            natural_key={
                "paper_order_id": settlement.paper_order_id,
                "label_version": settlement.label_version,
            },
            conflict_target=("paper_order_id", "label_version"),
            description=(
                "paper settlement "
                f"order_id={settlement.paper_order_id} "
                f"label_version={settlement.label_version}"
            ),
        )
