from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode
from bp_engine.v3_paper.service import (
    FROZEN_MODEL_SHA256,
    V3PaperPredictionService,
    load_activation,
    load_frozen_model,
)

_STOP = False


def _request_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _settings(env_file: str | None) -> Settings:
    return Settings(_env_file=env_file) if env_file else Settings()


def require_zero_money_research(settings: Settings) -> None:
    if settings.mode is not TradingMode.RESEARCH:
        raise RuntimeError("V3 paper predictor requires MODE=research")
    if settings.live_trading_enabled:
        raise RuntimeError("V3 paper predictor requires LIVE_TRADING_ENABLED=false")
    if Decimal(str(settings.max_trade_size_usd)) != Decimal("0"):
        raise RuntimeError("V3 paper predictor requires MAX_TRADE_SIZE_USD=0")
    if Decimal(str(settings.max_daily_loss_usd)) != Decimal("0"):
        raise RuntimeError("V3 paper predictor requires MAX_DAILY_LOSS_USD=0")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run frozen V3 prospective zero-money paper signals"
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--activation", required=True)
    parser.add_argument("--verify-model", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    return parser


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    global _STOP
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")

    settings = _settings(args.env_file)
    require_zero_money_research(settings)
    activation = load_activation(args.activation)
    model = load_frozen_model(args.model)
    if args.verify_model:
        print(
            json.dumps(
                {
                    "verified": True,
                    "model_path": str(Path(args.model)),
                    "model_sha256": FROZEN_MODEL_SHA256,
                    "activated_at": activation.activated_at.isoformat(),
                },
                sort_keys=True,
            )
        )
        return 0

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    service = V3PaperPredictionService(
        engine=engine,
        activation=activation,
        model_bundle=model,
    )

    if args.once:
        report = service.run_once(now=datetime.now(UTC))
        print(json.dumps(_json(asdict(report)), sort_keys=True))
        engine.dispose()
        return 0

    _STOP = False
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        while not _STOP:
            report = service.run_once(now=datetime.now(UTC))
            print(json.dumps(_json(asdict(report)), sort_keys=True), flush=True)
            deadline = time.monotonic() + args.poll_seconds
            while not _STOP and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        engine.dispose()
    return 0
