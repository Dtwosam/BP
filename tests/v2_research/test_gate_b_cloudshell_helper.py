from __future__ import annotations

import re
import subprocess
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "deploy"
    / "phase14_v2_gate_b_research_cloudshell.sh"
)


def test_gate_b_cloudshell_helper_is_valid_bash() -> None:
    result = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_gate_b_cloudshell_helper_is_non_deploying_and_fail_closed() -> None:
    source = HELPER.read_text(encoding="utf-8")

    required = (
        "local_helper_head_mismatch",
        "remote_main_changed",
        "candidate_archive_sha256_mismatch",
        "unexpected_deployed_head",
        "research_zero_money",
        "recorder_config_worker_count_not_4",
        "gate_b_plan_failed",
        "GATE_B_AUTHORIZED=false",
        "AUTOMATIC_PROMOTION=false",
        'PYTHONPATH="$RUNTIME_ROOT/src"',
        "git archive --format=tar.gz",
        "gcloud compute scp",
        "production_database_mutated_by_gate_b",
        "read_only_transaction_each_stage",
    )
    for token in required:
        assert token in source

    forbidden_patterns = (
        r"git\s+(?:-C\s+\S+\s+)?checkout\b",
        r"git\s+(?:-C\s+\S+\s+)?reset\b",
        r"systemctl\s+(?:start|stop|restart|enable|disable)\b",
        r"pip\s+install\b",
        r"metadata\.create_all",
        r"LIVE_TRADING_ENABLED=true",
        r"MAX_TRADE_SIZE_USD=[1-9]",
        r"MAX_DAILY_LOSS_USD=[1-9]",
        r"run_candidate\s+prepare\b",
        r"run_candidate\s+evaluate-holdout\b",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, source) is None


def test_gate_b_cloudshell_helper_freezes_fresh_plan_before_holdout() -> None:
    source = HELPER.read_text(encoding="utf-8")

    required = (
        "PHASE14_V2_GATE_B_PLANNING_EPOCH_START",
        "PHASE14_V2_GATE_B_EXPECTED_PLAN_SHA256",
        "PHASE14_V2_GATE_B_STOP_AFTER_PLAN",
        "planning_epoch_start_invalid",
        "expected_plan_sha256_invalid",
        "stop_after_plan_required",
        '--planning-epoch-start "$PLANNING_EPOCH_START"',
        'payload.get("planning_epoch_start_at")',
        'payload.get("plan_sha256")',
        "expected plan SHA-256 mismatch",
        "PHASE14_V2_GATE_B_RESEARCH=PLAN_FROZEN",
        "HOLDOUT_TOUCHED=false",
        "SELECTION_PRESENT=false",
        "HOLDOUT_PRESENT=false",
    )
    for token in required:
        assert token in source
