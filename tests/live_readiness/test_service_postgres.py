from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, func, select

from bp_engine.config import Settings
from bp_engine.live_readiness.geoblock import GeoblockError
from bp_engine.live_readiness.interlock import ActivationManifestError
from bp_engine.live_readiness.models import ActivationManifest, GeoblockResult
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.service import (
    LiveReadinessService,
    OfficialOrderSnapshot,
)
from bp_engine.storage import schema

DATABASE_URL = os.environ.get("BP_TEST_DATABASE_URL")
BASE = datetime(2026, 8, 29, 16, 45, tzinfo=UTC)
GIT_SHA = "a" * 64

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _cleanup(engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(schema.live_order_events))
        connection.execute(delete(schema.live_order_intents))
        connection.execute(delete(schema.live_risk_decisions))
        connection.execute(delete(schema.live_reconciliation_runs))
        connection.execute(delete(schema.live_readiness_checks))


@pytest.fixture
def engine():
    assert DATABASE_URL is not None
    value = create_engine(DATABASE_URL)
    schema.metadata.create_all(value)
    _cleanup(value)
    try:
        yield value
    finally:
        _cleanup(value)


def _activation_loader(_path: str, *, expected_git_sha: str, observed_at: datetime):
    raise ActivationManifestError("missing")


def _service(
    engine,
    *,
    settings: Settings | None = None,
    activation_loader=_activation_loader,
    kill_switch=True,
    geoblock: GeoblockResult | Exception | None = None,
    sdk_healthy=True,
    wallet_configured=False,
    stale_after_seconds=30,
) -> LiveReadinessService:
    def geoblock_check(*, observed_at: datetime) -> GeoblockResult:
        if isinstance(geoblock, Exception):
            raise geoblock
        if geoblock is None:
            raise GeoblockError("unavailable")
        return geoblock

    return LiveReadinessService(
        engine=engine,
        repository=LiveReadinessRepository(),
        settings=settings or Settings(),
        activation_loader=activation_loader,
        kill_switch_probe=lambda _path: kill_switch,
        geoblock_check=geoblock_check,
        sdk_health=lambda: sdk_healthy,
        wallet_configured=lambda: wallet_configured,
        reconciliation_stale_after_seconds=stale_after_seconds,
    )


def test_default_readiness_is_fail_closed_without_secure_client(engine) -> None:
    service = _service(
        engine,
        geoblock=GeoblockResult(
            blocked=False,
            country="NL",
            region="NH",
            checked_at=BASE,
        ),
    )

    check = service.build_readiness_check(expected_git_sha=GIT_SHA, observed_at=BASE)
    stored = service.store_readiness_check(check)
    report = service.get_report()

    assert check.eligible is False
    assert {
        "mode_not_live",
        "live_trading_disabled",
        "trade_size_limit_zero",
        "total_exposure_limit_zero",
        "daily_loss_limit_zero",
        "consecutive_loss_limit_zero",
        "activation_manifest_invalid",
        "kill_switch_engaged",
        "wallet_not_configured",
        "reconciliation_unavailable",
    }.issubset(set(check.reasons))
    assert stored.record["eligible"] is False
    assert report["eligible"] is False
    assert report["wallet_configured"] is False
    assert "private_key" not in repr(report).lower()


def test_geoblock_blocked_and_error_are_explicit_fail_closed_reasons(engine) -> None:
    blocked = _service(
        engine,
        geoblock=GeoblockResult(
            blocked=True,
            country="US",
            region="NY",
            checked_at=BASE,
        ),
    ).build_readiness_check(expected_git_sha=GIT_SHA, observed_at=BASE)
    errored = _service(
        engine,
        geoblock=GeoblockError("network detail that must not leak"),
    ).build_readiness_check(expected_git_sha=GIT_SHA, observed_at=BASE)

    assert "geographic_eligibility_blocked" in blocked.reasons
    assert blocked.evidence["geoblock"] == {
        "status": "ok",
        "blocked": True,
        "country": "US",
        "region": "NY",
    }
    assert "geoblock_error" in errored.reasons
    assert errored.evidence["geoblock"] == {
        "status": "error",
        "blocked": None,
        "country": None,
        "region": None,
    }
    assert "network detail" not in repr(errored.evidence)


