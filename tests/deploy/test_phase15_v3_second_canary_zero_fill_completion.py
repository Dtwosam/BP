from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_second_canary_zero_fill_completion_cloudshell.sh"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-second-canary-zero-fill-completion-pass-production-20260928.json"
)


def test_zero_fill_completion_is_recorded_and_non_trading() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    auth = gate["second_live_canary_authorization"]
    repair = gate["second_canary_db_reconciliation"]

    assert gate["live_trading_enabled"] is False
    assert gate["second_order_authorized"] is False
    assert auth["consumed"] is True
    assert auth["network_submission_attempt_consumed"] is True
    assert auth["third_order_authorized"] is False

    assert repair["status"] == "COMPLETED_PASS"
    assert repair["authorized"] is False
    assert repair["authorization_consumed"] is True
    assert repair["helper_git_blob_sha"] == (
        "1fa22dae91f8ffde5c26c0096f1bf5c53547098e"
    )
    assert repair["completion_helper_git_blob_sha"] == (
        "272313d9bda1712a9d5afa7fd9b0ea634cde2267"
    )
    assert repair["live_account_snapshot_runtime_fix_git_blob_sha"] == (
        "3dd9d1ed6821a126e9075f72b2a2e0d6d1f969a6"
    )
    assert repair["live_account_snapshot_runtime_fix_deployment_authorized"] is True
    assert repair["live_account_snapshot_runtime_fix_deployed"] is True
    assert repair["sidecar_runner_git_blob_sha"] == (
        "ed3ced72c75291a0fd79a15009b0a468561683d9"
    )
    assert repair["sidecar_service_unit_git_blob_sha"] == (
        "2f162c9c17658d6917544b056176a56cc50bea46"
    )
    assert repair["sidecar_canary_git_blob_sha"] == (
        "14c3f508ec1a0c446af4c4ee92572075ffff3753"
    )
    assert repair["prepare_watcher_start_or_restart_authorized"] is False
    assert repair["core_service_restart_authorized"] is False
    assert repair["frozen_v3_runtime_mutation_authorized"] is False
    assert repair["production_db_mutation_performed"] is True
    assert repair["production_db_reconciliation_id"] == (
        "live-reconciliation-50ac17663fa46ba96b7d341a68c03c53"
    )
    assert repair["post_completion_total_exposure_usd"] == 0
    assert repair["post_completion_unresolved_critical_reconciliation"] == 0
    assert repair["completion_result"] == "PASS"
    assert repair["third_order_authorized"] is False


def test_zero_fill_completion_production_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    canary = gate["second_live_canary"]
    repair = gate["second_canary_db_reconciliation"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main"] == "4632b9240a761d578e950bbef51d4dd185f717ec"
    assert evidence["runtime_fix"]["result"] == "PASS"
    assert evidence["runtime_fix"]["prepare_watcher_active"] is False
    assert evidence["runtime_fix"]["core_service_pids_preserved"] is True
    assert evidence["runtime_fix"]["frozen_v3_runtime_mutated"] is False
    assert evidence["official_fill_probe"]["fill_state"] == "zero_fill_observed"
    assert evidence["official_fill_probe"]["open_order_count"] == 0
    assert evidence["reconciliation"]["reconciliation_id"] == canary["db_reconciliation_id"]
    assert evidence["reconciliation"]["unresolved_count"] == 0
    assert evidence["reconciliation"]["critical_count"] == 0
    assert evidence["reconciliation"]["normalized_event_state"] == (
        "absent_verified_by_executor_receipt"
    )
    assert evidence["post_completion_account_snapshot"]["total_exposure_usd"] == "0"
    assert evidence["post_completion_account_snapshot"][
        "unresolved_critical_reconciliation"
    ] == 0
    assert evidence["safety"]["third_order_authorized"] is False
    assert evidence["safety"]["global_live_trading_enabled"] is False
    assert repair["completion_result"] == "PASS"
    assert repair["authorization_consumed"] is True


def test_zero_fill_completion_helper_is_fail_closed_and_does_not_start_trading() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE15_ACCEPT_SECOND_CANARY_ZERO_FILL_COMPLETION",
        "explicit_zero_fill_completion_authorization_required",
        'repair["status"] == "AUTHORIZED_READY"',
        'repair["authorized"] is True',
        'repair["authorization_consumed"] is False',
        'repair["completion_helper_git_blob_sha"] == completion_blob',
        'repair["live_account_snapshot_runtime_fix_git_blob_sha"] == live_blob',
        'repair["live_account_snapshot_runtime_fix_deployment_authorized"] is True',
        'repair["sidecar_runner_git_blob_sha"] == runner_blob',
        'repair["sidecar_service_unit_git_blob_sha"] == unit_blob',
        'repair["sidecar_canary_git_blob_sha"] == canary_blob',
        "prepare_watcher_must_be_inactive",
        "systemctl is-active --quiet \"$SERVICE\" && fail",
        "CORE_SERVICE_PIDS_PRESERVED=true",
        "FROZEN_V3_RUNTIME_MUTATED=false",
        "PHASE15_ACCEPT_SECOND_CANARY_DB_RECONCILIATION=yes",
        "post_reconciliation_account_snapshot_not_clean",
        "unresolved_critical_reconciliation",
        "ADDITIONAL_NETWORK_ATTEMPT_CONSUMED=false",
        "THIRD_ORDER_AUTHORIZED=false",
        "GLOBAL_LIVE_TRADING_ENABLED=false",
        "PHASE15_V3_SECOND_CANARY_ZERO_FILL_COMPLETION=PASS",
    ):
        assert marker in text

    for forbidden in (
        "systemctl start \"$SERVICE\"",
        "systemctl restart \"$SERVICE\"",
        "create_limit_order(",
        "post_order(",
        "cancel_order(",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in text

    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 4
    for block in blocks:
        ast.parse(block)

    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
