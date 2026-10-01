from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.execution.fast_live_book import StreamingBookCache
from bp_engine.storage import schema
from bp_engine.v3_paper.fresh_book_shadow import evaluate_fresh_book_shadow
from bp_engine.v3_paper.service import V3_PAPER_PREDICTION_VERSION

FORBIDDEN_ENV = (
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_WALLET_ADDRESS",
    "BP_TELEGRAM_BOT_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Observe frozen V3 predictions against a dedicated fresh Polymarket "
            "book stream and emit money-disabled shadow records."
        )
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--run-seconds", type=float, default=3600.0)
    parser.add_argument("--poll-seconds", type=float, default=0.10)
    parser.add_argument("--quote-wait-seconds", type=float, default=1.0)
    parser.add_argument("--quote-fresh-seconds", type=float, default=0.25)
    parser.add_argument("--market-lookahead-seconds", type=float, default=600.0)
    return parser.parse_args()


def _require_safe_environment(env_file: str) -> None:
    for name in FORBIDDEN_ENV:
        if os.environ.get(name):
            raise SystemExit(f"forbidden environment present: {name}")

    path = Path(env_file)
    if not path.is_file():
        raise SystemExit(f"environment file missing: {env_file}")
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.add(line.split("=", 1)[0].strip())
    forbidden = sorted(names.intersection(FORBIDDEN_ENV))
    if forbidden:
        raise SystemExit(
            "forbidden trading/credential names present in environment file: "
            + ",".join(forbidden)
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _subscribe_nearby_markets(
    connection,
    cache: StreamingBookCache,
    *,
    now: datetime,
    lookahead_seconds: float,
) -> int:
    future = now + timedelta(seconds=lookahead_seconds)
    rows = connection.execute(
        select(
            schema.polymarket_markets.c.up_token_id,
            schema.polymarket_markets.c.down_token_id,
        ).where(
            schema.polymarket_markets.c.horizon_seconds == 300,
            schema.polymarket_markets.c.active.is_(True),
            schema.polymarket_markets.c.end_at >= now,
            schema.polymarket_markets.c.start_at <= future,
        )
    ).all()
    tokens = sorted(
        {
            str(token)
            for row in rows
            for token in row
            if str(token or "").strip()
        }
    )
    cache.subscribe(tokens)
    return len(tokens)


def _prediction_rows(connection, *, start_at: datetime) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            select(schema.live_predictions)
            .where(
                schema.live_predictions.c.prediction_version
                == V3_PAPER_PREDICTION_VERSION,
                schema.live_predictions.c.recorded_at >= start_at,
            )
            .order_by(
                schema.live_predictions.c.recorded_at,
                schema.live_predictions.c.id,
            )
        ).mappings()
    ]


def _selected_token(row: dict[str, Any]) -> str:
    probability = float(row["calibrated_probability"])
    key = "up_token_id" if probability >= 0.5 else "down_token_id"
    return str(row[key])


def _fresh_levels(
    cache: StreamingBookCache,
    token_id: str,
    *,
    wait_seconds: float,
) -> tuple[tuple[str, str], ...] | None:
    deadline = time.monotonic() + wait_seconds
    while True:
        levels = cache.snapshot(token_id)
        if levels is not None:
            return levels
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.01)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    args = _parse_args()
    if args.run_seconds <= 0:
        raise SystemExit("--run-seconds must be greater than zero")
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be greater than zero")
    if args.quote_wait_seconds < 0:
        raise SystemExit("--quote-wait-seconds must be non-negative")
    if args.quote_fresh_seconds <= 0:
        raise SystemExit("--quote-fresh-seconds must be greater than zero")
    if args.market_lookahead_seconds <= 0:
        raise SystemExit("--market-lookahead-seconds must be greater than zero")

    _require_safe_environment(args.env_file)
    settings = Settings(_env_file=args.env_file)
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if settings.max_trade_size_usd != 0:
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if settings.max_daily_loss_usd != 0:
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )

    cache = StreamingBookCache(quote_fresh_seconds=args.quote_fresh_seconds)
    cache.start()
    started_at = datetime.now(UTC)
    deadline = time.monotonic() + args.run_seconds
    seen: set[str] = set()
    subscribed = 0

    _emit(
        {
            "event": "fresh_book_shadow_started",
            "started_at": started_at.isoformat(),
            "prediction_version": V3_PAPER_PREDICTION_VERSION,
            "database_read_only": True,
            "order_submission_enabled": False,
            "wallet_material_loaded": False,
        }
    )

    try:
        while time.monotonic() < deadline:
            now = datetime.now(UTC)
            with engine.connect() as connection:
                read_only = connection.execute(
                    text("SHOW default_transaction_read_only")
                ).scalar_one()
                if read_only != "on":
                    raise SystemExit("database connection is not read-only")

                subscribed = max(
                    subscribed,
                    _subscribe_nearby_markets(
                        connection,
                        cache,
                        now=now,
                        lookahead_seconds=args.market_lookahead_seconds,
                    ),
                )
                rows = _prediction_rows(connection, start_at=started_at)

            for row in rows:
                prediction_id = str(row["prediction_id"])
                if prediction_id in seen:
                    continue
                seen.add(prediction_id)
                token_id = _selected_token(row)
                cache.subscribe([token_id])
                levels = _fresh_levels(
                    cache,
                    token_id,
                    wait_seconds=args.quote_wait_seconds,
                )
                quote_at = datetime.now(UTC)
                if levels is None:
                    _emit(
                        {
                            "event": "fresh_book_shadow_quote_unavailable",
                            "prediction_id": prediction_id,
                            "prediction_recorded_at": _utc(
                                row["recorded_at"]
                            ).isoformat(),
                            "quote_checked_at": quote_at.isoformat(),
                            "token_id": token_id,
                            "trade": False,
                            "reason": "fresh_quote_unavailable",
                            "order_submission_enabled": False,
                        }
                    )
                    continue

                result = evaluate_fresh_book_shadow(
                    row,
                    levels,
                    quote_observed_at=quote_at,
                )
                payload = result.as_mapping()
                payload.update(
                    {
                        "event": "fresh_book_shadow_evaluated",
                        "order_submission_enabled": False,
                        "wallet_material_loaded": False,
                    }
                )
                _emit(payload)

            time.sleep(args.poll_seconds)
    finally:
        cache.stop()
        engine.dispose()

    _emit(
        {
            "event": "fresh_book_shadow_completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "prediction_count": len(seen),
            "subscribed_token_count_high_water": subscribed,
            "database_read_only": True,
            "order_submission_enabled": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
