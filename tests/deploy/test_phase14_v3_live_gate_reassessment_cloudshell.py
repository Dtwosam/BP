from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy/phase14_v3_live_gate_reassessment_cloudshell.sh"


def test_v3_live_gate_cloudshell_bridge_is_read_only_and_direct() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = [
        "local_main_not_current",
        "local_source_truth_not_authorized",
        "default_transaction_read_only=on",
        'SHOW default_transaction_read_only',
        "paper-execution-v3-frozen-v1",
        "v3-frozen-paper-v1",
        "https://polymarket.com/api/geoblock",
        "follow_redirects=False",
        "wallet_or_signing_material_read",
        "real_order_submission_attempted",
        "real_money_mutation_performed",
        '"live_gate_eligible": False',
        '"phase15_permitted": False',
        "PHASE14_V3_LIVE_GATE_REASSESSMENT=PASS",
        "v3_prediction_ids",
        "schema.live_predictions.c.prediction_version",
        "import polymarket",
        'python3 - "$ROOT/PROJECT_STATE.json"',
    ]
    for marker in required:
        assert marker in text

    forbidden = [
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
        'import polymarket_client',
        '"$ROOT/.venv/bin/python" - "$ROOT/PROJECT_STATE.json"',
    ]
    for marker in forbidden:
        assert marker not in text


def test_v3_live_gate_cloudshell_bridge_binds_current_runtime_identities() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "52b4355d6f077373b873f7a6f42bc37a20ddbc7b" in text
    assert "/var/lib/bp/runtime/v3-paper-9d52eb753355365848a637ffa6663928664bf770" in text
    assert "/var/lib/bp/runtime/v4-forward-36b02d0687194173ab5d3862d3b88c6c90607574" in text
    assert "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7" not in text
