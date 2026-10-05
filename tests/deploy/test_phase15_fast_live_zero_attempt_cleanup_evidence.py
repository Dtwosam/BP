from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_build_zero_attempt_cleanup_evidence.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "phase15_v3_fast_live_build_zero_attempt_cleanup_evidence",
        BUILDER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cleanup_output(**overrides: str) -> str:
    values = {
        "PHASE15_FAST_LIVE_CLEANUP": "PASS",
        "CLEANUP_MODE": "expired_zero_attempt",
        "AUTHORIZATION_ID": (
            "phase15-v3-fast-live-auto-continuous-v2-12h-"
            "9824a0b1-20261001T201044Z"
        ),
        "AUTHORIZATION_MODE": "auto-telegram-continuous-v2",
        "AUTHORIZATION_SOURCE_HOST": "recorder",
        "SESSION_RELEASE_MAIN": "9824a0b16f64b5018739a33634dc5e4dea673be8",
        "RUNTIME_EXPIRES_AT": "2026-10-02T08:10:44+00:00",
        "ZERO_NETWORK_ATTEMPT_VERIFIED": "true",
        "SESSION_PUBLICATION_COUNT": "0",
        "SESSION_NETWORK_SUBMISSION_ATTEMPT_COUNT": "0",
        "SESSION_EXECUTION_RESULT_COUNT": "0",
        "SESSION_REAL_ORDER_SUBMITTED": "false",
        "KILL_SWITCH_ENGAGED": "true",
        "SESSION_RUNTIME_FILES_PRESENT": "false",
        "SESSION_PUBSUB_RESOURCES_PRESENT": "false",
        "RECORDER_RUNTIME_AUTHORIZATION_PRESENT": "false",
        "EXECUTOR_RUNTIME_AUTHORIZATION_PRESENT": "false",
        "RECORDER_TRANSPORT_KEY_PRESENT": "false",
        "EXECUTOR_TRANSPORT_KEY_PRESENT": "false",
        "RECORDER_SOURCE_ACTIVE": "false",
        "EXECUTOR_RECEIVER_ACTIVE": "false",
        "HISTORICAL_STATE_PRESERVED": "true",
        "CLEANUP_COMPLETED": "true",
        "SERVICES_STARTED": "false",
        "REAL_ORDER_SUBMITTED": "false",
        "EXECUTOR_GEO_COUNTRY": "ZA",
        "EXECUTOR_GEO_BLOCKED": "false",
        "EXECUTOR_OPEN_ORDER_COUNT": "0",
        "EXECUTOR_ACCOUNT_CLEAN": "true",
    }
    values.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def test_zero_attempt_cleanup_evidence_builder_accepts_exact_safe_output() -> None:
    module = _module()
    evidence = module.build_evidence(
        cleanup_output=_cleanup_output(),
        observed_at=datetime(2026, 10, 5, 20, 30, tzinfo=UTC),
    )

    assert evidence["purpose"] == (
        "phase15-v3-fast-live-expired-zero-attempt-cleanup-v1"
    )
    assert evidence["status"] == "CLEANUP_VERIFIED"
    assert evidence["authorization_id"].endswith("9824a0b1-20261001T201044Z")
    assert evidence["session_release_main"] == (
        "9824a0b16f64b5018739a33634dc5e4dea673be8"
    )
    assert evidence["cleanup_mode"] == "expired_zero_attempt"
    assert evidence["zero_network_attempt_verified"] is True
    assert evidence["session_publication_count"] == 0
    assert evidence["session_network_submission_attempt_count"] == 0
    assert evidence["session_execution_result_count"] == 0
    assert evidence["session_real_order_submitted"] is False
    assert evidence["services_started"] is False
    assert evidence["cleanup_real_order_submitted"] is False
    assert evidence["kill_switch_engaged"] is True
    assert evidence["historical_state_preserved"] is True
    assert evidence["session_runtime_files_present"] is False
    assert evidence["session_pubsub_resources_present"] is False
    assert evidence["recorder_source_active"] is False
    assert evidence["executor_receiver_active"] is False
    assert evidence["executor_account_clean"] is True
    assert evidence["executor_open_order_count"] == 0
    assert evidence["executor_geo_country"] == "ZA"
    assert evidence["executor_geo_blocked"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("PHASE15_FAST_LIVE_CLEANUP", "FAIL"),
        ("CLEANUP_MODE", "expired"),
        ("AUTHORIZATION_MODE", "auto-telegram-continuous-v1"),
        ("ZERO_NETWORK_ATTEMPT_VERIFIED", "false"),
        ("SESSION_PUBLICATION_COUNT", "1"),
        ("SESSION_NETWORK_SUBMISSION_ATTEMPT_COUNT", "1"),
        ("SESSION_EXECUTION_RESULT_COUNT", "1"),
        ("SESSION_REAL_ORDER_SUBMITTED", "true"),
        ("KILL_SWITCH_ENGAGED", "false"),
        ("SESSION_RUNTIME_FILES_PRESENT", "true"),
        ("SESSION_PUBSUB_RESOURCES_PRESENT", "true"),
        ("RECORDER_RUNTIME_AUTHORIZATION_PRESENT", "true"),
        ("EXECUTOR_RUNTIME_AUTHORIZATION_PRESENT", "true"),
        ("RECORDER_TRANSPORT_KEY_PRESENT", "true"),
        ("EXECUTOR_TRANSPORT_KEY_PRESENT", "true"),
        ("RECORDER_SOURCE_ACTIVE", "true"),
        ("EXECUTOR_RECEIVER_ACTIVE", "true"),
        ("HISTORICAL_STATE_PRESERVED", "false"),
        ("CLEANUP_COMPLETED", "false"),
        ("SERVICES_STARTED", "true"),
        ("REAL_ORDER_SUBMITTED", "true"),
        ("EXECUTOR_GEO_COUNTRY", "US"),
        ("EXECUTOR_GEO_BLOCKED", "true"),
        ("EXECUTOR_OPEN_ORDER_COUNT", "1"),
        ("EXECUTOR_ACCOUNT_CLEAN", "false"),
    ],
)
def test_zero_attempt_cleanup_evidence_builder_rejects_unsafe_output(
    key: str,
    value: str,
) -> None:
    module = _module()
    with pytest.raises(module.EvidenceError, match="cleanup output"):
        module.build_evidence(
            cleanup_output=_cleanup_output(**{key: value}),
            observed_at=datetime(2026, 10, 5, 20, 30, tzinfo=UTC),
        )


