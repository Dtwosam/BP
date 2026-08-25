from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, create_engine

from bp_engine.backfill.bybit import (
    BybitHistoryClient,
    BybitHistoryUnavailableError,
    backfill_bybit_candles,
)
from bp_engine.backfill.coinbase import CoinbaseHistoryClient, backfill_coinbase_candles
from bp_engine.backfill.polymarket import backfill_polymarket_markets
from bp_engine.backfill.polymarket_prices import (
    PolymarketPriceHistoryClient,
    backfill_polymarket_prices,
)
from bp_engine.backfill.provenance import (
    BackfillRun,
    BackfillStats,
    ProvenanceRepository,
)
from bp_engine.config import Settings
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.storage.schema import metadata

STANDARD_SEQUENCE = (
    "polymarket_markets",
    "polymarket_prices",
    "bybit_spot",
    "bybit_linear",
    "coinbase_spot",
)

_DATASET_SOURCE = {
    "polymarket_markets": "polymarket_gamma",
    "polymarket_prices": "polymarket_clob",
    "bybit_spot": "bybit",
    "bybit_linear": "bybit",
    "coinbase_spot": "coinbase",
}


def parse_datetime(value: str) -> datetime:
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset or Z")
    return parsed.astimezone(UTC)


def validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", required=True, type=parse_datetime)
    parser.add_argument("--end", required=True, type=parse_datetime)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--database-url", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run reproducible, idempotent BP historical backfills"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    markets = subparsers.add_parser(
        "polymarket-markets",
        help="Backfill historical BTC Up/Down market metadata",
    )
    _add_common_options(markets)
    markets.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        default=None,
        help="BTC horizon such as 5m or 15m; repeat to include multiple horizons",
    )

    prices = subparsers.add_parser(
        "polymarket-prices",
        help="Backfill historical Up/Down token price series",
    )
    _add_common_options(prices)
    prices.add_argument("--fidelity-minutes", type=int, default=1)

    btc = subparsers.add_parser(
        "btc-candles",
        help="Backfill BTC reference candles",
    )
    _add_common_options(btc)
    btc.add_argument("--interval-seconds", type=int, default=60)
    btc.add_argument(
        "--source",
        choices=("all", "bybit-spot", "bybit-linear", "coinbase-spot"),
        default="all",
    )

    standard = subparsers.add_parser(
        "standard",
        help="Run the complete Phase 4 historical backfill sequence",
    )
    _add_common_options(standard)
    standard.add_argument("--fidelity-minutes", type=int, default=1)
    standard.add_argument("--interval-seconds", type=int, default=60)
    standard.add_argument(
        "--require-bybit",
        action="store_true",
        help=(
            "fail if Bybit historical REST is unavailable; by default an HTTP 403 is "
            "audited as unavailable while core sources continue"
        ),
    )
    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if args.database_url:
        settings = settings.model_copy(update={"database_url": args.database_url})
    return settings


def _run_parameters(
    name: str,
    *,
    settings: Settings,
    fidelity_minutes: int,
    interval_seconds: int,
    horizons: tuple[str, ...] | None,
) -> dict[str, Any]:
    if name == "polymarket_markets":
        return {"horizons": list(horizons or settings.active_horizons)}
    if name == "polymarket_prices":
        return {"fidelity_minutes": fidelity_minutes}
    if name in {"bybit_spot", "bybit_linear"}:
        return {
            "symbol": "BTCUSDT",
            "category": name.removeprefix("bybit_"),
            "interval_seconds": interval_seconds,
        }
    if name == "coinbase_spot":
        return {"product_id": "BTC-USD", "interval_seconds": interval_seconds}
    raise ValueError(f"unknown historical dataset: {name}")


async def _execute_dataset(
    name: str,
    connection: Any,
    *,
    run_id: str,
    start: datetime,
    end: datetime,
    settings: Settings,
    downloaded_at: datetime,
    fidelity_minutes: int,
    interval_seconds: int,
    horizons: tuple[str, ...] | None,
) -> BackfillStats:
    if name == "polymarket_markets":
        return await backfill_polymarket_markets(
            connection,
            GammaClient(),
            run_id=run_id,
            start=start,
            end=end,
            horizons=horizons or settings.active_horizons,
            downloaded_at=downloaded_at,
        )
    if name == "polymarket_prices":
        return await backfill_polymarket_prices(
            connection,
            PolymarketPriceHistoryClient(),
            run_id=run_id,
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            fidelity_minutes=fidelity_minutes,
        )
    if name in {"bybit_spot", "bybit_linear"}:
        return await backfill_bybit_candles(
            connection,
            BybitHistoryClient(),
            run_id=run_id,
            category=name.removeprefix("bybit_"),
            symbol="BTCUSDT",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=interval_seconds,
        )
    if name == "coinbase_spot":
        return await backfill_coinbase_candles(
            connection,
            CoinbaseHistoryClient(),
            run_id=run_id,
            product_id="BTC-USD",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=interval_seconds,
        )
    raise ValueError(f"unknown historical dataset: {name}")


