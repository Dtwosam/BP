from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from bp_engine.polymarket.gamma import GammaClient

DATA_API = "https://data-api.polymarket.com"
AFFECTED_LAST_EVENT_OFFSETS = {
    "btc-updown-15m-1788217200": 759.77,
    "btc-updown-15m-1788218100": 733.06,
    "btc-updown-15m-1788219000": 361.07,
    "btc-updown-15m-1788219900": 361.06,
    "btc-updown-15m-1788220800": 361.07,
    "btc-updown-15m-1788221700": 361.06,
    "btc-updown-15m-1788222600": 361.07,
    "btc-updown-15m-1788223500": 542.92,
}
HEALTHY_15M_COMPARATOR_SLUGS = (
    "btc-updown-15m-1788216300",
    "btc-updown-15m-1788224400",
    "btc-updown-15m-1788225300",
)
SAME_START_5M_SLUGS = tuple(
    slug.replace("btc-updown-15m-", "btc-updown-5m-")
    for slug in AFFECTED_LAST_EVENT_OFFSETS
)


def _market_start_from_slug(slug: str) -> int:
    return int(slug.rsplit("-", 1)[1])


def _horizon_seconds_from_slug(slug: str) -> int:
    if "-15m-" in slug:
        return 900
    if "-5m-" in slug:
        return 300
    raise ValueError(f"unknown horizon in slug: {slug}")


def _trade_timestamp(trade: dict[str, Any]) -> int | None:
    value = trade.get("timestamp")
    if value is None:
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 100_000_000_000:
        timestamp //= 1000
    return timestamp


def _offset_range(timestamps: list[int], start_epoch: int) -> list[int] | None:
    if not timestamps:
        return None
    return [timestamps[0] - start_epoch, timestamps[-1] - start_epoch]


def _kind(slug: str) -> str:
    if slug in AFFECTED_LAST_EVENT_OFFSETS:
        return "affected_15m"
    if slug in HEALTHY_15M_COMPARATOR_SLUGS:
        return "healthy_15m_comparator"
    return "same_start_5m"


async def main() -> None:
    gamma = GammaClient()
    output: list[dict[str, object]] = []
    slugs = (
        list(AFFECTED_LAST_EVENT_OFFSETS)
        + list(HEALTHY_15M_COMPARATOR_SLUGS)
        + list(SAME_START_5M_SLUGS)
    )

    async with httpx.AsyncClient(base_url=DATA_API, timeout=20.0) as client:
        for slug in slugs:
            last_event_offset = AFFECTED_LAST_EVENT_OFFSETS.get(slug)
            market = await gamma.get_market_by_slug(slug)
            if market is None:
                output.append({"kind": _kind(slug), "slug": slug, "error": "gamma_market_missing"})
                continue

            condition_id = str(market.get("conditionId") or "")
            if not condition_id:
                output.append({"kind": _kind(slug), "slug": slug, "error": "condition_id_missing"})
                continue

            response = await client.get(
                "/trades",
                params={
                    "market": condition_id,
                    "limit": 10000,
                    "offset": 0,
                    "takerOnly": "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                output.append({"kind": _kind(slug), "slug": slug, "error": "trades_response_not_list"})
                continue

            trades = [trade for trade in payload if isinstance(trade, dict)]
            matching = [
                trade
                for trade in trades
                if str(trade.get("conditionId") or "").lower() == condition_id.lower()
                and str(trade.get("slug") or "") == slug
            ]
            start_epoch = _market_start_from_slug(slug)
            horizon_seconds = _horizon_seconds_from_slug(slug)
            end_epoch = start_epoch + horizon_seconds
            timestamps = sorted(
                timestamp
                for trade in matching
                if (timestamp := _trade_timestamp(trade)) is not None
            )
            during_window = [
                timestamp for timestamp in timestamps if start_epoch <= timestamp <= end_epoch
            ]

            result: dict[str, object] = {
                "kind": _kind(slug),
                "slug": slug,
                "condition_id": condition_id,
                "window_start": datetime.fromtimestamp(start_epoch, tz=UTC).isoformat(),
                "horizon_seconds": horizon_seconds,
                "gamma_volume": market.get("volume"),
                "gamma_volume_num": market.get("volumeNum"),
                "matching_market_trade_count": len(matching),
                "matching_trade_timestamp_offset_range_seconds": _offset_range(
                    timestamps, start_epoch
                ),
                "trade_count_during_window": len(during_window),
                "first_trade_in_window_offset_seconds": (
                    None if not during_window else during_window[0] - start_epoch
                ),
                "last_trade_in_window_offset_seconds": (
                    None if not during_window else during_window[-1] - start_epoch
                ),
            }

            if last_event_offset is not None:
                cutoff_epoch = start_epoch + last_event_offset
                after_cutoff = [
                    timestamp for timestamp in during_window if timestamp > cutoff_epoch
                ]
                result.update(
                    {
                        "bp_last_event_offset_seconds": last_event_offset,
                        "bp_last_event_at": datetime.fromtimestamp(
                            cutoff_epoch, tz=UTC
                        ).isoformat(),
                        "trades_after_bp_last_event_before_window_end": len(after_cutoff),
                    }
                )

            output.append(result)

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "cross_horizon_trade_activity",
                "taker_only": False,
                "markets": output,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
