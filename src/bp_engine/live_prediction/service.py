from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, select

from bp_engine.config import Settings, TradingMode
from bp_engine.live_prediction.evaluation import append_available_evaluations
from bp_engine.live_prediction.inputs import observe_live_input
from bp_engine.live_prediction.models import LIVE_PREDICTION_VERSION, LivePolicySpec
from bp_engine.live_prediction.predictor import build_live_prediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage.schema import live_predictions, polymarket_markets

LOGGER = logging.getLogger(__name__)


class LivePredictionSafetyError(RuntimeError):
    """Raised when the prospective predictor is not strictly research-only."""


@dataclass(frozen=True)
class DueMarket:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    scheduled_at: datetime
    up_token_id: str
    down_token_id: str


@dataclass(frozen=True)
class LivePredictionCycleResult:
    due_markets: int
    created_predictions: int
    existing_predictions: int
    missed_predictions: int
    failed_markets: int


Observer = Callable[..., Awaitable[Any]]
Predictor = Callable[..., Any]
Evaluator = Callable[..., Any]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def ensure_live_prediction_safety(settings: Settings) -> None:
    """Fail closed unless every money-bearing runtime switch is disabled."""
    failures: list[str] = []
    if settings.mode != TradingMode.RESEARCH:
        failures.append("mode must be research")
    if settings.live_trading_enabled:
        failures.append("live_trading_enabled must be false")
    if settings.max_trade_size_usd != 0:
        failures.append("max_trade_size_usd must be zero")
    if settings.max_daily_loss_usd != 0:
        failures.append("max_daily_loss_usd must be zero")
    if failures:
        raise LivePredictionSafetyError("; ".join(failures))


def _validate_policy_mapping(policies: Mapping[int, LivePolicySpec]) -> None:
    if not policies:
        raise ValueError("at least one live prediction policy is required")
    for horizon_seconds, policy in policies.items():
        if horizon_seconds <= 0:
            raise ValueError("policy horizon key must be positive")
        if policy.horizon_seconds != horizon_seconds:
            raise ValueError(
                "policy horizon key does not match policy.horizon_seconds: "
                f"{horizon_seconds} != {policy.horizon_seconds}"
            )
        if policy.selected_offset_seconds <= 0:
            raise ValueError("selected_offset_seconds must be positive")
        if policy.selected_offset_seconds >= horizon_seconds:
            raise ValueError("selected_offset_seconds must be inside the market horizon")


def load_due_markets(
    connection: Connection,
    *,
    policies: Mapping[int, LivePolicySpec],
    now: datetime,
    max_lateness_seconds: int = 10,
    prediction_version: str = LIVE_PREDICTION_VERSION,
) -> tuple[DueMarket, ...]:
    """Return only prospective markets whose frozen prediction time is due."""
    _validate_policy_mapping(policies)
    current = _require_aware_utc(now, "now")
    if max_lateness_seconds < 0:
        raise ValueError("max_lateness_seconds must be non-negative")

    existing_condition_ids = set(
        connection.execute(
            select(live_predictions.c.condition_id).where(
                live_predictions.c.prediction_version == prediction_version
            )
        ).scalars()
    )
    rows = connection.execute(
        select(polymarket_markets)
        .where(
            polymarket_markets.c.horizon_seconds.in_(tuple(policies)),
            polymarket_markets.c.active.is_(True),
            polymarket_markets.c.closed.is_(False),
            polymarket_markets.c.resolved_outcome.is_(None),
        )
        .order_by(polymarket_markets.c.start_at, polymarket_markets.c.condition_id)
    ).mappings()

    due: list[DueMarket] = []
    for row in rows:
        condition_id = str(row["condition_id"] or "")
        if condition_id in existing_condition_ids:
            continue
        slug = str(row["slug"] or "")
        up_token_id = str(row["up_token_id"] or "")
        down_token_id = str(row["down_token_id"] or "")
        if not condition_id or not slug or not up_token_id or not down_token_id:
            continue
        if up_token_id == down_token_id:
            continue

        horizon_seconds = int(row["horizon_seconds"])
        policy = policies[horizon_seconds]
        market_start_at = _as_utc(row["start_at"])
        market_end_at = _as_utc(row["end_at"])
        scheduled_at = market_start_at + timedelta(
            seconds=policy.selected_offset_seconds
        )
        if not market_start_at < scheduled_at < market_end_at:
            raise ValueError(
                "policy scheduled_at must be strictly inside market window "
                f"condition_id={condition_id}"
            )
        deadline = scheduled_at + timedelta(seconds=max_lateness_seconds)
        if current < scheduled_at or current > deadline or current >= market_end_at:
            continue

        due.append(
            DueMarket(
                condition_id=condition_id,
                slug=slug,
                horizon_seconds=horizon_seconds,
                market_start_at=market_start_at,
                market_end_at=market_end_at,
                scheduled_at=scheduled_at,
                up_token_id=up_token_id,
                down_token_id=down_token_id,
            )
        )

    return tuple(sorted(due, key=lambda market: (market.scheduled_at, market.condition_id)))


def _default_predictor(
    policy: LivePolicySpec,
    live_input: Any,
    *,
    condition_id: str,
    slug: str,
    horizon_seconds: int,
    market_start_at: datetime,
    market_end_at: datetime,
    up_token_id: str,
    down_token_id: str,
    recorded_at: datetime,
    **_: Any,
) -> Any:
    return build_live_prediction(
        policy,
        live_input,
        condition_id=condition_id,
        slug=slug,
        horizon_seconds=horizon_seconds,
        market_start_at=market_start_at,
        market_end_at=market_end_at,
        up_token_id=up_token_id,
        down_token_id=down_token_id,
        recorded_at=recorded_at,
    )


