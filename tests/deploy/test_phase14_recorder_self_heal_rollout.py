from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_recorder_self_heal_rollout_cloudshell.sh"
)


def test_recorder_self_heal_rollout_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_recorder_self_heal_preflight_is_non_contacting() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_RECORDER_SELF_HEAL_PREFLIGHT_ONLY",
        "PHASE14_RECORDER_SELF_HEAL_PREFLIGHT=PASS",
        "PRODUCTION_HOST_CONTACTED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "SERVICE_RESTART_PERFORMED=false",
        "DATABASE_WRITES_PERFORMED=false",
        "I_APPROVE_PHASE14_RECORDER_SELF_HEAL_RESTART",
        "a352c66ec0110925727bc40de767ee4ba981f965",
    ):
        assert marker in source
    assert source.index("PHASE14_RECORDER_SELF_HEAL_PREFLIGHT=PASS") < source.index(
        "command -v gcloud"
    )


def test_recorder_self_heal_rollout_mutates_only_recorder_service() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert 'systemctl restart "$RECORDER_UNIT"' in source
    for forbidden in (
        'systemctl restart "$EXPECTED_SHADOW_UNIT"',
        'systemctl stop "$EXPECTED_SHADOW_UNIT"',
        'systemctl restart "$FAST_LIVE_SOURCE"',
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in source


def test_recorder_self_heal_rollout_requires_stale_then_fresh_sources() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "recorder sources are not all stale; refusing restart",
        "recorder sources did not all become fresh within 90s",
        'probe_v4_sources stale "$STALE_TMP"',
        'probe_v4_sources fresh "$ACCEPT_TMP"',
        "age_seconds",
        "received_at >= :cutoff",
        "default_transaction_read_only=on",
    ):
        assert marker in source


def test_recorder_self_heal_rollout_scope_is_exact() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for path in (
        "src/bp_engine/recorder/service.py",
        "tests/recorder/test_recorder_service.py",
    ):
        assert path in source
    assert "candidate_scope_mismatch" in source
    assert "candidate_blob_not_exact_main" in source


def test_recorder_self_heal_rollout_preserves_shadow_identity() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "shadow_pid_changed",
        "shadow_restarted",
        "SHADOW_RESTARTED=false",
        "validate_shadow_contract",
    ):
        assert marker in source
