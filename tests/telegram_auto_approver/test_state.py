from __future__ import annotations

import os
import stat

from bp_telegram_auto_approver.state import ApprovalStore, NewApproval


def _row(message_id: int = 1, nonce: str = "Abcdefghijklmnop") -> NewApproval:
    return NewApproval(
        telegram_chat_id=5,
        telegram_message_id=message_id,
        nonce=nonce,
        status="reserved",
        reason=None,
        side="DOWN",
        limit_price="0.72",
        shares="6.81",
        maximum_spend="5",
        time_remaining_seconds="50.0",
        deadline_at="2026-09-27T12:00:40+00:00",
        received_at="2026-09-27T12:00:02+00:00",
        approval_at=None,
        result="reserved",
        failure_reason=None,
    )


def test_sqlite_file_is_private_and_schema_reopens(tmp_path) -> None:
    path = tmp_path / "approvals.sqlite"
    store = ApprovalStore(path)
    created = store.insert(_row())
    assert created.created
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    store.close()
    reopened = ApprovalStore(path)
    assert reopened.get_by_message(5, 1)["status"] == "reserved"
    assert reopened.sweep_unconfirmed("2026-09-27T12:01:00+00:00") == 1
    assert reopened.get_by_message(5, 1)["status"] == "failed_or_unknown"
    assert reopened.sweep_unconfirmed("2026-09-27T12:02:00+00:00") == 0
    reopened.close()


def test_nonce_and_message_identity_are_unique(tmp_path) -> None:
    store = ApprovalStore(tmp_path / "approvals.sqlite")
    assert store.insert(_row()).created
    assert not store.insert(_row(message_id=2)).created
    assert not store.insert(_row(message_id=1, nonce="ZZZZZZZZZZZZZZZZ")).created
    assert store.get_by_nonce("Abcdefghijklmnop")["telegram_message_id"] == 1
    store.close()
