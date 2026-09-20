from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import pytest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v3_paper import service as module

START = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
END = START + timedelta(seconds=300)
SCHEDULED = START + timedelta(seconds=240)
ACTIVATED = START - timedelta(minutes=1)


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _activation(*, activated_at: datetime = ACTIVATED) -> module.V3PaperActivation:
    return module.V3PaperActivation(
        activated_at=activated_at,
        candidate_head="a" * 40,
        model_sha256=module.FROZEN_MODEL_SHA256,
        prediction_version=module.V3_PAPER_PREDICTION_VERSION,
        execution_version=module.V3_PAPER_EXECUTION_VERSION,
        paper_starting_cash_usd="100.00",
        paper_target_notional_usd="5.00",
    )


def _insert_market(
    connection,
    *,
    condition_id: str,
    start_at: datetime = START,
    active: bool = True,
    closed: bool = False,
) -> None:
    connection.execute(
        insert(schema.polymarket_markets).values(
            gamma_market_id=f"gamma-{condition_id}",
            event_id=f"event-{condition_id}",
            condition_id=condition_id,
            slug=f"btc-updown-5m-{condition_id}",
            question=f"market {condition_id}",
            horizon_seconds=300,
            start_at=start_at,
            end_at=start_at + timedelta(seconds=300),
            up_token_id=f"{condition_id}-up",
            down_token_id=f"{condition_id}-down",
            resolution_source="Chainlink",
            rules_text="rules",
            rules_hash="sha256:" + "1" * 64,
            active=active,
            closed=closed,
            accepting_orders=active and not closed,
            resolved_outcome=None,
            discovered_at=start_at - timedelta(minutes=1),
            updated_at=start_at,
        )
    )


def _insert_state(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    effective_at: datetime,
    state: dict[str, object],
    asset_id: str | None = None,
) -> None:
    suffix = asset_id or "none"
    connection.execute(
        insert(schema.market_state_1s).values(
            bucket_at=effective_at,
            state_key=f"{source}/{stream}/{instrument}/{suffix}/{effective_at.timestamp()}",
            source=source,
            stream=stream,
            instrument=instrument,
            market_id=instrument if source == "polymarket" else None,
            asset_id=asset_id,
            last_event_at=effective_at,
            state=state,
        )
    )


def _seed_btc(connection) -> None:
    times = (
        START - timedelta(seconds=1),
        START + timedelta(seconds=119),
        START + timedelta(seconds=179),
        START + timedelta(seconds=209),
        START + timedelta(seconds=239),
    )
    venues = (
        ("coinbase", "spot", "BTC-USD", (100, 101, 102, 103, 104)),
        ("bybit", "spot", "BTCUSDT", (200, 201, 202, 203, 204)),
        ("bybit", "linear", "BTCUSDT", (201, 202, 203, 204, 205)),
    )
    for source, stream, instrument, prices in venues:
        for index, (effective_at, price) in enumerate(zip(times, prices, strict=True)):
            extra = {}
            if stream == "linear" and index == len(times) - 1:
                extra = {"funding_rate": "0.0001", "open_interest": "1000"}
            _insert_state(
                connection,
                source=source,
                stream=stream,
                instrument=instrument,
                effective_at=effective_at,
                state={"last_price": str(price), **extra},
            )


def _seed_book(
    connection,
    *,
    condition_id: str,
    effective_at: datetime | None = None,
    up_bid: str = "0.39",
    up_ask: str = "0.40",
    down_bid: str = "0.59",
    down_ask: str = "0.60",
) -> None:
    at = effective_at or (SCHEDULED - timedelta(seconds=1))
    _insert_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=condition_id,
        effective_at=at,
        asset_id=f"{condition_id}-up",
        state={"best_bid": up_bid, "best_ask": up_ask},
    )
    _insert_state(
        connection,
        source="polymarket",
        stream="market",
        instrument=condition_id,
        effective_at=at,
        asset_id=f"{condition_id}-down",
        state={"best_bid": down_bid, "best_ask": down_ask},
    )


def _bundle() -> dict[str, object]:
    x = [[-0.04], [-0.02], [0.02], [0.04]]
    y = [0, 0, 1, 1]
    imputer = SimpleImputer(strategy="median").fit(x)
    transformed = imputer.transform(x)
    scaler = StandardScaler().fit(transformed)
    estimator = LogisticRegression(random_state=0).fit(scaler.transform(transformed), y)
    return {
        "research_plan_version": module.FROZEN_RESEARCH_PLAN_VERSION,
        "plan_sha256": module.FROZEN_PLAN_SHA256,
        "candidate": module.FROZEN_CANDIDATE,
        "family": module.FROZEN_FAMILY,
        "offset_seconds": module.FROZEN_OFFSET_SECONDS,
        "edge_policy": module.FROZEN_EDGE_POLICY,
        "selected_min_edge": module.FROZEN_MIN_EDGE,
        "fee_rate": module.FROZEN_FEE_RATE,
        "slippage_buffer": module.FROZEN_SLIPPAGE_BUFFER,
        "max_selected_book_age_seconds": module.FROZEN_MAX_BOOK_AGE_SECONDS,
        "predictor_names": ("coinbase_return_from_market_start",),
        "imputer": imputer,
        "scaler": scaler,
        "estimator": estimator,
        "calibration_fit": {
            "method": "identity",
            "intercept": None,
            "coefficient": None,
        },
    }


