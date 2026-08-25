from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, insert, select

from bp_engine.labels.models import MarketLabel
from bp_engine.storage.schema import market_labels


class LabelConflict(RuntimeError):
    """Raised when an existing semantic label would be rewritten."""


@dataclass(frozen=True)
class LabelStoreResult:
    created: bool


class MarketLabelRepository:
    def store(self, connection: Connection, label: MarketLabel) -> LabelStoreResult:
        self._validate(label)
        existing = connection.execute(
            select(market_labels).where(
                market_labels.c.condition_id == label.condition_id,
                market_labels.c.label_version == label.label_version,
            )
        ).mappings().one_or_none()

        if existing is None:
            connection.execute(insert(market_labels).values(**label.__dict__))
            return LabelStoreResult(created=True)

        expected = self._semantic_values(label.__dict__)
        actual = self._semantic_values(dict(existing))
        if actual != expected:
            raise LabelConflict(
                "conflicting label for "
                f"condition={label.condition_id} version={label.label_version}"
            )
        return LabelStoreResult(created=False)

    @staticmethod
    def _validate(label: MarketLabel) -> None:
        for name in (
            "market_start_at",
            "market_end_at",
            "source_observed_at",
            "generated_at",
        ):
            value = getattr(label, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if label.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if label.market_end_at <= label.market_start_at:
            raise ValueError("market_end_at must be after market_start_at")
        if label.source_observed_at < label.market_end_at:
            raise ValueError("source_observed_at must be at or after market_end_at")
        if label.official_outcome not in {"Up", "Down"}:
            raise ValueError("official_outcome must be Up or Down")

    @classmethod
    def _semantic_values(cls, values: dict[str, Any]) -> tuple[Any, ...]:
        return (
            values["gamma_market_id"],
            values["slug"],
            int(values["horizon_seconds"]),
            cls._normalize_datetime(values["market_start_at"]),
            cls._normalize_datetime(values["market_end_at"]),
            values["official_outcome"],
            cls._normalize_decimal(values["start_reference"]),
            cls._normalize_decimal(values["end_reference"]),
            values["resolution_source"],
            values["rules_hash"],
            values["label_source"],
            values["source_snapshot_sha256"],
            cls._normalize_datetime(values["source_observed_at"]),
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _normalize_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
