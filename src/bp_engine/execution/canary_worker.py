from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings, TradingMode
from bp_engine.execution.live import (
    InterlockDecision,
    PolymarketLiveExecutionGateway,
)
from bp_engine.execution.models import (
    PaperExecutionConfig,
    V3_LIVE_CANARY_EXECUTION_VERSION,
)
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.execution.remote_canary import (
    SshExecutorConfig,
    SshPolymarketTradingClient,
    assert_executor_files,
)
from bp_engine.live_readiness.interlock import (
    ActivationManifestError,
    kill_switch_engaged,
    load_activation_manifest,
)
from bp_engine.live_readiness.models import LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema
from bp_engine.v3_paper.service import V3_PAPER_PREDICTION_VERSION

CANARY_POLICY_VERSION = "live-risk-v3-canary-v1"
CANARY_TARGET_NOTIONAL_USD = Decimal("1.00")
CANARY_MAX_TOTAL_EXPOSURE_USD = Decimal("1.00")
CANARY_MAX_DAILY_LOSS_USD = Decimal("2.00")
CANARY_MAX_CONSECUTIVE_LOSSES = 2
CANARY_MIN_EDGE = Decimal("0.075")
CANARY_MAX_PREDICTION_AGE_SECONDS = Decimal("10")
CANARY_MIN_TIME_TO_EXPIRY_SECONDS = Decimal("30")
CANARY_COOLDOWN_SECONDS = Decimal("60")
CANARY_ORDER_TTL_MS = 2000
CANARY_LATENCY_MS = 250

_STOP = False


def _request_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _settings() -> Settings:
    path = os.environ.get("BP_ENV_FILE", "/etc/bp/bp-canary.env")
    return Settings(_env_file=path)


def _expected_git_sha() -> str:
    value = os.environ.get("BP_CANARY_EXPECTED_GIT_SHA", "").strip().lower()
    if len(value) != 40:
        raise RuntimeError("BP_CANARY_EXPECTED_GIT_SHA is invalid")
    return value


def _remote_client() -> SshPolymarketTradingClient:
    config = SshExecutorConfig(
        host=os.environ["BP_CANARY_EXECUTOR_HOST"],
        user=os.environ.get("BP_CANARY_EXECUTOR_USER", "bp-exec"),
        identity_file=os.environ.get(
            "BP_CANARY_EXECUTOR_IDENTITY_FILE",
            "/var/lib/bp/live/canary-exec-key",
        ),
        known_hosts_file=os.environ.get(
            "BP_CANARY_EXECUTOR_KNOWN_HOSTS_FILE",
            "/var/lib/bp/live/canary-known-hosts",
        ),
        remote_command=os.environ.get(
            "BP_CANARY_EXECUTOR_COMMAND",
            "/opt/bp-exec/venv/bin/python -m bp_engine.execution.canary_executor",
        ),
    )
    assert_executor_files(config)
    return SshPolymarketTradingClient(config)


def _assert_exact_canary_settings(settings: Settings) -> None:
    if settings.mode != TradingMode.LIVE:
        raise RuntimeError("one-dollar canary requires MODE=live")
    if not settings.live_trading_enabled:
        raise RuntimeError("one-dollar canary requires LIVE_TRADING_ENABLED=true")
    expected = {
        "max_trade_size_usd": CANARY_TARGET_NOTIONAL_USD,
        "max_total_exposure_usd": CANARY_MAX_TOTAL_EXPOSURE_USD,
        "max_daily_loss_usd": CANARY_MAX_DAILY_LOSS_USD,
    }
    for name, value in expected.items():
        if Decimal(str(getattr(settings, name))) != value:
            raise RuntimeError(f"unexpected one-dollar canary setting: {name}")
    if settings.max_consecutive_losses != CANARY_MAX_CONSECUTIVE_LOSSES:
        raise RuntimeError("unexpected one-dollar canary consecutive-loss limit")
    if Decimal(str(settings.live_min_edge)) != CANARY_MIN_EDGE:
        raise RuntimeError("unexpected one-dollar canary min edge")