def test_readiness_can_be_eligible_only_with_all_explicit_gates(engine) -> None:
    settings = Settings(
        mode="live",
        live_trading_enabled=True,
        max_trade_size_usd=5,
        max_total_exposure_usd=10,
        max_daily_loss_usd=5,
        max_consecutive_losses=2,
    )

    def activation_loader(
        _path: str,
        *,
        expected_git_sha: str,
        observed_at: datetime,
    ) -> ActivationManifest:
        return ActivationManifest(
            authorized=True,
            git_sha=expected_git_sha,
            authorization_id="auth-phase14-test",
            issued_at=observed_at - timedelta(minutes=1),
            expires_at=observed_at + timedelta(minutes=5),
        )

    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        repository.store_reconciliation_run(
            connection,
            observed_at=BASE - timedelta(seconds=1),
            unresolved_count=0,
            critical_count=0,
            evidence={"issues": []},
        )

    service = _service(
        engine,
        settings=settings,
        activation_loader=activation_loader,
        kill_switch=False,
        geoblock=GeoblockResult(
            blocked=False,
            country="NL",
            region="NH",
            checked_at=BASE,
        ),
        wallet_configured=True,
    )

    check = service.build_readiness_check(expected_git_sha=GIT_SHA, observed_at=BASE)

    assert check.eligible is True
    assert check.reasons == ()
    assert check.evidence["activation"]["authorized"] is True
    assert check.evidence["reconciliation"]["critical_count"] == 0


def _store_intent(
    engine,
    *,
    prediction_id: str = "prediction-1",
    token_id: str = "token-up",
    size: Decimal = Decimal("10"),
    limit_price: Decimal = Decimal("0.60"),
    external_order_id: str | None = "external-1",
    event_type: str = "accepted",
) -> str:
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        intent = repository.store_order_intent(
            connection,
            prediction_id=prediction_id,
            policy_version="live-risk-v1",
            request_id=f"request-{prediction_id}",
            risk_decision_id=f"risk-{prediction_id}",
            token_id=token_id,
            side="BUY",
            size=size,
            limit_price=limit_price,
            pre_submit_at=BASE - timedelta(seconds=5),
            evidence={"condition_id": "condition-1"},
        ).record
        if event_type:
            repository.store_order_event(
                connection,
                event_key=f"{intent['intent_id']}:{event_type}",
                intent_id=intent["intent_id"],
                event_type=event_type,
                observed_at=BASE - timedelta(seconds=4),
                external_order_id=external_order_id,
                external_trade_id=None,
                evidence={"status": event_type},
            )
    return str(intent["intent_id"])


def _official(
    *,
    external_order_id: str = "external-1",
    token_id: str = "token-up",
    side: str = "BUY",
    status: str = "open",
    original_size: Decimal = Decimal("10"),
    filled_size: Decimal = Decimal("0"),
    limit_price: Decimal = Decimal("0.60"),
    average_fill_price: Decimal | None = None,
    observed_at: datetime = BASE,
) -> OfficialOrderSnapshot:
    return OfficialOrderSnapshot(
        external_order_id=external_order_id,
        token_id=token_id,
        side=side,
        status=status,
        original_size=original_size,
        filled_size=filled_size,
        limit_price=limit_price,
        average_fill_price=average_fill_price,
        observed_at=observed_at,
    )


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_reconciliation_detects_intent_without_external_result(engine) -> None:
    _store_intent(engine, external_order_id=None, event_type="submission_unknown")

    result = _service(engine).reconcile_snapshot(official_orders=(), observed_at=BASE)

    assert "intent_without_external_result" in _codes(result)
    assert result.critical_discrepancy_count == 1


