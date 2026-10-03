from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, insert

from bp_engine.storage import schema
from bp_engine.v3_paper import service as v3_service

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "report_v3_feature_forensics.py"


def _load_module():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("v3_feature_forensics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_support_summary_distinguishes_cross_venue_confirmation() -> None:
    module = _load_module()
    features = {
        "coinbase_return_from_market_start": 0.002,
        "coinbase_return_30s": 0.001,
        "coinbase_return_60s": -0.001,
        "coinbase_return_120s": 0.003,
        "bybit_spot_return_from_market_start": -0.001,
        "bybit_spot_return_30s": 0.001,
        "bybit_spot_return_60s": 0.001,
        "bybit_spot_return_120s": 0.002,
        "bybit_linear_return_from_market_start": 0.002,
        "bybit_linear_return_30s": 0.001,
        "bybit_linear_return_60s": 0.001,
        "bybit_linear_return_120s": 0.002,
    }

    summary = module._support_summary(features, selected_side="up")

    assert summary["market_start_venue_votes"] == {
        "support": 2,
        "oppose": 1,
        "zero": 0,
        "present": 3,
    }
    assert summary["all_return_votes"]["support"] == 10
    assert summary["all_return_votes"]["oppose"] == 2
    assert summary["coinbase_selected_side_signed_bps"] == 20.0
    assert summary["bybit_spot_selected_side_signed_bps"] == -10.0


def test_cohort_summary_reports_signed_btc_support_and_pnl() -> None:
    module = _load_module()
    rows = [
        {
            "correct": True,
            "realized_pnl_usd": "4.0",
            "feature_support": {
                "coinbase_selected_side_signed_bps": 20.0,
                "bybit_spot_selected_side_signed_bps": 18.0,
                "bybit_linear_selected_side_signed_bps": 19.0,
                "coinbase_abs_move_bps": 20.0,
                "market_start_venue_votes": {"support": 3},
                "all_return_votes": {"support": 10},
            },
        },
        {
            "correct": False,
            "realized_pnl_usd": "-3.0",
            "feature_support": {
                "coinbase_selected_side_signed_bps": 5.0,
                "bybit_spot_selected_side_signed_bps": -4.0,
                "bybit_linear_selected_side_signed_bps": -3.0,
                "coinbase_abs_move_bps": 5.0,
                "market_start_venue_votes": {"support": 1},
                "all_return_votes": {"support": 4},
            },
        },
    ]

    summary = module._cohort_summary(rows)

    assert summary["trade_count"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["realized_pnl_usd"] == "1.0"
    assert summary["coinbase_selected_side_signed_bps"]["median"] == 12.5
    assert summary["market_start_support_votes"]["mean"] == 2.0


def test_forensics_source_requires_exact_input_replay() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "FROZEN_CANDIDATE",
        "FROZEN_MODEL_SHA256",
        "FROZEN_OFFSET_SECONDS",
        "build_v3_feature",
        "_books",
        "_book_descriptor",
        "input_fingerprint",
        "market_probability_response_sha256",
        "fingerprint_match",
        "book_hash_match",
        "provider_source_time_claimed",
        "v4_holdout_labels_read",
    ):
        assert marker in source

    assert "schema.live_predictions" in source
    assert "schema.market_features" not in source
    assert "official-outcome-v1" not in source


START = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
END = START + timedelta(seconds=300)
SCHEDULED = START + timedelta(seconds=240)


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


def _seed_inputs(connection, condition_id: str) -> None:
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

    for side, bid, ask in (
        ("up", "0.39", "0.40"),
        ("down", "0.59", "0.60"),
    ):
        _insert_state(
            connection,
            source="polymarket",
            stream="market",
            instrument=condition_id,
            effective_at=SCHEDULED - timedelta(seconds=1),
            asset_id=f"{condition_id}-{side}",
            state={"best_bid": bid, "best_ask": ask},
        )


def _bundle() -> dict[str, object]:
    x = [[-0.04], [-0.02], [0.02], [0.04]]
    y = [0, 0, 1, 1]
    imputer = SimpleImputer(strategy="median").fit(x)
    transformed = imputer.transform(x)
    scaler = StandardScaler().fit(transformed)
    estimator = LogisticRegression(random_state=0).fit(
        scaler.transform(transformed),
        y,
    )
    return {
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


def test_replay_recovers_exact_prediction_input_fingerprint() -> None:
    module = _load_module()
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    condition_id = "forensic-replay"
    market = v3_service.V3PaperMarket(
        condition_id=condition_id,
        slug="btc-updown-5m-forensic",
        horizon_seconds=300,
        market_start_at=START,
        market_end_at=END,
        scheduled_at=SCHEDULED,
        up_token_id=f"{condition_id}-up",
        down_token_id=f"{condition_id}-down",
    )
    try:
        with engine.begin() as connection:
            _seed_inputs(connection, condition_id)
            prediction = v3_service.build_v3_paper_prediction(
                connection,
                market=market,
                bundle=_bundle(),
                clock=lambda: SCHEDULED + timedelta(seconds=1),
            )
            replay = module._replay_inputs(connection, asdict(prediction))

        assert replay["fingerprint_match"] is True
        assert replay["book_hash_match"] is True
        assert replay["model_predictor_names"] == [
            "coinbase_return_from_market_start",
            "missing__coinbase_market_start_missing",
            "missing__coinbase_market_start_stale",
            "missing__coinbase_current_missing",
            "missing__coinbase_current_stale",
        ]
        assert replay["features"]["coinbase_return_from_market_start"] > 0
        assert replay["features"]["bybit_spot_return_from_market_start"] > 0
        assert replay["features"]["bybit_linear_return_from_market_start"] > 0
    finally:
        engine.dispose()