def _policy() -> LiveRiskPolicy:
    return LiveRiskPolicy(
        max_trade_size_usd=CANARY_TARGET_NOTIONAL_USD,
        max_total_exposure_usd=CANARY_MAX_TOTAL_EXPOSURE_USD,
        max_daily_loss_usd=CANARY_MAX_DAILY_LOSS_USD,
        max_consecutive_losses=CANARY_MAX_CONSECUTIVE_LOSSES,
        min_edge=CANARY_MIN_EDGE,
        min_probability=Decimal("0"),
        min_liquidity_usd=Decimal("1.00"),
        max_spread=Decimal("1"),
        max_prediction_age_seconds=CANARY_MAX_PREDICTION_AGE_SECONDS,
        min_time_to_expiry_seconds=CANARY_MIN_TIME_TO_EXPIRY_SECONDS,
        cooldown_seconds=CANARY_COOLDOWN_SECONDS,
        policy_version=CANARY_POLICY_VERSION,
    )


def _seed_reconciliation(engine, repository: LiveReadinessRepository) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        intent_count = int(
            connection.execute(
                select(func.count()).select_from(schema.live_order_intents)
            ).scalar_one()
        )
        if intent_count:
            raise RuntimeError("one-dollar canary live-order intent already exists")
        latest = connection.execute(
            select(schema.live_reconciliation_runs)
            .order_by(
                schema.live_reconciliation_runs.c.observed_at.desc(),
                schema.live_reconciliation_runs.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()
        if latest is None:
            repository.store_reconciliation_run(
                connection,
                observed_at=now,
                unresolved_count=0,
                critical_count=0,
                evidence={
                    "source": "phase15-v3-one-dollar-canary-initial-baseline",
                    "official_order_count": 0,
                    "account_snapshot": {
                        "realized_daily_pnl_usd": "0",
                        "consecutive_losses": 0,
                        "total_exposure_usd": "0",
                    },
                },
            )
        elif int(latest["critical_count"]) != 0:
            raise RuntimeError("latest live reconciliation contains critical issues")


def _candidate_rows(engine, activated_at: datetime) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(schema.live_predictions)
            .where(
                schema.live_predictions.c.prediction_version
                == V3_PAPER_PREDICTION_VERSION,
                schema.live_predictions.c.recorded_at >= activated_at,
                schema.live_predictions.c.trade.is_(True),
                schema.live_predictions.c.executable.is_(True),
            )
            .order_by(
                schema.live_predictions.c.recorded_at,
                schema.live_predictions.c.id,
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _build_request(row: dict[str, Any], now: datetime):
    config = PaperExecutionConfig(
        starting_cash_usd=CANARY_TARGET_NOTIONAL_USD,
        target_notional_usd=CANARY_TARGET_NOTIONAL_USD,
        latency_ms=CANARY_LATENCY_MS,
        order_ttl_ms=CANARY_ORDER_TTL_MS,
        share_precision=6,
        execution_version=V3_LIVE_CANARY_EXECUTION_VERSION,
        prediction_version=V3_PAPER_PREDICTION_VERSION,
    )
    draft = build_paper_order(
        row,
        config,
        CANARY_TARGET_NOTIONAL_USD,
    )
    if not isinstance(draft, PaperOrderDraft):
        return None
    market_end = row["market_end_at"]
    if market_end.tzinfo is None or market_end.utcoffset() is None:
        market_end = market_end.replace(tzinfo=UTC)
    else:
        market_end = market_end.astimezone(UTC)
    arrival = now
    expiry = min(
        now + timedelta(milliseconds=CANARY_ORDER_TTL_MS),
        market_end,
    )
    if expiry <= arrival:
        return None
    return replace(
        draft.request,
        arrival_at=arrival,
        expires_at=expiry,
    )


def _intent_exists(engine, prediction_id: str) -> bool:
    with engine.connect() as connection:
        count = int(
            connection.execute(
                select(func.count())
                .select_from(schema.live_order_intents)
                .where(
                    schema.live_order_intents.c.prediction_id == prediction_id,
                    schema.live_order_intents.c.policy_version
                    == CANARY_POLICY_VERSION,
                )
            ).scalar_one()
        )
    return count > 0


def main() -> int:
    global _STOP
    settings = _settings()
    _assert_exact_canary_settings(settings)
    expected_sha = _expected_git_sha()
    manifest = load_activation_manifest(
        settings.live_activation_manifest_path,
        expected_git_sha=expected_sha,
        observed_at=datetime.now(UTC),
    )
    if kill_switch_engaged(settings.live_kill_switch_path):
        raise RuntimeError("one-dollar canary kill switch is engaged")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    repository = LiveReadinessRepository()
    remote = _remote_client()
    health = remote.health()
    if health.get("ok") is not True:
        raise RuntimeError("Johannesburg executor is not ready")
    geoblock = dict(health.get("geoblock") or {})
    if geoblock.get("blocked") is not False:
        raise RuntimeError("Johannesburg executor geoblock is not clear")

    _seed_reconciliation(engine, repository)

    def interlock(observed_at: datetime) -> InterlockDecision:
        reasons: list[str] = []
        try:
            load_activation_manifest(
                settings.live_activation_manifest_path,
                expected_git_sha=expected_sha,
                observed_at=observed_at,
            )
        except ActivationManifestError:
            reasons.append("activation_invalid")
        if kill_switch_engaged(settings.live_kill_switch_path):
            reasons.append("kill_switch_engaged")
        try:
            remote_health = remote.health()
        except Exception:
            reasons.append("remote_executor_unhealthy")
        else:
            if remote_health.get("ok") is not True:
                reasons.append("remote_executor_unhealthy")
            remote_geo = dict(remote_health.get("geoblock") or {})
            if remote_geo.get("blocked") is not False:
                reasons.append("remote_executor_geoblocked")
        return InterlockDecision(eligible=not reasons, reasons=tuple(reasons))

    gateway = PolymarketLiveExecutionGateway(
        engine=engine,
        repository=repository,
        policy=_policy(),
        client_factory=lambda: remote,
        interlock=interlock,
        api_health=lambda: remote.health().get("ok") is True,
        now=lambda: datetime.now(UTC),
    )

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    _STOP = False
    try:
        while not _STOP:
            current = datetime.now(UTC)
            for row in _candidate_rows(engine, manifest.issued_at):
                recorded = row["recorded_at"]
                if recorded.tzinfo is None or recorded.utcoffset() is None:
                    recorded = recorded.replace(tzinfo=UTC)
                else:
                    recorded = recorded.astimezone(UTC)
                age = Decimal(str((current - recorded).total_seconds()))
                if age < 0 or age > CANARY_MAX_PREDICTION_AGE_SECONDS:
                    continue
                market_end = row["market_end_at"]
                if market_end.tzinfo is None or market_end.utcoffset() is None:
                    market_end = market_end.replace(tzinfo=UTC)
                else:
                    market_end = market_end.astimezone(UTC)
                if Decimal(str((market_end - current).total_seconds())) < (
                    CANARY_MIN_TIME_TO_EXPIRY_SECONDS
                ):
                    continue
                request = _build_request(row, current)
                if request is None:
                    continue
                ack = gateway.submit_order(request)
                if not _intent_exists(engine, request.prediction_id):
                    continue

                cancel_result: dict[str, Any] | None = None
                if ack.accepted:
                    delay = max(
                        0.0,
                        (request.expires_at - datetime.now(UTC)).total_seconds(),
                    )
                    if delay:
                        time.sleep(min(delay, 2.1))
                    cancel = gateway.cancel_order(
                        ack.order_id,
                        datetime.now(UTC),
                    )
                    cancel_result = {
                        "cancelled": cancel.cancelled,
                        "order_id": cancel.order_id,
                        "reason": cancel.reason,
                    }
                print(
                    json.dumps(
                        {
                            "phase15_v3_one_dollar_canary": "submission_consumed",
                            "prediction_id": request.prediction_id,
                            "accepted": ack.accepted,
                            "order_id": ack.order_id,
                            "reason": ack.reason,
                            "cancel": cancel_result,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 0
            time.sleep(0.25)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