async def _run_named_dataset(
    name: str,
    *,
    engine: Engine,
    start: datetime,
    end: datetime,
    settings: Settings,
    downloaded_at: datetime,
    fidelity_minutes: int,
    interval_seconds: int,
    horizons: tuple[str, ...] | None = None,
    allow_unavailable: bool = False,
) -> dict[str, Any]:
    validate_window(start, end)
    if fidelity_minutes <= 0:
        raise ValueError("fidelity_minutes must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    run_id = str(uuid4())
    source = _DATASET_SOURCE[name]
    params = _run_parameters(
        name,
        settings=settings,
        fidelity_minutes=fidelity_minutes,
        interval_seconds=interval_seconds,
        horizons=horizons,
    )
    provenance = ProvenanceRepository()
    run = BackfillRun(
        run_id=run_id,
        dataset=name,
        source=source,
        requested_start=start,
        requested_end=end,
        parameters=params,
        started_at=downloaded_at,
    )

    # Persist the run record before network work so a later failure remains auditable.
    with engine.begin() as connection:
        provenance.start_run(connection, run)

    try:
        with engine.begin() as connection:
            stats = await _execute_dataset(
                name,
                connection,
                run_id=run_id,
                start=start,
                end=end,
                settings=settings,
                downloaded_at=downloaded_at,
                fidelity_minutes=fidelity_minutes,
                interval_seconds=interval_seconds,
                horizons=horizons,
            )
            provenance.finish_run(connection, run_id, datetime.now(UTC), stats)
    except BybitHistoryUnavailableError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        with engine.begin() as connection:
            provenance.mark_unavailable(connection, run_id, datetime.now(UTC), reason)
        if not allow_unavailable:
            raise
        return {
            "run_id": run_id,
            "dataset": name,
            "source": source,
            "status": "unavailable",
            "reason": reason,
            "rows_inserted": 0,
            "rows_existing": 0,
            "chunks_fetched": 0,
        }
    except Exception as exc:
        with engine.begin() as connection:
            provenance.fail_run(
                connection,
                run_id,
                datetime.now(UTC),
                f"{type(exc).__name__}: {exc}",
            )
        raise

    return {
        "run_id": run_id,
        "dataset": name,
        "source": source,
        "status": "success",
        "rows_inserted": stats.rows_inserted,
        "rows_existing": stats.rows_existing,
        "chunks_fetched": stats.chunks_fetched,
    }


async def _run_standard(
    *,
    engine: Engine,
    start: datetime,
    end: datetime,
    settings: Settings,
    downloaded_at: datetime,
    fidelity_minutes: int,
    interval_seconds: int,
    require_bybit: bool = False,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for name in STANDARD_SEQUENCE:
        results[name] = await _run_named_dataset(
            name,
            engine=engine,
            start=start,
            end=end,
            settings=settings,
            downloaded_at=downloaded_at,
            fidelity_minutes=fidelity_minutes,
            interval_seconds=interval_seconds,
            allow_unavailable=name.startswith("bybit_") and not require_bybit,
        )
    return results


async def _run_args(args: argparse.Namespace) -> dict[str, Any]:
    validate_window(args.start, args.end)
    settings = _settings(args)
    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    downloaded_at = datetime.now(UTC)
    fidelity_minutes = getattr(args, "fidelity_minutes", 1)
    interval_seconds = getattr(args, "interval_seconds", 60)

    if args.command == "standard":
        return await _run_standard(
            engine=engine,
            start=args.start,
            end=args.end,
            settings=settings,
            downloaded_at=downloaded_at,
            fidelity_minutes=fidelity_minutes,
            interval_seconds=interval_seconds,
            require_bybit=args.require_bybit,
        )

    if args.command == "polymarket-markets":
        result = await _run_named_dataset(
            "polymarket_markets",
            engine=engine,
            start=args.start,
            end=args.end,
            settings=settings,
            downloaded_at=downloaded_at,
            fidelity_minutes=1,
            interval_seconds=60,
            horizons=tuple(args.horizons) if args.horizons else None,
        )
        return {"polymarket_markets": result}

    if args.command == "polymarket-prices":
        result = await _run_named_dataset(
            "polymarket_prices",
            engine=engine,
            start=args.start,
            end=args.end,
            settings=settings,
            downloaded_at=downloaded_at,
            fidelity_minutes=fidelity_minutes,
            interval_seconds=60,
        )
        return {"polymarket_prices": result}

    if args.command == "btc-candles":
        names = {
            "all": ("bybit_spot", "bybit_linear", "coinbase_spot"),
            "bybit-spot": ("bybit_spot",),
            "bybit-linear": ("bybit_linear",),
            "coinbase-spot": ("coinbase_spot",),
        }[args.source]
        results: dict[str, dict[str, Any]] = {}
        for name in names:
            results[name] = await _run_named_dataset(
                name,
                engine=engine,
                start=args.start,
                end=args.end,
                settings=settings,
                downloaded_at=downloaded_at,
                fidelity_minutes=1,
                interval_seconds=interval_seconds,
            )
        return results

    raise ValueError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run_args(args))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