def test_zero_attempt_cleanup_evidence_builder_rejects_bad_binding() -> None:
    module = _module()

    with pytest.raises(module.EvidenceError, match="session release main invalid"):
        module.build_evidence(
            cleanup_output=_cleanup_output(SESSION_RELEASE_MAIN="bad"),
            observed_at=datetime(2026, 10, 5, 20, 30, tzinfo=UTC),
        )

    with pytest.raises(module.EvidenceError, match="authorization source host invalid"):
        module.build_evidence(
            cleanup_output=_cleanup_output(AUTHORIZATION_SOURCE_HOST="other"),
            observed_at=datetime(2026, 10, 5, 20, 30, tzinfo=UTC),
        )

    with pytest.raises(module.EvidenceError, match="cannot precede runtime expiry"):
        module.build_evidence(
            cleanup_output=_cleanup_output(),
            observed_at=datetime(2026, 10, 1, 20, 30, tzinfo=UTC),
        )


def test_zero_attempt_cleanup_evidence_builder_is_local_only() -> None:
    text = BUILDER.read_text(encoding="utf-8")
    for forbidden in (
        "gcloud ",
        "systemctl ",
        "subprocess",
        "requests",
        "httpx",
        "post_order",
        "executor.sh",
    ):
        assert forbidden not in text
    assert "PRODUCTION_MUTATION_PERFORMED=false" in text
    assert "REAL_ORDER_SUBMITTED=false" in text
