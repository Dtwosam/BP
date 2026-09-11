from __future__ import annotations

import re
import subprocess
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "deploy"
    / "phase14_v2_gate_b_readiness_cloudshell.sh"
)


def test_gate_b_readiness_cloudshell_helper_is_valid_bash() -> None:
    result = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_gate_b_readiness_helper_is_repeatable_feature_only_and_non_deploying() -> None:
    source = HELPER.read_text(encoding="utf-8")

    required = (
        "PHASE14_V2_GATE_B_READINESS=PASS",
        "READY=",
        "HOLDOUT_TOUCHED=false",
        "readiness >",
        "labels_read",
        "plan_artifact_written",
        "selection_artifact_written",
        "minimum_contiguous_epoch_seconds",
        "64800.0",
        "required_ordinary_folds",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "unexpected_deployed_head",
        "recorder_config_worker_count_not_4",
        'PYTHONPATH="$RUNTIME_ROOT/src"',
        "git archive --format=tar.gz",
        "gcloud compute scp",
    )
    for token in required:
        assert token in source

    forbidden_patterns = (
        r"\bprepare\b.*--plan",
        r"evaluate-holdout",
        r"phase14-v2-gate-b-\$STAMP",
        r"git\s+(?:-C\s+\S+\s+)?checkout\b",
        r"git\s+(?:-C\s+\S+\s+)?reset\b",
        r"systemctl\s+(?:start|stop|restart|enable|disable)\b",
        r"pip\s+install\b",
        r"metadata\.create_all",
        r"LIVE_TRADING_ENABLED=true",
        r"MAX_TRADE_SIZE_USD=[1-9]",
        r"MAX_DAILY_LOSS_USD=[1-9]",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, source) is None


def test_gate_b_readiness_helper_requires_and_verifies_fresh_planning_epoch() -> None:
    source = HELPER.read_text(encoding="utf-8")

    required = (
        "PHASE14_V2_GATE_B_READINESS_PLANNING_EPOCH_START",
        "planning_epoch_start_invalid",
        '--planning-epoch-start "$PLANNING_EPOCH_START"',
        'payload.get("planning_epoch_start_at")',
        "PLANNING_EPOCH_START=",
    )
    for token in required:
        assert token in source