class LivePredictionService:
    """Prospective research-only scheduler for immutable live predictions."""

    def __init__(
        self,
        *,
        engine: Engine,
        policies: Mapping[int, LivePolicySpec],
        client: Any,
        observer: Observer = observe_live_input,
        predictor: Predictor = _default_predictor,
        repository: LivePredictionRepository | None = None,
        evaluator: Evaluator = append_available_evaluations,
        clock: Clock = _utc_now,
        max_lateness_seconds: int = 10,
        poll_interval_seconds: float = 1.0,
        logger: logging.Logger | None = None,
    ) -> None:
        _validate_policy_mapping(policies)
        if max_lateness_seconds < 0:
            raise ValueError("max_lateness_seconds must be non-negative")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._engine = engine
        self._policies = dict(policies)
        self._client = client
        self._observer = observer
        self._predictor = predictor
        self._repository = repository or LivePredictionRepository()
        self._evaluator = evaluator
        self._clock = clock
        self._max_lateness_seconds = max_lateness_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._logger = logger or LOGGER

    def _now(self) -> datetime:
        return _require_aware_utc(self._clock(), "clock")

    async def _process_market(self, market: DueMarket) -> tuple[bool, bool, bool]:
        policy = self._policies[market.horizon_seconds]
        with self._engine.begin() as connection:
            live_input = await self._observer(
                connection,
                self._client,
                condition_id=market.condition_id,
                up_token_id=market.up_token_id,
                down_token_id=market.down_token_id,
                market_start_at=market.market_start_at,
                market_end_at=market.market_end_at,
                scheduled_at=market.scheduled_at,
                clock=self._clock,
                max_lateness_seconds=self._max_lateness_seconds,
            )
            completed_at = self._now()
            deadline = market.scheduled_at + timedelta(
                seconds=self._max_lateness_seconds
            )
            if completed_at > deadline:
                self._logger.warning(
                    "live_prediction_missed",
                    extra={
                        "condition_id": market.condition_id,
                        "reason": "deadline_exceeded_after_observation",
                        "scheduled_at": market.scheduled_at.isoformat(),
                        "deadline_at": deadline.isoformat(),
                        "completed_at": completed_at.isoformat(),
                    },
                )
                return False, False, True
            if completed_at >= market.market_end_at:
                self._logger.warning(
                    "live_prediction_missed",
                    extra={
                        "condition_id": market.condition_id,
                        "reason": "market_ended_after_observation",
                        "scheduled_at": market.scheduled_at.isoformat(),
                        "market_end_at": market.market_end_at.isoformat(),
                        "completed_at": completed_at.isoformat(),
                    },
                )
                return False, False, True

            prediction = self._predictor(
                policy,
                live_input,
                condition_id=market.condition_id,
                slug=market.slug,
                horizon_seconds=market.horizon_seconds,
                market_start_at=market.market_start_at,
                market_end_at=market.market_end_at,
                scheduled_at=market.scheduled_at,
                up_token_id=market.up_token_id,
                down_token_id=market.down_token_id,
                recorded_at=completed_at,
            )
            result = self._repository.store(connection, prediction)
            self._logger.info(
                "live_prediction_recorded",
                extra={
                    "condition_id": market.condition_id,
                    "prediction_id": prediction.prediction_id,
                    "created": result.created,
                    "existing": result.existing,
                    "scheduled_at": market.scheduled_at.isoformat(),
                    "recorded_at": prediction.recorded_at.isoformat(),
                },
            )
            return result.created, result.existing, False

    async def run_once(self) -> LivePredictionCycleResult:
        cycle_at = self._now()
        with self._engine.begin() as connection:
            due_markets = load_due_markets(
                connection,
                policies=self._policies,
                now=cycle_at,
                max_lateness_seconds=self._max_lateness_seconds,
            )

        created = 0
        existing = 0
        missed = 0
        failed = 0
        for market in due_markets:
            try:
                was_created, was_existing, was_missed = await self._process_market(market)
                created += int(was_created)
                existing += int(was_existing)
                missed += int(was_missed)
            except Exception as exc:
                failed += 1
                self._logger.exception(
                    "live_prediction_market_failed",
                    extra={
                        "condition_id": market.condition_id,
                        "horizon_seconds": market.horizon_seconds,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

        try:
            evaluated_at = self._now()
            with self._engine.begin() as connection:
                self._evaluator(connection, evaluated_at=evaluated_at)
        except Exception as exc:
            self._logger.error(
                "live_prediction_evaluation_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

        return LivePredictionCycleResult(
            due_markets=len(due_markets),
            created_predictions=created,
            existing_predictions=existing,
            missed_predictions=missed,
            failed_markets=failed,
        )

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            await self.run_once()
            if stop is None:
                await asyncio.sleep(self._poll_interval_seconds)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                pass


async def run_live_prediction_service(
    *,
    settings: Settings,
    policies: Mapping[int, LivePolicySpec],
    engine: Engine,
    client: Any,
    stop: asyncio.Event | None = None,
) -> None:
    """Validate the research-only interlock, then run the prospective loop."""
    ensure_live_prediction_safety(settings)
    service = LivePredictionService(
        engine=engine,
        policies=policies,
        client=client,
    )
    await service.run(stop=stop)
