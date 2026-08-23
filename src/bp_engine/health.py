from __future__ import annotations

import json

from bp_engine.config import Settings, get_settings


def build_health_payload(settings: Settings) -> dict[str, object]:
    return {
        "status": "ok",
        "mode": settings.mode.value,
        "live_trading_enabled": settings.live_trading_enabled,
        "active_horizons": list(settings.active_horizons),
        "optional_horizons": list(settings.optional_horizons),
        "timezone": settings.timezone,
    }


def main() -> None:
    print(json.dumps(build_health_payload(get_settings()), sort_keys=True))


if __name__ == "__main__":
    main()
