from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.polymarket.discovery import build_candidate_slugs
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.polymarket.parsing import parse_gamma_market

REQUIRED_HORIZONS = ("5m", "15m")
OFFSETS = (-1, 0, 1)
OUTPUT = Path("gamma-smoke-capture.json")


async def capture() -> list[dict[str, object]]:
    now = datetime.now(UTC)
    client = GammaClient()
    captured: list[dict[str, object]] = []
    found_seconds: set[int] = set()

    for slug in build_candidate_slugs(now, REQUIRED_HORIZONS, offsets=OFFSETS):
        payload = await client.get_market_by_slug(slug)
        if payload is None:
            continue
        market = parse_gamma_market(payload)
        found_seconds.add(market.horizon_seconds)
        captured.append(
            {
                "captured_at": now.isoformat(),
                "gamma_payload": payload,
                "normalized": market.model_dump(mode="json"),
            }
        )

    required_seconds = {300, 900}
    missing = sorted(required_seconds - found_seconds)
    if missing:
        raise RuntimeError(f"live Gamma smoke missing required horizon seconds: {missing}")
    return captured


def main() -> None:
    captured = asyncio.run(capture())
    OUTPUT.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "ok", "captured_markets": len(captured)}))
    for item in captured:
        normalized = item["normalized"]
        print(
            json.dumps(
                {
                    "slug": normalized["slug"],
                    "condition_id": normalized["condition_id"],
                    "horizon_seconds": normalized["horizon_seconds"],
                    "resolution_source": normalized["resolution_source"],
                    "rules_hash": normalized["rules_hash"],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
