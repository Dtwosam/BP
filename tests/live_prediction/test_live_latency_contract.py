from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.backfill.polymarket_prices import PriceHistoryResponse
from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.live_prediction.inputs import observe_live_input
from bp_engine.live_prediction.models import LivePolicySpec
from bp_engine.live_prediction.service import LivePredictionService
from bp_engine.storage import schema


class BudgetAwarePriceHistoryClient:
    def __init__(self, scheduled_at: datetime) -> None:
        self.scheduled_at = scheduled_at
        self.timeout_seconds: float | None = None

    async def get_history(
        self,
        asset_id: str,
        *,
        start: datetime,
        end: datetime,
        fidelity_minutes: int,
        timeout_seconds: float,
    ) -> PriceHistoryResponse:
        self.timeout_seconds = timeout_seconds
        return PriceHistoryResponse(
            points=(),
            request_params={
                "market": asset_id,
                "startTs": str(int(start.timestamp())),
                "endTs": str(int(end.timestamp())),
                "fidelity": str(fidelity_minutes),
            },
            raw_payload={"history": []},
        )


def _policy() -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id="phase9-300-latency",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id="phase8-300-latency",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id="phase7-300-latency",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        selected_offset_seconds=240,
        calibration_fit=CalibrationFit(method="identity", intercept=None, coefficient=None),
        edge_config=EdgeConfig(
            fee_rate=0.07,
            slippage_buffer=0.01,
            min_edge_grid=(0.02,),
            min_validation_trades=1,
        ),
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def _market(condition_id: str, start_at: datetime) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{condition_id}",
        "event_id": f"event-{condition_id}",
        "condition_id": condition_id,
        "slug": f"btc-updown-{condition_id}",
        "question": "Will BTC be up?",
        "horizon_seconds": 300,
        "start_at": start_at,
        "end_at": start_at + timedelta(seconds=300),
        "up_token_id": f"up-{condition_id}",
        "down_token_id": f"down-{condition_id}",
        "resolution_source": "official-rules",
        "rules_text": "Up if final reference exceeds start reference.",
        "rules_hash": f"rules-{condition_id}",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "resolved_outcome": None,
        "discovered_at": start_at - timedelta(minutes=5),
        "updated_at": start_at,
    }


@pytest.mark.asyncio
async def test_live_input_bounds_history_request_by_remaining_deadline() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    client = BudgetAwarePriceHistoryClient(scheduled)

    with engine.begin() as connection:
        await observe_live_input(
            connection,
            client,
            condition_id="latency-budget",
            up_token_id="up-latency-budget",
            down_token_id="down-latency-budget",
            market_start_at=start,
            market_end_at=start + timedelta(minutes=5),
            scheduled_at=scheduled,
            clock=lambda: scheduled + timedelta(seconds=2),
            max_lateness_seconds=10,
        )

    assert client.timeout_seconds is not None
    assert 0 < client.timeout_seconds < 8


@pytest.mark.asyncio
async def test_run_once_processes_simultaneous_due_markets_concurrently() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    start = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)
    policy = _policy()
    scheduled = start + timedelta(seconds=policy.selected_offset_seconds)

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets),
            [_market("latency-a", start), _market("latency-b", start)],
        )

    service = LivePredictionService(
        engine=engine,
        policies={300: policy},
        client=object(),
        evaluator=lambda connection, *, evaluated_at: None,
        clock=lambda: scheduled + timedelta(seconds=1),
    )
    started: list[str] = []
    both_started = asyncio.Event()
    active = 0
    max_active = 0

    async def process_market(market):
        nonlocal active, max_active
        started.append(market.condition_id)
        active += 1
        max_active = max(max_active, active)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        active -= 1
        return True, False, False

    service._process_market = process_market  # type: ignore[method-assign]
    result = await service.run_once()

    assert sorted(started) == ["latency-a", "latency-b"]
    assert max_active == 2
    assert result.created_predictions == 2
    assert result.failed_markets == 0


@pytest.mark.asyncio
async def test_live_runtime_uses_one_persistent_clob_http_client(monkeypatch) -> None:
    module = importlib.import_module("bp_engine.live_prediction.cli")
    http_clients: list[object] = []
    service_clients: list[object] = []

    class FakeHttpClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            http_clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeService:
        def __init__(self, *, client, **kwargs) -> None:
            service_clients.append(client)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeHttpClient)
    monkeypatch.setattr(module, "LivePredictionService", FakeService)

    await module._run_live_service(
        engine=object(),
        policies={300: _policy()},
        max_lateness_seconds=10,
        poll_interval_seconds=1.0,
    )

    assert len(http_clients) == 1
    assert len(service_clients) == 1
    assert service_clients[0]._http_client is http_clients[0]
