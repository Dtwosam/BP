from __future__ import annotations

import uvicorn

from bp_engine.config import Settings, TradingMode, get_settings

DASHBOARD_API_HOST = "127.0.0.1"


def validate_dashboard_safety(settings: Settings) -> None:
    if (
        settings.mode is not TradingMode.RESEARCH
        or settings.live_trading_enabled
        or settings.max_trade_size_usd != 0
        or settings.max_daily_loss_usd != 0
    ):
        raise RuntimeError(
            "dashboard safety interlock requires research mode, live trading off, and zero limits"
        )


def main() -> None:
    settings = get_settings()
    validate_dashboard_safety(settings)
    uvicorn.run(
        "bp_engine.dashboard.app:create_default_app",
        factory=True,
        host=DASHBOARD_API_HOST,
        port=settings.dashboard_api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
