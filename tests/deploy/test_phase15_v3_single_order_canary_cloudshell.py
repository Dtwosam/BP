from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy/phase15_v3_single_order_canary_cloudshell.sh"


def test_phase15_canary_deploy_requires_explicit_real_money_acknowledgement() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "PHASE15_ACCEPT_REAL_MONEY_CANARY" in text
    assert "PHASE15_CANARY_MAX_LOSS_USD" in text
    assert "real_money_canary_not_explicitly_accepted" in text
    assert "canary_max_loss_acknowledgement_must_equal_5" in text


def test_phase15_canary_deploy_preserves_secret_and_geography_boundaries() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    required = (
        "africa-south1-a",
        "bp-v3-canary-exec",
        "https://polymarket.com/api/geoblock",
        "blocked",
        "country",
        "ZA",
        "region",
        "GP",
        "Polymarket private key (not echoed; never paste this into chat)",
        "chmod 640 /etc/bp/bp-v3-live-canary.env",
        "BP_CANARY_REMOTE_HOST",
        "restrict,command=\"/usr/local/bin/bp-v3-canary-executor\"",
    )
    for marker in required:
        assert marker in text
    assert "POLYMARKET_PRIVATE_KEY=$POLYMARKET_PRIVATE_KEY" in text
    assert "POLYMARKET_PRIVATE_KEY=" not in text.split(
        "cat > /etc/bp/bp-v3-live-canary.env"
    )[-1]


def test_phase15_canary_deploy_unlocks_only_after_preflight() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    health = text.index('REMOTE_HEALTH=')
    remote_unlock = text.index(
        "sudo -u bp-exec rm -f /var/lib/bp/live-canary/KILL"
    )
    local_unlock = text.index("sudo -u bp rm -f /var/lib/bp/live-canary/KILL")
    service_start = text.index("sudo systemctl start bp-v3-live-canary.service")
    assert health < remote_unlock < local_unlock < service_start
    assert "rollback_remote" in text
    assert "sudo systemctl stop bp-v3-live-canary.service" in text


def test_phase15_canary_deploy_never_changes_global_production_env() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "bp-v3-live-canary.env" in text
    assert "tee /etc/bp/bp.env" not in text
    assert "sed -i" not in text
    assert "systemctl restart bp-recorder" not in text
    assert "systemctl restart bp-v3-frozen-predictor" not in text
    assert "systemctl restart bp-v3-paper-execution" not in text
