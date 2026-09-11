from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.features.v2_models import V2_FEATURE_VERSION
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema


def _prediction() -> LivePrediction:
    start = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    recorded = scheduled + timedelta(seconds=1)
    slug = f"btc-updown-5m-{int(start.timestamp())}"
    return LivePrediction(
        prediction_id="e" * 64,
        semantic_sha256="1" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="prospective-outcome-sync",
        slug=slug,
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded,
        lateness_ms=1000,
        up_token_id="sync-up",
        down_token_id="sync-down",
        source_calibration_run_id="phase9-sync",
        source_calibration_semantic_sha256="2" * 64,
        source_backtest_run_id="phase8-sync",
        source_backtest_semantic_sha256="3" * 64,
        source_training_run_id="phase7-sync",
        source_training_semantic_sha256="4" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="5" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="6" * 64,
        edge_config={"fee_rate": 0.07, "slippage_buffer": 0.01},
        edge_config_sha256="7" * 64,
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=0.62,
        calibrated_probability=0.64,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.62,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled + timedelta(milliseconds=500),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "sync-up", "fidelity": "1"},
        market_probability_response_sha256="8" * 64,
        up_best_bid=0.54,
        up_best_ask=0.56,
        up_book_cutoff_at=scheduled,
        up_book_fresh=True,
        down_best_bid=0.44,
        down_best_ask=0.46,
        down_book_cutoff_at=scheduled,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=True,
        decision_reason="trade",
        selected_ask=0.56,
        selected_bid=0.54,
        selected_spread=0.02,
        fee=0.017248,
        slippage_buffer=0.01,
        raw_edge=0.08,
        cost_adjusted_edge=0.052752,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True},
        input_fingerprint="9" * 64,
    )


def _resolved_gamma_payload(prediction: LivePrediction) -> dict[str, object]:
    return {
        "id": "gamma-prospective-outcome-sync",
        "conditionId": prediction.condition_id,
        "slug": prediction.slug,
        "question": "Bitcoin Up or Down?",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["sync-up", "sync-down"]',
        "outcomePrices": '["1", "0"]',
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "description": "Resolves Up when the stated BTC TWAP is at least the opening value.",
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "events": [{"id": "event-prospective-outcome-sync"}],
    }


class FakeGammaClient:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def get_market_by_slug(self, slug: str) -> dict[str, object] | None:
        self.calls.append(slug)
        return self.payload


def _engine_with_prediction() -> tuple[object, LivePrediction]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction = _prediction()
    with engine.begin() as connection:
        LivePredictionRepository().store(connection, prediction)
    return engine, prediction


def _insert_v2_feature_market(engine: object, prediction: LivePrediction) -> None:
    with engine.begin() as connection:
        connection.execute(
            schema.polymarket_markets.insert().values(
                gamma_market_id="gamma-prospective-outcome-sync",
                event_id="event-prospective-outcome-sync",
                condition_id=prediction.condition_id,
                slug=prediction.slug,
                question="Bitcoin Up or Down?",
                horizon_seconds=prediction.horizon_seconds,
                start_at=prediction.market_start_at,
                end_at=prediction.market_end_at,
                up_token_id=prediction.up_token_id,
                down_token_id=prediction.down_token_id,
                resolution_source="https://data.chain.link/streams/btc-usd",
                rules_text="Resolves from the official BTC reference.",
                rules_hash="a" * 64,
                active=False,
                closed=True,
                accepting_orders=False,
                resolved_outcome=None,
                discovered_at=prediction.market_start_at,
                updated_at=prediction.market_end_at,
            )
        )
        for offset in (60, 120, 180, 240):
            feature_at = prediction.market_start_at + timedelta(seconds=offset)
            connection.execute(
                schema.market_features.insert().values(
                    condition_id=prediction.condition_id,
                    slug=prediction.slug,
                    horizon_seconds=prediction.horizon_seconds,
                    market_start_at=prediction.market_start_at,
                    market_end_at=prediction.market_end_at,
                    feature_at=feature_at,
                    feature_offset_seconds=offset,
                    feature_version=V2_FEATURE_VERSION,
                    features={"market_price": 0.5},
                    missing_flags={},
                    source_cutoffs={},
                    input_fingerprint=f"{offset:064x}",
                    feature_hash=f"{offset + 1:064x}",
                    generated_at=feature_at,
                )
            )


@pytest.mark.asyncio
async def test_sync_resolved_prediction_creates_snapshot_label_and_evaluation() -> None:
    from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

    engine, prediction = _engine_with_prediction()
    client = FakeGammaClient(_resolved_gamma_payload(prediction))

    report = await ProspectiveOutcomeSyncService(engine=engine, client=client).run_once(
        now=prediction.market_end_at + timedelta(minutes=1)
    )

    with engine.begin() as connection:
        snapshot_count = connection.scalar(
            select(func.count()).select_from(schema.polymarket_market_snapshots)
        )
        label_count = connection.scalar(select(func.count()).select_from(schema.market_labels))
        evaluation = connection.execute(
            select(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == prediction.prediction_id
            )
        ).mappings().one()

    assert client.calls == [prediction.slug]
    assert report.candidates == 1
    assert report.resolved_markets == 1
    assert report.created_snapshots == 1
    assert report.created_labels == 1
    assert report.created_evaluations == 1
    assert snapshot_count == 1
    assert label_count == 1
    assert evaluation["official_outcome"] == "Up"
    assert evaluation["label_source_snapshot_sha256"]
    assert len(evaluation["label_source_snapshot_sha256"]) == 64