def test_activation_manifest_freezes_zero_money_paper_identity(tmp_path: Path) -> None:
    path = tmp_path / "activation.json"
    path.write_text(
        json.dumps(
            {
                "activated_at": ACTIVATED.isoformat(),
                "candidate_head": "a" * 40,
                "model_sha256": module.FROZEN_MODEL_SHA256,
                "prediction_version": module.V3_PAPER_PREDICTION_VERSION,
                "execution_version": module.V3_PAPER_EXECUTION_VERSION,
                "paper_starting_cash_usd": "100.00",
                "paper_target_notional_usd": "5.00",
            }
        ),
        encoding="utf-8",
    )

    activation = module.load_activation(path)

    assert activation.activated_at == ACTIVATED
    assert activation.model_sha256 == module.FROZEN_MODEL_SHA256

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["paper_target_notional_usd"] = "100.00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.V3PaperIntegrityError, match="target notional"):
        module.load_activation(path)


def test_frozen_model_loader_checks_hash_and_contract(tmp_path: Path) -> None:
    path = tmp_path / "model.joblib"
    joblib.dump(_bundle(), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    loaded = module.load_frozen_model(path, expected_sha256=digest)
    assert loaded["candidate"] == module.FROZEN_CANDIDATE

    with pytest.raises(module.V3PaperIntegrityError, match="SHA mismatch"):
        module.load_frozen_model(path, expected_sha256="0" * 64)


def test_due_market_discovery_never_backfills_before_activation() -> None:
    engine = _engine()
    with engine.begin() as connection:
        _insert_market(
            connection,
            condition_id="before",
            start_at=ACTIVATED - timedelta(minutes=5),
        )
        _insert_market(connection, condition_id="eligible")
        due = module.discover_due_markets(
            connection,
            now=SCHEDULED + timedelta(seconds=1),
            activation=_activation(),
        )

    assert [market.condition_id for market in due] == ["eligible"]


def test_v3_prediction_uses_btc_for_forecast_and_polymarket_only_for_execution() -> None:
    engine = _engine()
    condition_id = "btc-only"
    with engine.begin() as connection:
        _insert_market(connection, condition_id=condition_id)
        _seed_btc(connection)
        _seed_book(connection, condition_id=condition_id)
        market = module.discover_due_markets(
            connection,
            now=SCHEDULED + timedelta(seconds=1),
            activation=_activation(),
        )[0]
        prediction = module.build_v3_paper_prediction(
            connection,
            market=market,
            bundle=_bundle(),
            clock=lambda: SCHEDULED + timedelta(seconds=1),
        )

    assert prediction.prediction_version == module.V3_PAPER_PREDICTION_VERSION
    assert prediction.source_feature_version == "core-v3-btc-native"
    assert prediction.market_probability_observed is False
    assert prediction.market_probability is None
    assert prediction.edge_decision["forecast_source"] == "frozen_v3_btc_model"
    assert prediction.edge_decision["polymarket_role"] == "execution_only"
    assert prediction.selected_ask in {0.40, 0.60}
    assert prediction.decision_min_edge == module.FROZEN_MIN_EDGE


def test_future_state_after_scheduled_time_cannot_change_prediction() -> None:
    engine = _engine()
    condition_id = "future-safe"
    fixed_clock = lambda: SCHEDULED + timedelta(seconds=1)
    with engine.begin() as connection:
        _insert_market(connection, condition_id=condition_id)
        _seed_btc(connection)
        _seed_book(connection, condition_id=condition_id)
        market = module.discover_due_markets(
            connection,
            now=SCHEDULED + timedelta(seconds=1),
            activation=_activation(),
        )[0]
        before = module.build_v3_paper_prediction(
            connection,
            market=market,
            bundle=_bundle(),
            clock=fixed_clock,
        )
        _insert_state(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            effective_at=SCHEDULED + timedelta(seconds=1),
            state={"last_price": "999999"},
        )
        _seed_book(
            connection,
            condition_id=condition_id,
            effective_at=SCHEDULED + timedelta(seconds=1),
            up_ask="0.99",
            down_ask="0.99",
        )
        after = module.build_v3_paper_prediction(
            connection,
            market=market,
            bundle=_bundle(),
            clock=fixed_clock,
        )

    assert before.raw_probability == after.raw_probability
    assert before.calibrated_probability == after.calibrated_probability
    assert before.selected_ask == after.selected_ask
    assert before.semantic_sha256 == after.semantic_sha256


def test_post_compute_deadline_is_fail_closed() -> None:
    engine = _engine()
    condition_id = "deadline"
    times = iter(
        (
            SCHEDULED,
            SCHEDULED + timedelta(seconds=11),
        )
    )
    with engine.begin() as connection:
        _insert_market(connection, condition_id=condition_id)
        _seed_btc(connection)
        _seed_book(connection, condition_id=condition_id)
        market = module.discover_due_markets(
            connection,
            now=SCHEDULED,
            activation=_activation(),
        )[0]
        with pytest.raises(module.V3PaperIntegrityError, match="deadline"):
            module.build_v3_paper_prediction(
                connection,
                market=market,
                bundle=_bundle(),
                clock=lambda: next(times),
            )


def test_v3_paper_service_has_no_wallet_signer_or_live_order_dependency() -> None:
    source = inspect.getsource(module).lower()
    forbidden = (
        "bp_engine.execution",
        "private_key",
        "wallet_address",
        "signing",
        "submit_order",
        "live_gateway",
    )
    assert all(value not in source for value in forbidden)
