from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, insert, select
from sqlalchemy.engine import RowMapping

from bp_engine.features.models import MarketFeature
from bp_engine.storage.schema import market_features


class FeatureConflict(RuntimeError):
    """Raised when an immutable feature snapshot would be rewritten."""


@dataclass(frozen=True)
class FeatureStoreResult:
    created: bool


class MarketFeatureRepository:
    def find(
        self,
        connection: Connection,
        *,
        condition_id: str,
        feature_at: datetime,
        feature_version: str,
    ) -> RowMapping | None:
        return connection.execute(
            select(market_features).where(
                market_features.c.condition_id == condition_id,
                market_features.c.feature_at == feature_at,
                market_features.c.feature_version == feature_version,
            )
        ).mappings().one_or_none()

    def store(self, connection: Connection, feature: MarketFeature) -> FeatureStoreResult:
        self._validate(feature)
        existing = self.find(
            connection,
            condition_id=feature.condition_id,
            feature_at=feature.feature_at,
            feature_version=feature.feature_version,
        )

        if existing is None:
            connection.execute(insert(market_features).values(**feature.__dict__))
            return FeatureStoreResult(created=True)

        expected = self._semantic_values(feature.__dict__)
        actual = self._semantic_values(dict(existing))
        if actual != expected:
            raise FeatureConflict(
                "conflicting feature for "
                f"condition={feature.condition_id} "
                f"feature_at={feature.feature_at.isoformat()} "
                f"version={feature.feature_version}"
            )
        return FeatureStoreResult(created=False)

    @staticmethod
    def _validate(feature: MarketFeature) -> None:
        for name in ("market_start_at", "market_end_at", "feature_at", "generated_at"):
            value = getattr(feature, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if feature.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if feature.market_end_at <= feature.market_start_at:
            raise ValueError("market_end_at must be after market_start_at")
        if not feature.market_start_at < feature.feature_at < feature.market_end_at:
            raise ValueError("feature_at must be strictly inside market window")
        expected_offset = int((feature.feature_at - feature.market_start_at).total_seconds())
        if feature.feature_offset_seconds != expected_offset:
            raise ValueError("feature_offset_seconds must match feature_at - market_start_at")
        if not feature.feature_version:
            raise ValueError("feature_version must not be empty")
        for name in ("input_fingerprint", "feature_hash"):
            value = getattr(feature, name)
            if len(value) != 64:
                raise ValueError(f"{name} must be a 64-character SHA-256 hex digest")

    @classmethod
    def _semantic_values(cls, values: dict[str, Any]) -> tuple[Any, ...]:
        return (
            values["slug"],
            int(values["horizon_seconds"]),
            cls._normalize_datetime(values["market_start_at"]),
            cls._normalize_datetime(values["market_end_at"]),
            int(values["feature_offset_seconds"]),
            values["features"],
            values["missing_flags"],
            values["source_cutoffs"],
            values["input_fingerprint"],
            values["feature_hash"],
        )

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
