from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.telegram_dispatch_ticket import (
    DispatchTicketError,
    claim_dispatch_ticket,
    create_dispatch_ticket,
    verify_dispatch_ticket,
    verify_dispatch_ticket_against_report,
)
from bp_engine.execution.telegram_pre_execution import (
    evaluate_pre_execution_authorization,
)


MODULE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "bp_engine"
    / "execution"
    / "telegram_dispatch_ticket.py"
)


def _ready(now: datetime) -> dict[str, object]:
    return {
        "status": "execution_ready_origin_verified",
        "transport_key_id": "phase15-telegram-transport-v1",
        "origin_key_id": "phase15-telegram-origin-v1",
        "intent_id": "live-intent-dispatch",
        "prediction_id": "prediction-dispatch",
        "paper_order_id": "paper-dispatch",
        "request_sha256": "1" * 64,
        "prepared_sha256": "2" * 64,
        "approval_sha256": "3" * 64,
        "approval_source_sha256": "4" * 64,
        "origin_attestation_sha256": "5" * 64,
        "origin_attested_at": now.isoformat(),
        "origin_expires_at": (now + timedelta(seconds=15)).isoformat(),
        "retry_allowed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _authorized_state() -> dict[str, object]:
    return {
        "live_trading_enabled": False,
        "phase_15_v3_live_canary": {
            "live_trading_enabled": False,
            "phase15_canary_authorized": True,
            "canary_order_submitted": True,
            "pending_unsubmitted_intent": None,
            "v3_strategy_mutation_performed": False,
            "second_order_authorized": True,
            "automated_real_money_submission": True,
            "manual_real_money_submission_required": False,
            "telegram_one_tap_submission_authorized": True,
            "telegram_persistent_execution_transport_authorized": True,
            "telegram_pubsub_transport_authorized": True,
            "first_live_canary": {
                "official_reconciliation_complete": True,
            },
        },
    }


def _report(now: datetime) -> dict[str, object]:
    result = evaluate_pre_execution_authorization(
        ready_verification=_ready(now),
        project_state=_authorized_state(),
    )
    assert result["authorized"] is True
    return result


def test_dispatch_ticket_claim_is_exact_one_shot_and_private(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report = _report(now)
    ticket = create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )
    verified = verify_dispatch_ticket_against_report(
        ticket,
        pre_execution_report=report,
        ready_verification=_ready(now),
        project_state=_authorized_state(),
        observed_at=now + timedelta(seconds=2),
    )
    assert verified == ticket

    state_dir = tmp_path / "claims"
    claimed = claim_dispatch_ticket(
        ticket,
        pre_execution_report=report,
        ready_verification=_ready(now),
        project_state=_authorized_state(),
        observed_at=now + timedelta(seconds=2),
        state_dir=state_dir,
    )
    assert claimed["status"] == "dispatch_claimed"
    assert claimed["retry_allowed"] is False
    assert claimed["executor_invoked"] is False
    assert claimed["real_order_submitted"] is False
    claim_path = Path(claimed["claim_path"])
    assert claim_path.is_file()
    assert (os.stat(state_dir).st_mode & 0o777) == 0o700
    assert (os.stat(claim_path).st_mode & 0o777) == 0o600
    persisted = json.loads(claim_path.read_text(encoding="utf-8"))
    assert persisted["dispatch_ticket_sha256"] == ticket["dispatch_ticket_sha256"]
    assert persisted["authorization_report_sha256"] == report[
        "authorization_report_sha256"
    ]

    with pytest.raises(DispatchTicketError, match="already claimed"):
        claim_dispatch_ticket(
            ticket,
            pre_execution_report=report,
            ready_verification=_ready(now),
            project_state=_authorized_state(),
            observed_at=now + timedelta(seconds=3),
            state_dir=state_dir,
        )


def test_dispatch_claim_rejects_ticket_or_report_mutation_before_consumption(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report = _report(now)
    ticket = create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )

    changed_ticket = copy.deepcopy(ticket)
    changed_ticket["request_sha256"] = "9" * 64
    with pytest.raises(DispatchTicketError, match="hash mismatch"):
        claim_dispatch_ticket(
            changed_ticket,
            pre_execution_report=report,
            ready_verification=_ready(now),
            project_state=_authorized_state(),
            observed_at=now + timedelta(seconds=2),
            state_dir=tmp_path / "ticket-mutation",
        )
    assert not (tmp_path / "ticket-mutation").exists()

    changed_report = copy.deepcopy(report)
    changed_report["request_sha256"] = "8" * 64
    with pytest.raises(
        DispatchTicketError,
        match="stale or modified",
    ):
        claim_dispatch_ticket(
            ticket,
            pre_execution_report=changed_report,
            ready_verification=_ready(now),
            project_state=_authorized_state(),
            observed_at=now + timedelta(seconds=2),
            state_dir=tmp_path / "report-mutation",
        )
    assert not (tmp_path / "report-mutation").exists()


def test_dispatch_ticket_expiry_and_symlink_state_fail_closed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report = _report(now)
    ticket = create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )
    expires = datetime.fromisoformat(str(ticket["expires_at"]))
    with pytest.raises(DispatchTicketError, match="expired"):
        verify_dispatch_ticket(ticket, observed_at=expires)

    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "claims"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(DispatchTicketError, match="non-symlink directory"):
        claim_dispatch_ticket(
            ticket,
            pre_execution_report=report,
            ready_verification=_ready(now),
            project_state=_authorized_state(),
            observed_at=now + timedelta(seconds=2),
            state_dir=link,
        )


def test_dispatch_ticket_rejects_blocked_pre_execution_report() -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    report = _report(now)
    report["status"] = "pre_execution_blocked"
    report["authorized"] = False
    report["blockers"] = ["synthetic-blocker"]
    with pytest.raises(DispatchTicketError, match="not authorized"):
        create_dispatch_ticket(
            report,
            created_at=now + timedelta(seconds=1),
        )


def test_dispatch_ticket_module_has_no_network_wallet_or_execution_path() -> None:
    text = MODULE.read_text(encoding="utf-8")
    compile(text, str(MODULE), "exec")
    for forbidden in (
        "httpx",
        "urllib",
        "requests",
        "subprocess",
        "gcloud",
        "google.cloud",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in text


def test_dispatch_claim_rejects_current_source_truth_drift_before_consumption(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    ready = _ready(now)
    state = _authorized_state()
    report = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )
    ticket = create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )
    changed_state = copy.deepcopy(state)
    phase = changed_state["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["second_order_authorized"] = False
    state_dir = tmp_path / "source-truth-drift"

    with pytest.raises(DispatchTicketError, match="stale or modified"):
        claim_dispatch_ticket(
            ticket,
            pre_execution_report=report,
            ready_verification=ready,
            project_state=changed_state,
            observed_at=now + timedelta(seconds=2),
            state_dir=state_dir,
        )
    assert not state_dir.exists()


def test_dispatch_claim_rejects_current_ready_drift_before_consumption(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, 2, tzinfo=UTC)
    ready = _ready(now)
    state = _authorized_state()
    report = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )
    ticket = create_dispatch_ticket(
        report,
        created_at=now + timedelta(seconds=1),
    )
    changed_ready = dict(ready)
    changed_ready["request_sha256"] = "7" * 64
    state_dir = tmp_path / "ready-drift"

    with pytest.raises(DispatchTicketError, match="stale or modified"):
        claim_dispatch_ticket(
            ticket,
            pre_execution_report=report,
            ready_verification=changed_ready,
            project_state=state,
            observed_at=now + timedelta(seconds=2),
            state_dir=state_dir,
        )
    assert not state_dir.exists()
