from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts/deploy/phase15_v3_canary_official_fill_probe_cloudshell.sh"


def test_official_fill_probe_is_read_only_and_binds_submitted_canary() -> None:
    text = PROBE.read_text(encoding="utf-8")
    for marker in (
        "PHASE_15_FIRST_LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED",
        "LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED",
        "official_order_fill_reconciliation_status",
        "list_open_orders",
        "list_account_trades",
        "taker_order_id",
        "maker_orders",
        "matched_amount",
        "snapshot_stable_across_3_seconds",
        "official_reconciliation_complete",
        "NETWORK_SUBMISSION_ATTEMPT_CONSUMED=true",
        "SECOND_ORDER_AUTHORIZED=false",
        "NO_ORDER_MUTATION_PERFORMED=true",
        "PHASE15_V3_CANARY_OFFICIAL_FILL_PROBE=PASS",
        "PHASE15_V3_CANARY_OFFICIAL_FILL_PROBE=PENDING",
    ):
        assert marker in text


def test_official_fill_probe_pins_sdk_and_safety_state() -> None:
    text = PROBE.read_text(encoding="utf-8")
    assert 'importlib.metadata.version("polymarket-client") != "0.7.1"' in text
    assert 'payload["kill_switch_engaged"] is True' in text
    assert 'payload["submission_ready"] is False' in text
    assert 'payload["geoblock"]["country"] == "ZA"' in text
    assert "PHASE15_ACCEPT_REAL_MONEY" not in text


def test_official_fill_probe_shell_and_embedded_python_are_syntax_valid() -> None:
    text = PROBE.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        ast.parse(block)
    subprocess.run(
        ["bash", "-n", str(PROBE)],
        check=True,
        capture_output=True,
        text=True,
    )
