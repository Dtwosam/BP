from __future__ import annotations

from datetime import UTC, datetime

from bp_engine.dashboard.service import build_dashboard_snapshot


class FakePaperDashboardRepository:
    def list_active_markets(self, now: datetime):
        return []

    def list_feed_health(self, now: datetime):
        return []

    def list_predictions(self, limit: int = 100):
        return []

    def list_performance_predictions(self):
        return []

    def list_evaluations(self):
        return []

    def get_paper_execution_evidence(self, *, history_limit: int = 100):
        assert history_limit == 25
        return {
            "paper_pnl": {
                "status": "AVAILABLE",
                "starting_cash": 100.0,
                "current_cash": 101.1496,
                "open_capital": 0.0,
                "unrealized_value": None,
                "realized_pnl": 1.1496,
                "return_on_starting_cash": 0.011496,
                "max_realized_equity_drawdown": 0.0,
                "settled_trade_count": 1,
                "open_position_count": 0,
                "fill_count": 1,
                "no_fill_expired_count": 0,
                "total_fees": 0.0504,
                "total_slippage_cost": 0.0,
                "reconciliation": {
                    "status": "OK",
                    "violation_count": 0,
                    "paper_order_count": 1,
                    "trade_signal_count": 1,
                    "no_trade_signal_count": 1,
                },
            },
            "paper_orders": [{"paper_order_id": "paper-1", "selected_side": "up"}],
            "paper_fills": [{"paper_order_id": "paper-1", "price": 0.6, "shares": 3.0}],
            "paper_settlements": [
                {"paper_order_id": "paper-1", "realized_pnl": 1.1496}
            ],
        }


def test_snapshot_exposes_phase12_paper_evidence_without_enabling_real_execution() -> None:
    now = datetime(2026, 8, 28, 18, 30, tzinfo=UTC)

    snapshot = build_dashboard_snapshot(
        FakePaperDashboardRepository(),
        now=now,
        history_limit=25,
    )

    assert snapshot["mode"] == {
        "trading_mode": "RESEARCH",
        "live_trading_enabled": False,
        "execution_available": False,
        "paper_execution_available": True,
    }
    assert snapshot["paper_pnl"]["status"] == "AVAILABLE"
    assert snapshot["paper_pnl"]["starting_cash"] == 100.0
    assert snapshot["paper_pnl"]["current_cash"] == 101.1496
    assert snapshot["paper_pnl"]["realized_pnl"] == 1.1496
    assert snapshot["paper_pnl"]["reconciliation"]["status"] == "OK"
    assert snapshot["paper_pnl"]["reconciliation"]["no_trade_signal_count"] == 1
    assert snapshot["paper_orders"][0]["paper_order_id"] == "paper-1"
    assert snapshot["paper_fills"][0]["shares"] == 3.0
    assert snapshot["paper_settlements"][0]["realized_pnl"] == 1.1496


def test_snapshot_zero_activity_is_real_paper_account_not_phase11_unavailable_sentinel() -> None:
    class EmptyPaperRepository(FakePaperDashboardRepository):
        def get_paper_execution_evidence(self, *, history_limit: int = 100):
            return {
                "paper_pnl": {
                    "status": "AVAILABLE",
                    "starting_cash": 100.0,
                    "current_cash": 100.0,
                    "open_capital": 0.0,
                    "unrealized_value": None,
                    "realized_pnl": 0.0,
                    "return_on_starting_cash": 0.0,
                    "max_realized_equity_drawdown": 0.0,
                    "settled_trade_count": 0,
                    "open_position_count": 0,
                    "fill_count": 0,
                    "no_fill_expired_count": 0,
                    "total_fees": 0.0,
                    "total_slippage_cost": 0.0,
                    "reconciliation": {
                        "status": "OK",
                        "violation_count": 0,
                        "paper_order_count": 0,
                        "trade_signal_count": 0,
                        "no_trade_signal_count": 0,
                    },
                },
                "paper_orders": [],
                "paper_fills": [],
                "paper_settlements": [],
            }

    snapshot = build_dashboard_snapshot(
        EmptyPaperRepository(),
        now=datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert snapshot["paper_pnl"]["status"] == "AVAILABLE"
    assert snapshot["paper_pnl"]["starting_cash"] == 100.0
    assert snapshot["paper_pnl"]["current_cash"] == 100.0
    assert snapshot["paper_pnl"]["settled_trade_count"] == 0
    assert snapshot["paper_orders"] == []
    assert snapshot["paper_fills"] == []
    assert snapshot["paper_settlements"] == []
