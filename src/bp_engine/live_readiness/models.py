from __future__ import annotations

import math
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

LIVE_POLICY_VERSION = "live-risk-v1"
LIVE_READINESS_VERSION = "live-readiness-v1"
_HEX = set(string.hexdigits)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _sha(value: str, name: str) -> str:
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")
    return value.lower()


def _decimal(value: Decimal | int | float | str, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def _nonnegative(value: Decimal | int | float | str, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


@dataclass(frozen=True)
class GeoblockResult:
    blocked: bool
    country: str
    region: str
    checked_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "checked_at", _aware(self.checked_at, "checked_at"))
        if not isinstance(self.country, str) or not isinstance(self.region, str):
            raise ValueError("country and region must be strings")


@dataclass(frozen=True)
class ActivationManifest:
    authorized: bool
    git_sha: str
    authorization_id: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "git_sha", _sha(self.git_sha, "git_sha"))
        issued_at = _aware(self.issued_at, "issued_at")
        expires_at = _aware(self.expires_at, "expires_at")
        if expires_at <= issued_at:
            raise ValueError("expires_at must be after issued_at")
        if not self.authorization_id.strip():
            raise ValueError("authorization_id must not be blank")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True)
class LiveRiskPolicy:
    max_trade_size_usd: Decimal
    max_total_exposure_usd: Decimal
    max_daily_loss_usd: Decimal
    max_consecutive_losses: int
    min_edge: Decimal
    min_probability: Decimal
    min_liquidity_usd: Decimal
    max_spread: Decimal
    max_prediction_age_seconds: Decimal
    min_time_to_expiry_seconds: Decimal
    cooldown_seconds: Decimal
    policy_version: str = LIVE_POLICY_VERSION

    def __post_init__(self) -> None:
        decimal_fields = (
            "max_trade_size_usd",
            "max_total_exposure_usd",
            "max_daily_loss_usd",
            "min_edge",
            "min_probability",
            "min_liquidity_usd",
            "max_spread",
            "max_prediction_age_seconds",
            "min_time_to_expiry_seconds",
            "cooldown_seconds",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, _nonnegative(getattr(self, name), name))
        if self.max_consecutive_losses < 0:
            raise ValueError("max_consecutive_losses must be nonnegative")
        if self.min_probability > 1:
            raise ValueError("min_probability must be between 0 and 1")
        if self.max_spread > 1:
            raise ValueError("max_spread must be between 0 and 1")
        if (
            self.max_trade_size_usd > 0
            and self.max_total_exposure_usd > 0
            and self.max_trade_size_usd > self.max_total_exposure_usd
        ):
            raise ValueError("max_trade_size_usd cannot exceed max_total_exposure_usd")
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")


@dataclass(frozen=True)
class LiveAccountSnapshot:
    total_exposure_usd: Decimal
    realized_daily_pnl_usd: Decimal
    consecutive_losses: int
    last_order_at: datetime | None
    unresolved_critical_reconciliation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "total_exposure_usd",
            _nonnegative(self.total_exposure_usd, "total_exposure_usd"),
        )
        object.__setattr__(
            self,
            "realized_daily_pnl_usd",
            _decimal(self.realized_daily_pnl_usd, "realized_daily_pnl_usd"),
        )
        if self.consecutive_losses < 0:
            raise ValueError("consecutive_losses must be nonnegative")
        if self.unresolved_critical_reconciliation < 0:
            raise ValueError("unresolved_critical_reconciliation must be nonnegative")
        if self.last_order_at is not None:
            object.__setattr__(self, "last_order_at", _aware(self.last_order_at, "last_order_at"))


@dataclass(frozen=True)
class LiveRiskContext:
    prediction_id: str
    prediction_semantic_sha256: str
    recorded_at: datetime
    market_end_at: datetime
    trade: bool
    executable: bool
    probability: Decimal
    expected_edge: Decimal
    selected_ask: Decimal | None
    spread: Decimal | None
    selected_liquidity_usd: Decimal | None
    requested_notional_usd: Decimal
    observed_at: datetime
    api_healthy: bool
    duplicate_intent: bool
    account: LiveAccountSnapshot

    def __post_init__(self) -> None:
        if not self.prediction_id.strip():
            raise ValueError("prediction_id must not be blank")
        object.__setattr__(
            self,
            "prediction_semantic_sha256",
            _sha(self.prediction_semantic_sha256, "prediction_semantic_sha256"),
        )
        for name in ("recorded_at", "market_end_at", "observed_at"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        probability = _decimal(self.probability, "probability")
        if probability < 0 or probability > 1:
            raise ValueError("probability must be between 0 and 1")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "expected_edge", _decimal(self.expected_edge, "expected_edge"))
        if self.selected_ask is not None:
            ask = _decimal(self.selected_ask, "selected_ask")
            if ask <= 0 or ask > 1:
                raise ValueError("selected_ask must be within (0, 1]")
            object.__setattr__(self, "selected_ask", ask)
        if self.spread is not None:
            spread = _nonnegative(self.spread, "spread")
            if spread > 1:
                raise ValueError("spread must be at most 1")
            object.__setattr__(self, "spread", spread)
        if self.selected_liquidity_usd is not None:
            object.__setattr__(
                self,
                "selected_liquidity_usd",
                _nonnegative(self.selected_liquidity_usd, "selected_liquidity_usd"),
            )
        notional = _decimal(self.requested_notional_usd, "requested_notional_usd")
        if notional <= 0:
            raise ValueError("requested_notional_usd must be positive")
        object.__setattr__(self, "requested_notional_usd", notional)


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.rule.strip():
            raise ValueError("rule must not be blank")
        if not self.reason.strip():
            raise ValueError("reason must not be blank")


@dataclass(frozen=True)
class LiveRiskDecision:
    eligible: bool
    reasons: tuple[str, ...]
    rules: tuple[RuleResult, ...]
    policy_sha256: str
    semantic_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "policy_sha256", _sha(self.policy_sha256, "policy_sha256"))
        object.__setattr__(self, "semantic_sha256", _sha(self.semantic_sha256, "semantic_sha256"))
        if self.eligible and self.reasons:
            raise ValueError("eligible decision cannot contain blocking reasons")


def finite_float(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value
