from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_paper_source_time_profitability_audit_cloudshell.sh"
)


def test_v3_paper_source_time_profitability_audit_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(AUDIT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v3_paper_source_time_profitability_audit_is_read_only_and_exact() -> None:
    text = AUDIT.read_text(encoding="utf-8")

    for marker in (
        'MAX_SOURCE_AGE_SECONDS = Decimal("2")',
        'MAX_FUTURE_SKEW_SECONDS = Decimal("1")',
        'schema.raw_market_events.c.source == "polymarket"',
        'schema.raw_market_events.c.stream == "market"',
        'schema.raw_market_events.c.event_type == "price_change"',
        'schema.raw_market_events.c.instrument == condition_id',
        'str(change.get("asset_id") or "") == token_id',
        '.limit(128)',
        '"-c default_transaction_read_only=on"',
        'SHOW default_transaction_read_only',
        '"exclude_trade_fail_closed"',
        '"repricing_performed": False',
        '"model_refit_performed": False',
        '"threshold_tuning_performed": False',
        '"profitable_after_guard"',
        '"excluded_trades"',
        '"database_session_read_only": True',
        '"order_submission_attempted": False',
        "PHASE15_V3_PAPER_SOURCE_TIME_PROFITABILITY_AUDIT=PASS",
        "MUTATIONS_PERFORMED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    for forbidden in (
        "systemctl start",
        "systemctl restart",
        "systemctl stop",
        "gcloud pubsub",
        "post_order(",
        "create_market_order",
        "rm -f /etc/bp-fast-live",
        "rm -rf /var/lib/bp",
    ):
        assert forbidden not in text


def test_v3_paper_source_time_profitability_uses_submission_as_decision_time() -> None:
    text = AUDIT.read_text(encoding="utf-8")

    assert 'observed_at = utc(order["submitted_at"])' in text
    assert (
        '"decision_time": "paper_order.submitted_at == prediction.recorded_at"'
        in text
    )
    assert (
        'schema.raw_market_events.c.received_at <= observed_at'
        in text
    )
