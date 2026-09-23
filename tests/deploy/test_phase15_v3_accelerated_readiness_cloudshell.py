from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy/phase15_v3_accelerated_readiness_cloudshell.sh"


def test_accelerated_readiness_bridge_is_read_only_and_fail_closed() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = (
        "local_main_not_current",
        "local_source_truth_not_authorized",
        "0.14.178",
        "ACCELERATED_READINESS_ENGINEERING_NOT_RUN",
        "default_transaction_read_only=on",
        "SHOW default_transaction_read_only",
        "v3-frozen-paper-v1",
        "paper-execution-v3-frozen-v1",
        "build_calibration_audit",
        "build_accelerated_v3_readiness",
        "bootstrap_resamples=2_000",
        "ordinary_validation_economics_passed",
        "authenticated_trading_client_constructed",
        "wallet_or_signing_material_read",
        "real_order_submission_attempted",
        "PHASE15_V3_ACCELERATED_READINESS=PASS",
    )
    for marker in required:
        assert marker in text

    forbidden = (
        "create_all(",
        "insert(",
        "update(",
        "delete(",
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "git checkout",
        "git reset",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "OfficialPolymarketTradingClient",
        "submit_limit_buy",
        "post_order",
        "LIVE_TRADING_ENABLED=true",
    )
    for marker in forbidden:
        assert marker not in text


def test_accelerated_readiness_bridge_binds_accepted_runtime() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "52b4355d6f077373b873f7a6f42bc37a20ddbc7b" in text
    assert "/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770" in text
    assert "/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574" in text