def test_reconciliation_detects_external_order_without_local_intent(engine) -> None:
    result = _service(engine).reconcile_snapshot(
        official_orders=(_official(external_order_id="orphan-order"),),
        observed_at=BASE,
    )

    assert "external_order_without_local_intent" in _codes(result)
    assert result.critical_discrepancy_count == 1


def test_reconciliation_detects_duplicate_external_ids(engine) -> None:
    _store_intent(engine)

    result = _service(engine).reconcile_snapshot(
        official_orders=(
            _official(),
            _official(status="partially_filled", filled_size=Decimal("1")),
        ),
        observed_at=BASE,
    )

    assert "duplicate_external_order_id" in _codes(result)
    assert result.critical_discrepancy_count >= 1


def test_reconciliation_detects_overfill_and_price_outside_limit(engine) -> None:
    _store_intent(engine)

    result = _service(engine).reconcile_snapshot(
        official_orders=(
            _official(
                status="filled",
                filled_size=Decimal("11"),
                average_fill_price=Decimal("0.61"),
            ),
        ),
        observed_at=BASE,
    )

    assert {"filled_amount_exceeds_request", "fill_price_above_limit"}.issubset(_codes(result))
    assert result.critical_discrepancy_count == 2


def test_reconciliation_detects_cancellation_disagreement(engine) -> None:
    intent_id = _store_intent(engine)
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        repository.store_order_event(
            connection,
            event_key=f"{intent_id}:cancelled",
            intent_id=intent_id,
            event_type="cancelled",
            observed_at=BASE - timedelta(seconds=2),
            external_order_id="external-1",
            external_trade_id=None,
            evidence={"status": "cancelled"},
        )

    result = _service(engine).reconcile_snapshot(
        official_orders=(_official(status="open"),),
        observed_at=BASE,
    )

    assert "cancellation_disagreement" in _codes(result)
    assert result.critical_discrepancy_count == 1


def test_reconciliation_detects_unknown_and_stale_external_state(engine) -> None:
    _store_intent(engine)

    unknown = _service(engine).reconcile_snapshot(
        official_orders=(_official(status="unknown"),),
        observed_at=BASE,
    )
    stale = _service(engine, stale_after_seconds=5).reconcile_snapshot(
        official_orders=(_official(observed_at=BASE - timedelta(seconds=6)),),
        observed_at=BASE,
    )

    assert "unknown_external_state" in _codes(unknown)
    assert "stale_external_state" in _codes(stale)


def test_clean_reconciliation_is_idempotent_and_persisted(engine) -> None:
    _store_intent(engine)
    service = _service(engine)
    official = (_official(),)

    first = service.reconcile_snapshot(official_orders=official, observed_at=BASE)
    second = service.reconcile_snapshot(official_orders=official, observed_at=BASE)

    assert first.unresolved_count == 0
    assert first.critical_discrepancy_count == 0
    assert first.issues == ()
    assert second == first
    with engine.begin() as connection:
        count = connection.scalar(
            select(func.count()).select_from(schema.live_reconciliation_runs)
        )
        stored = connection.execute(select(schema.live_reconciliation_runs)).mappings().one()
    assert count == 1
    assert stored["critical_count"] == 0
    assert stored["evidence"]["issues"] == []


def test_latest_critical_reconciliation_blocks_readiness(engine) -> None:
    _store_intent(engine, external_order_id=None, event_type="submission_unknown")
    service = _service(
        engine,
        geoblock=GeoblockResult(
            blocked=False,
            country="NL",
            region="NH",
            checked_at=BASE,
        ),
    )
    service.reconcile_snapshot(official_orders=(), observed_at=BASE)

    check = service.build_readiness_check(
        expected_git_sha=GIT_SHA,
        observed_at=BASE + timedelta(seconds=1),
    )

    assert "reconciliation_blocked" in check.reasons
    assert check.evidence["reconciliation"]["critical_count"] == 1
