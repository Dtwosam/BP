from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy/phase15_v3_canary_host_probe_cloudshell.sh"


def test_johannesburg_host_probe_is_geoblock_only() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = (
        "PHASE15_ACCEPT_BILLABLE_VM",
        "africa-south1-a",
        "bp-v3-canary-exec",
        "e2-micro",
        "https://polymarket.com/api/geoblock",
        "candidate_instance_already_exists",
        "--no-service-account",
        "TRADING_SOFTWARE_INSTALLED=false",
        "WALLET_OR_SIGNING_MATERIAL_PRESENT=false",
        "LIVE_TRADING_ENABLED=false",
        "PHASE15_V3_CANARY_HOST_PROBE=PASS",
    )
    for marker in required:
        assert marker in text

    forbidden = (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "OfficialPolymarketTradingClient",
        "SecureClient",
        "submit_limit_buy",
        "post_order",
        "LIVE_TRADING_ENABLED=true",
        "MAX_TRADE_SIZE_USD=1",
        "MAX_DAILY_LOSS_USD=1",
        "vpn",
        "proxy",
        "ssh -D",
        "socat",
    )
    lowered = text.lower()
    for marker in forbidden:
        assert marker.lower() not in lowered


def test_blocked_probe_deletes_candidate_but_pass_keeps_it() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    blocked = text.index('if [[ "$PROBE_RESULT" != "PASS" ]]')
    deletion = text.index('gcloud compute instances delete "$VM"', blocked)
    success = text.index("PHASE15_V3_CANARY_HOST_PROBE=PASS")
    assert blocked < deletion < success
    assert "Candidate VM is intentionally left running" in text
