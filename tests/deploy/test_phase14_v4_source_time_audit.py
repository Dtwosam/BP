from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_source_time_audit_cloudshell.sh"
)


def test_v4_source_time_audit_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(AUDIT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_source_time_audit_is_read_only_and_holdout_safe() -> None:
    text = AUDIT.read_text(encoding="utf-8")

    for marker in (
        'SOURCE_TIME_LIMIT_SECONDS = Decimal("2")',
        'LEGACY_FRESHNESS_SECONDS = Decimal("10")',
        'SELECTED_OFFSET_SECONDS = 240',
        '"-c default_transaction_read_only=on"',
        'SHOW default_transaction_read_only',
        'RESEARCH_PLAN_VERSION = "v4-gate-b-preregister-v2"',
        'FEATURE_VERSION = "core-v4-regime-aware"',
        "EPOCH_START = datetime(2026, 9, 24, 0, 0, tzinfo=UTC)",
        "EPOCH_END = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)",
        "HOLDOUT_START = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)",
        "mf.market_start_at < :holdout_start",
        "source = 'coinbase'",
        "source = 'bybit'",
        "source = 'polymarket'",
        "event_type = 'price_change'",
        "jsonb_array_elements",
        '"old_v4_edge_policy_reused_v3_execution_books": True',
        '"model_refit_performed": False',
        '"threshold_tuning_performed": False',
        '"final_holdout_evaluated": False',
        '"database_session_read_only": True',
        '"order_submission_attempted": False',
        "PHASE14_V4_SOURCE_TIME_AUDIT=PASS",
        "MUTATIONS_PERFORMED=false",
        "FINAL_HOLDOUT_LABELS_READ=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    assert "bp_engine.v4_research" not in text

    for forbidden in (
        "systemctl start",
        "systemctl restart",
        "systemctl stop",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "post_order(",
        "create_market_order",
        "gcloud pubsub",
    ):
        assert forbidden not in text
