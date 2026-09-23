from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings, TradingMode
from bp_engine.execution.live import InterlockDecision, PolymarketLiveExecutionGateway
from bp_engine.execution.remote_client import RemoteSshPolymarketTradingClient
from bp_engine.live_readiness.interlock import (
    ActivationManifestError,
    kill_switch_engaged,
    load_activation_manifest,
)
from bp_engine.live_readiness.models import LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema
from bp_engine.v3_live_canary.request import (
    CANARY_COOLDOWN_SECONDS,
    CANARY_MAX_CONSECUTIVE_LOSSES,
    CANARY_MAX_DAILY_LOSS_USD,
    CANARY_MAX_PREDICTION_AGE_SECONDS,
    CANARY_MAX_SPREAD,
    CANARY_MAX_TOTAL_EXPOSURE_USD,
    CANARY_MIN_LIQUIDITY_USD,
    CANARY_MIN_TIME_TO_EXPIRY_SECONDS,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    build_v3_live_canary_request,
)
from bp_engine.v3_paper.service import V3_PAPER_PREDICTION_VERSION


class V3LiveCanaryRuntimeError(RuntimeError):
    """Raised when the single-order canary runtime drifts or is unsafe."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise V3LiveCanaryRuntimeError(f"{name} is not configured")
    return value


def _expected_git_sha() -> str:
    value = _required_env("BP_CANARY_EXPECTED_GIT_SHA").lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise V3LiveCanaryRuntimeError("BP_CANARY_EXPECTED_GIT_SHA is invalid")
    return value


def _validate_settings(settings: Settings) -> None:
    expected = {
        "mode": TradingMode.LIVE,
        "live_trading_enabled": True,
        "max_trade_size_usd": CANARY_TARGET_NOTIONAL_USD,
        "max_total_exposure_usd": CANARY_MAX_TOTAL_EXPOSURE_USD,
        "max_daily_loss_usd": CANARY_MAX_DAILY_LOSS_USD,
        "max_consecutive_losses": CANARY_MAX_CONSECUTIVE_LOSSES,
        "live_min_edge": Decimal("0.075"),
        "live_min_probability": Decimal("0"),
        "live_min_liquidity_usd": CANARY_MIN_LIQUIDITY_USD,
        "live_max_spread": CANARY_MAX_SPREAD,
        "live_max_prediction_age_seconds": CANARY_MAX_PREDICTION_AGE_SECONDS,
        "live_min_time_to_expiry_seconds": CANARY_MIN_TIME_TO_EXPIRY_SECONDS,
        "live_cooldown_seconds": CANARY_COOLDOWN_SECONDS,
    }
    if settings.mode != expected["mode"]:
        raise V3LiveCanaryRuntimeError("mode is not live")
    if settings.live_trading_enabled is not True:
        raise V3LiveCanaryRuntimeError("live trading is not enabled for canary worker")
    decimal_fields = (
        "max_trade_size_usd",
        "max_total_exposure_usd",
        "max_daily_loss_usd",
        "live_min_edge",
        "live_min_probability",
        "live_min_liquidity_usd",
        "live_max_spread",
        "live_max_prediction_age_seconds",
        "live_min_time_to_expiry_seconds",
        "live_cooldown_seconds",
    )
    for name in decimal_fields:
        if Decimal(str(getattr(settings, name))) != expected[name]:
            raise V3LiveCanaryRuntimeError(f"{name} drifted from frozen canary contract")
    if settings.max_consecutive_losses != CANARY_MAX_CONSECUTIVE_LOSSES:
        raise V3LiveCanaryRuntimeError("max_consecutive_losses drifted")


def _policy(settings: Settings) -> LiveRiskPolicy:
    _validate_settings(settings)
    return LiveRiskPolicy(
        max_trade_size_usd=Decimal(str(settings.max_trade_size_usd)),
        max_total_exposure_usd=Decimal(str(settings.max_total_exposure_usd)),
        max_daily_loss_usd=Decimal(str(settings.max_daily_loss_usd)),
        max_consecutive_losses=settings.max_consecutive_losses,
        min_edge=Decimal(str(settings.live_min_edge)),
        min_probability=Decimal(str(settings.live_min_probability)),
        min_liquidity_usd=Decimal(str(settings.live_min_liquidity_usd)),
        max_spread=Decimal(str(settings.live_max_spread)),
        max_prediction_age_seconds=Decimal(
            str(settings.live_max_prediction_age_seconds)
        ),
        min_time_to_expiry_seconds=Decimal(
            str(settings.live_min_time_to_expiry_seconds)
        ),
        cooldown_seconds=Decimal(str(settings.live_cooldown_seconds)),
        policy_version=CANARY_POLICY_VERSION,
    )


def _interlock(
    *,
    settings: Settings,
    expected_git_sha: str,
    observed_at: datetime,
) -> InterlockDecision:
    reasons: list[str] = []
    try:
        load_activation_manifest(
            settings.live_activation_manifest_path,
            expected_git_sha=expected_git_sha,
            observed_at=observed_at,
        )
    except ActivationManifestError:
        reasons.append("activation_manifest_invalid")
    if kill_switch_engaged(settings.live_kill_switch_path):
        reasons.append("kill_switch_engaged")
    if settings.mode != TradingMode.LIVE:
        reasons.append("mode_not_live")
    if settings.live_trading_enabled is not True:
        reasons.append("live_trading_disabled")
    return InterlockDecision(eligible=not reasons, reasons=tuple(reasons))


def _seed_initial_reconciliation(
    *,
    engine: Any,
    repository: LiveReadinessRepository,
    observed_at: datetime,
) -> None:
    with engine.begin() as connection:
        intent_count = int(
            connection.execute(
                select(func.count()).select_from(schema.live_order_intents)
            ).scalar_one()
        )
        event_count = int(
            connection.execute(
                select(func.count()).select_from(schema.live_order_events)
            ).scalar_one()
        )
        if intent_count or event_count:
            raise V3LiveCanaryRuntimeError("live order ledger is not empty before canary")
        existing = connection.execute(
            select(schema.live_reconciliation_runs.c.id)
            .order_by(schema.live_reconciliation_runs.c.observed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is None:
            repository.store_reconciliation_run(
                connection,
                observed_at=observed_at,
                unresolved_count=0,
                critical_count=0,
                evidence={
                    "phase15_canary_initialization": True,
                    "account_snapshot": {
                        "realized_daily_pnl_usd": "0",
                        "consecutive_losses": 0,
                        "total_exposure_usd": "0",
                    },
                },
            )


def _candidate_prediction(
    *,
    engine: Any,
    activation_issued_at: datetime,
    observed_at: datetime,
    seen: set[str],
) -> dict[str, Any] | None:
    with engine.connect() as connection:
        rows = connection.execute(
            select(schema.live_predictions)
            .where(
                schema.live_predictions.c.prediction_version
                == V3_PAPER_PREDICTION_VERSION,
                schema.live_predictions.c.trade.is_(True),
                schema.live_predictions.c.executable.is_(True),
                schema.live_predictions.c.recorded_at >= activation_issued_at,
                schema.live_predictions.c.recorded_at <= observed_at,
            )
            .order_by(
                schema.live_predictions.c.recorded_at.desc(),
                schema.live_predictions.c.id.desc(),
            )
            .limit(8)
        ).mappings().all()
    for row in rows:
        prediction_id = str(row["prediction_id"])
        if prediction_id not in seen:
            return dict(row)
    return None


def _intent_exists(
    *,
    engine: Any,
    prediction_id: str,
) -> bool:
    with engine.connect() as connection:
        value = connection.execute(
            select(schema.live_order_intents.c.id)
            .where(
                schema.live_order_intents.c.prediction_id == prediction_id,
                schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION,
            )
            .limit(1)
        ).scalar_one_or_none()
    return value is not None


def _write_kill(path: str, reason: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write(reason + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    target.chmod(0o600)


def run() -> int:
    settings = Settings()
    expected_git_sha = _expected_git_sha()
    _validate_settings(settings)
    manifest = load_activation_manifest(
        settings.live_activation_manifest_path,
        expected_git_sha=expected_git_sha,
        observed_at=datetime.now(UTC),
    )
    remote = RemoteSshPolymarketTradingClient(
        host=_required_env("BP_CANARY_REMOTE_HOST"),
        user=_required_env("BP_CANARY_REMOTE_USER"),
        key_path=_required_env("BP_CANARY_REMOTE_KEY_PATH"),
        known_hosts_path=_required_env("BP_CANARY_REMOTE_KNOWN_HOSTS_PATH"),
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    repository = LiveReadinessRepository()
    _seed_initial_reconciliation(
        engine=engine,
        repository=repository,
        observed_at=datetime.now(UTC),
    )
    gateway = PolymarketLiveExecutionGateway(
        engine=engine,
        repository=repository,
        policy=_policy(settings),
        client_factory=lambda: remote,
        interlock=lambda now: _interlock(
            settings=settings,
            expected_git_sha=expected_git_sha,
            observed_at=now,
        ),
        api_health=remote.health,
        now=lambda: datetime.now(UTC),
    )

    seen: set[str] = set()
    try:
        while True:
            now = datetime.now(UTC)
            if now >= manifest.expires_at:
                _write_kill(settings.live_kill_switch_path, "activation manifest expired")
                print(json.dumps({"status": "expired_without_external_attempt"}, sort_keys=True))
                return 0
            if kill_switch_engaged(settings.live_kill_switch_path):
                print(json.dumps({"status": "kill_switch_engaged"}, sort_keys=True))
                return 0
            if not remote.health():
                time.sleep(0.5)
                continue

            prediction = _candidate_prediction(
                engine=engine,
                activation_issued_at=manifest.issued_at,
                observed_at=now,
                seen=seen,
            )
            if prediction is None:
                time.sleep(0.25)
                continue
            prediction_id = str(prediction["prediction_id"])
            seen.add(prediction_id)

            try:
                request = build_v3_live_canary_request(prediction)
            except Exception:
                continue
            if now >= request.expires_at:
                continue

            remote.set_order_deadline(request.expires_at)
            ack = gateway.submit_order(request)
            if not _intent_exists(engine=engine, prediction_id=prediction_id):
                # Risk/interlock blocked before any external order intent existed.
                continue
            if not ack.accepted and ack.reason == "request_expired":
                # The remote executor rejected before consuming the single
                # external attempt. Wait for the next frozen-V3 signal.
                continue

            _write_kill(
                settings.live_kill_switch_path,
                "single-order canary external attempt consumed",
            )
            print(
                json.dumps(
                    {
                        "status": "external_attempt_consumed",
                        "accepted": ack.accepted,
                        "order_id": ack.order_id,
                        "reason": ack.reason,
                        "observed_at": ack.observed_at.isoformat(),
                        "prediction_id": prediction_id,
                    },
                    sort_keys=True,
                )
            )
            return 0
    finally:
        engine.dispose()


def main() -> int:
    try:
        return run()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc)[:160],
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
