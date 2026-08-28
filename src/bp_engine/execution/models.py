from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

PAPER_EXECUTION_VERSION = "paper-execution-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _positive_decimal(value: Decimal, *, name: str) -> Decimal:
    numeric = Decimal(value)
    if not numeric.is_finite() or numeric <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return numeric


def _probability_price(value: Decimal, *, name: str) -> Decimal:
    numeric = Decimal(value)
    if not numeric.is_finite() or not Decimal("0") < numeric <= Decimal("1"):
        raise ValueError(f"{name} must be within (0, 1]")
    return numeric


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _text(value: str, *, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _sha256(value: str, *, name: str) -> str:
    normalized = str(value).lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized


@dataclass(frozen=True)
class PaperExecutionConfig:
    starting_cash_usd: Decimal = Decimal("100.00")
    target_notional_usd: Decimal = Decimal("5.00")
    latency_ms: int = 250
    order_ttl_ms: int = 2000
    share_precision: int = 6
    execution_version: str = PAPER_EXECUTION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "starting_cash_usd",
            _positive_decimal(self.starting_cash_usd, name="starting_cash_usd"),
        )
        object.__setattr__(
            self,
            "target_notional_usd",
            _positive_decimal(self.target_notional_usd, name="target_notional_usd"),
        )
        if self.latency_ms <= 0:
            raise ValueError("latency_ms must be greater than zero")
        if self.order_ttl_ms <= 0:
            raise ValueError("order_ttl_ms must be greater than zero")
        if not 0 <= self.share_precision <= 18:
            raise ValueError("share_precision must be within [0, 18]")
        if self.execution_version != PAPER_EXECUTION_VERSION:
            raise ValueError(f"execution_version must be {PAPER_EXECUTION_VERSION}")

    def as_mapping(self) -> dict[str, object]:
        return {
            "execution_version": self.execution_version,
            "starting_cash_usd": str(self.starting_cash_usd),
            "target_notional_usd": str(self.target_notional_usd),
            "latency_ms": self.latency_ms,
            "order_ttl_ms": self.order_ttl_ms,
            "share_precision": self.share_precision,
        }


@dataclass(frozen=True)
class ExecutionOrderRequest:
    prediction_id: str
    prediction_semantic_sha256: str
    condition_id: str
    token_id: str
    selected_side: str
    action: str
    requested_shares: Decimal
    target_notional_usd: Decimal
    submitted_at: datetime
    arrival_at: datetime
    expires_at: datetime
    limit_price: Decimal
    execution_version: str
    execution_config_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "prediction_id", _text(self.prediction_id, name="prediction_id"))
        object.__setattr__(
            self,
            "prediction_semantic_sha256",
            _sha256(self.prediction_semantic_sha256, name="prediction_semantic_sha256"),
        )
        object.__setattr__(self, "condition_id", _text(self.condition_id, name="condition_id"))
        object.__setattr__(self, "token_id", _text(self.token_id, name="token_id"))
        side = _text(self.selected_side, name="selected_side").lower()
        if side not in {"up", "down"}:
            raise ValueError("selected_side must be up or down")
        object.__setattr__(self, "selected_side", side)
        if self.action != "BUY":
            raise ValueError("Phase 12 execution action must be BUY")
        object.__setattr__(
            self,
            "requested_shares",
            _positive_decimal(self.requested_shares, name="requested_shares"),
        )
        object.__setattr__(
            self,
            "target_notional_usd",
            _positive_decimal(self.target_notional_usd, name="target_notional_usd"),
        )
        object.__setattr__(self, "limit_price", _probability_price(self.limit_price, name="limit_price"))
        submitted_at = _utc(self.submitted_at)
        arrival_at = _utc(self.arrival_at)
        expires_at = _utc(self.expires_at)
        if not submitted_at <= arrival_at < expires_at:
            raise ValueError("invalid timestamp order: require submitted_at <= arrival_at < expires_at")
        object.__setattr__(self, "submitted_at", submitted_at)
        object.__setattr__(self, "arrival_at", arrival_at)
        object.__setattr__(self, "expires_at", expires_at)
        if self.execution_version != PAPER_EXECUTION_VERSION:
            raise ValueError(f"execution_version must be {PAPER_EXECUTION_VERSION}")
        object.__setattr__(
            self,
            "execution_config_sha256",
            _sha256(self.execution_config_sha256, name="execution_config_sha256"),
        )

    def as_mapping(self, *, raw: bool = False) -> dict[str, Any]:
        mapping: dict[str, Any] = {
            "prediction_id": self.prediction_id,
            "prediction_semantic_sha256": self.prediction_semantic_sha256,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "selected_side": self.selected_side,
            "action": self.action,
            "requested_shares": self.requested_shares,
            "target_notional_usd": self.target_notional_usd,
            "submitted_at": self.submitted_at,
            "arrival_at": self.arrival_at,
            "expires_at": self.expires_at,
            "limit_price": self.limit_price,
            "execution_version": self.execution_version,
            "execution_config_sha256": self.execution_config_sha256,
        }
        if raw:
            return mapping
        for key in ("requested_shares", "target_notional_usd", "limit_price"):
            mapping[key] = str(mapping[key])
        for key in ("submitted_at", "arrival_at", "expires_at"):
            mapping[key] = mapping[key].isoformat()
        return mapping


@dataclass(frozen=True)
class ExecutionOrderAck:
    order_id: str
    accepted: bool
    observed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", _text(self.order_id, name="order_id"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        object.__setattr__(self, "reason", _text(self.reason, name="reason"))


@dataclass(frozen=True)
class ExecutionCancelAck:
    order_id: str
    cancelled: bool
    observed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", _text(self.order_id, name="order_id"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        object.__setattr__(self, "reason", _text(self.reason, name="reason"))