@pytest.mark.asyncio
async def test_multiple_prediction_versions_for_same_market_are_not_collapsed() -> None:
    from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

    engine, prediction = _engine_with_prediction()
    second = replace(
        prediction,
        prediction_id="f" * 64,
        semantic_sha256="a" * 64,
        prediction_version="live-prediction-v2",
    )
    with engine.begin() as connection:
        LivePredictionRepository().store(connection, second)
    client = FakeGammaClient(_resolved_gamma_payload(prediction))

    report = await ProspectiveOutcomeSyncService(engine=engine, client=client).run_once(
        now=prediction.market_end_at + timedelta(minutes=1)
    )

    with engine.begin() as connection:
        evaluation_count = connection.scalar(
            select(func.count()).select_from(schema.live_prediction_evaluations)
        )

    assert report.candidates == 2
    assert client.calls == [prediction.slug, prediction.slug]
    assert report.created_evaluations == 2
    assert evaluation_count == 2


@pytest.mark.asyncio
async def test_sync_resolved_v2_feature_market_without_prediction_creates_label() -> None:
    from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction = _prediction()
    _insert_v2_feature_market(engine, prediction)
    client = FakeGammaClient(_resolved_gamma_payload(prediction))

    report = await ProspectiveOutcomeSyncService(engine=engine, client=client).run_once(
        now=prediction.market_end_at + timedelta(minutes=1)
    )

    with engine.begin() as connection:
        snapshot_count = connection.scalar(
            select(func.count()).select_from(schema.polymarket_market_snapshots)
        )
        label_count = connection.scalar(select(func.count()).select_from(schema.market_labels))
        evaluation_count = connection.scalar(
            select(func.count()).select_from(schema.live_prediction_evaluations)
        )

    assert client.calls == [prediction.slug]
    assert report.candidates == 1
    assert report.resolved_markets == 1
    assert report.created_snapshots == 1
    assert report.created_labels == 1
    assert report.created_evaluations == 0
    assert snapshot_count == 1
    assert label_count == 1
    assert evaluation_count == 0


@pytest.mark.asyncio
async def test_unresolved_market_remains_pending_without_writes() -> None:
    from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

    engine, prediction = _engine_with_prediction()
    payload = _resolved_gamma_payload(prediction)
    payload["closed"] = False
    payload["active"] = True
    payload["outcomePrices"] = '["0.5", "0.5"]'
    client = FakeGammaClient(payload)

    report = await ProspectiveOutcomeSyncService(engine=engine, client=client).run_once(
        now=prediction.market_end_at + timedelta(minutes=1)
    )

    with engine.begin() as connection:
        assert connection.scalar(
            select(func.count()).select_from(schema.polymarket_market_snapshots)
        ) == 0
        assert connection.scalar(select(func.count()).select_from(schema.market_labels)) == 0
        assert connection.scalar(
            select(func.count()).select_from(schema.live_prediction_evaluations)
        ) == 0
    assert report.candidates == 1
    assert report.pending_markets == 1
    assert report.resolved_markets == 0


@pytest.mark.asyncio
async def test_gamma_identity_mismatch_fails_closed_before_any_write() -> None:
    from bp_engine.prospective_outcomes.service import (
        ProspectiveOutcomeIntegrityError,
        ProspectiveOutcomeSyncService,
    )

    engine, prediction = _engine_with_prediction()
    payload = _resolved_gamma_payload(prediction)
    payload["conditionId"] = "wrong-condition"
    client = FakeGammaClient(payload)

    with pytest.raises(ProspectiveOutcomeIntegrityError, match="identity"):
        await ProspectiveOutcomeSyncService(engine=engine, client=client).run_once(
            now=prediction.market_end_at + timedelta(minutes=1)
        )

    with engine.begin() as connection:
        assert connection.scalar(
            select(func.count()).select_from(schema.polymarket_market_snapshots)
        ) == 0
        assert connection.scalar(select(func.count()).select_from(schema.market_labels)) == 0
        assert connection.scalar(
            select(func.count()).select_from(schema.live_prediction_evaluations)
        ) == 0


@pytest.mark.asyncio
async def test_completed_evaluation_is_not_refetched_on_rerun() -> None:
    from bp_engine.prospective_outcomes.service import ProspectiveOutcomeSyncService

    engine, prediction = _engine_with_prediction()
    client = FakeGammaClient(_resolved_gamma_payload(prediction))
    service = ProspectiveOutcomeSyncService(engine=engine, client=client)
    now = prediction.market_end_at + timedelta(minutes=1)

    first = await service.run_once(now=now)
    second = await service.run_once(now=now + timedelta(minutes=1))

    assert first.created_evaluations == 1
    assert second.candidates == 0
    assert second.created_snapshots == 0
    assert second.created_labels == 0
    assert second.created_evaluations == 0
    assert client.calls == [prediction.slug]
