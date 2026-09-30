from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts" / "run_phase15_v3_fast_live_source.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "phase15_fast_live_source_continuous_state",
        SOURCE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_continuous_publication_state_ignores_prior_consumed_attempts(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "published"
    results = root / "results"
    root.mkdir()
    results.mkdir()

    intent_id = "intent-complete"
    request_hash = "1" * 64
    receipt = module._receipt_path(root, intent_id, request_hash)
    result = module._result_receipt_path(root, intent_id, request_hash)
    _write(
        receipt,
        {
            "intent_id": intent_id,
            "request_sha256": request_hash,
        },
    )
    _write(
        result,
        {
            "intent_id": intent_id,
            "request_sha256": request_hash,
            "network_submission_attempt_consumed": True,
        },
    )

    assert module._publication_state(root) == "attempt_consumed"
    assert (
        module._publication_state(root, continuous_session=True)
        == "ready"
    )
    assert module._pending_result_binding(root) is None


def test_continuous_restart_recovers_exact_unresolved_result_binding(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "published"
    (root / "results").mkdir(parents=True)

    completed_intent = "intent-old"
    completed_hash = "2" * 64
    _write(
        module._receipt_path(root, completed_intent, completed_hash),
        {
            "intent_id": completed_intent,
            "request_sha256": completed_hash,
        },
    )
    _write(
        module._result_receipt_path(root, completed_intent, completed_hash),
        {
            "intent_id": completed_intent,
            "request_sha256": completed_hash,
            "network_submission_attempt_consumed": True,
        },
    )

    pending_intent = "intent-current"
    pending_hash = "3" * 64
    _write(
        module._receipt_path(root, pending_intent, pending_hash),
        {
            "intent_id": pending_intent,
            "request_sha256": pending_hash,
        },
    )

    assert (
        module._publication_state(root, continuous_session=True)
        == "waiting_for_result"
    )
    assert module._pending_result_binding(root) == (
        pending_intent,
        pending_hash,
    )


def test_result_hash_is_canonical_and_detects_replay_conflicts() -> None:
    module = _module()
    base = {
        "status": "accepted",
        "intent_id": "intent-result-hash",
        "request_sha256": "9" * 64,
        "network_submission_attempt_consumed": True,
        "real_order_submitted": True,
        "external_order_id": "order-1",
        "official_reconciliation": {
            "official_reconciliation_complete": True,
            "fill_state": "zero_fill_observed",
        },
    }
    reordered = dict(reversed(tuple(base.items())))
    assert module._result_sha256(base) == module._result_sha256(reordered)

    redelivered = dict(base)
    redelivered["replayed_result"] = True
    redelivered["approval_received_at"] = "2026-09-29T13:00:00+00:00"
    redelivered["approval_to_receive_ms"] = 1250.0
    redelivered["prepare_created_at"] = "2026-09-29T12:59:58+00:00"
    assert module._result_sha256(base) == module._result_sha256(redelivered)

    changed_order = dict(base)
    changed_order["external_order_id"] = "order-2"
    assert module._result_sha256(base) != module._result_sha256(changed_order)

    changed_official = dict(base)
    changed_official["official_reconciliation"] = {
        "official_reconciliation_complete": True,
        "fill_state": "confirmed_fill",
    }
    assert module._result_sha256(base) != module._result_sha256(changed_official)


def test_pending_result_deadline_is_recovered_from_publication_receipt(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "published"
    (root / "results").mkdir(parents=True)

    intent_id = "intent-waiting"
    request_hash = "4" * 64
    published_at = "2026-09-29T12:00:00+00:00"
    _write(
        module._receipt_path(root, intent_id, request_hash),
        {
            "status": "fast_live_approval_published",
            "intent_id": intent_id,
            "request_sha256": request_hash,
            "published_at": published_at,
        },
    )

    deadline = module._result_wait_deadline(
        root,
        intent_id,
        request_hash,
    )

    assert deadline.isoformat() == "2026-09-29T12:00:20+00:00"


def test_stale_telegram_preview_is_not_reused_across_live_sessions(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "telegram"
    preview = {
        "intent_id": "candidate-preview-stale",
        "prediction_id": "prediction-stale",
        "paper_order_id": "paper-stale",
    }
    module._stage_telegram_candidate(
        root,
        preview,
        authorization_id="auth-old",
    )

    loaded = module._load_staged_telegram_candidate(
        root,
        expected_authorization_id="auth-new",
    )

    assert loaded is None
    assert not (root / "current-run").exists()


def test_settlement_marker_removes_completed_fill_from_restart_recovery(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "published"
    (root / "results").mkdir(parents=True)
    (root / "settlements").mkdir()

    intent_id = "intent-settled"
    request_hash = "5" * 64
    result_path = module._result_receipt_path(
        root,
        intent_id,
        request_hash,
    )
    _write(
        result_path,
        {
            "intent_id": intent_id,
            "request_sha256": request_hash,
            "official_recorded": {
                "settlement_reconciliation_required": True,
            },
        },
    )

    assert module._pending_settlement_intent(root) == intent_id
    marker = root / "settlements" / result_path.name
    _write(
        marker,
        {
            "status": "fast_live_settlement_recorded",
            "intent_id": intent_id,
        },
    )
    assert module._pending_settlement_intent(root) == ""
    assert (
        module._settlement_marker_path(
            root,
            intent_id=intent_id,
        )
        == marker
    )


def test_final_live_intent_clears_provisional_telegram_run(
    tmp_path: Path,
) -> None:
    module = _module()
    root = tmp_path / "telegram"
    preview = {
        "intent_id": "candidate-preview-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
    }
    module._stage_telegram_candidate(
        root,
        preview,
        authorization_id="auth-session-1",
    )
    module._write_telegram_state_once(
        root,
        "candidate-preview-1",
        "finalized.json",
        {
            "intent_id": "live-intent-final-1",
            "prediction_id": "prediction-1",
            "paper_order_id": "paper-1",
        },
    )

    assert (root / "current-run").is_file()
    module._clear_staged_telegram_candidate(
        root,
        "live-intent-final-1",
    )
    assert not (root / "current-run").exists()


def test_prepare_candidate_transport_verifies_auth_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    state_path = tmp_path / "PROJECT_STATE.json"
    auth_path = tmp_path / "authorization.json"
    telegram_root = tmp_path / "telegram"
    state = {"source": "state"}
    runtime = {"authorization_id": "auth-session"}
    preview = {
        "status": "prepared",
        "intent_id": "candidate-parallel-1",
        "prediction_id": "prediction-parallel-1",
        "paper_order_id": "paper-parallel-1",
    }
    calls: list[str] = []
    fake_future = object()

    monkeypatch.setattr(module, "_load_state", lambda path: state)
    monkeypatch.setattr(
        module,
        "load_private_json",
        lambda path, *, label: runtime,
    )

    def verify_runtime(
        payload: dict[str, object],
        *,
        state: dict[str, object],
        expected_main: str,
        observed_at: object,
        requires_telegram_approval: bool,
        continuous_session: bool,
    ) -> None:
        assert payload is runtime
        assert expected_main == "a" * 40
        assert requires_telegram_approval is True
        assert continuous_session is True
        calls.append("verified")

    monkeypatch.setattr(
        module,
        "verify_runtime_authorization",
        verify_runtime,
    )

    def create_prepare(
        prepared: dict[str, object],
        *,
        runtime_authorization: dict[str, object],
        key: bytes,
        key_id: str,
        created_at: object,
    ) -> dict[str, object]:
        assert prepared is preview
        assert runtime_authorization is runtime
        assert key == b"k" * 32
        assert key_id == "key-1"
        calls.append("message")
        return {
            "expires_at": (
                module._utc_now() + module.timedelta(seconds=5)
            ).isoformat(),
            "purpose": "phase15-v3-fast-live-prepare-v1",
            "authorization_id": "auth-session",
            "intent_id": preview["intent_id"],
            "request_sha256": "1" * 64,
        }

    monkeypatch.setattr(module, "create_prepare_message", create_prepare)

    def start_publish(
        publisher: object,
        *,
        topic_path: str,
        payload: dict[str, object],
    ) -> object:
        assert topic_path == "projects/p/topics/orders"
        assert payload["intent_id"] == preview["intent_id"]
        assert (telegram_root / "current-run").is_file()
        calls.append("publish")
        return fake_future

    monkeypatch.setattr(module, "_start_control_publish", start_publish)

    result = module._prepare_candidate_transport(
        project_state=state_path,
        runtime_authorization=auth_path,
        telegram_prepare_state_root=telegram_root,
        preview=preview,
        expected_main="a" * 40,
        continuous_session=True,
        key=b"k" * 32,
        key_id="key-1",
        publisher=object(),
        topic_path="projects/p/topics/orders",
    )

    assert calls == ["verified", "message", "publish"]
    assert result["runtime"] is runtime
    assert result["prepare_publish_future"] is fake_future
    assert result["prepared_path"].is_file()
    timing = result["timing"]
    for key in (
        "prepare_auth_verify_ms",
        "prepare_stage_candidate_ms",
        "prepare_message_build_ms",
        "prepare_publish_start_ms",
        "prepare_setup_total_ms",
    ):
        assert timing[key] >= 0


def test_prepare_candidate_transport_fails_before_staging_when_auth_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    telegram_root = tmp_path / "telegram"
    preview = {
        "status": "prepared",
        "intent_id": "candidate-parallel-invalid-auth",
        "prediction_id": "prediction-parallel-invalid-auth",
        "paper_order_id": "paper-parallel-invalid-auth",
    }
    monkeypatch.setattr(module, "_load_state", lambda path: {})
    monkeypatch.setattr(
        module,
        "load_private_json",
        lambda path, *, label: {"authorization_id": "auth-session"},
    )

    def reject_runtime(*args: object, **kwargs: object) -> None:
        raise RuntimeError("authorization invalid")

    monkeypatch.setattr(
        module,
        "verify_runtime_authorization",
        reject_runtime,
    )

    with pytest.raises(RuntimeError, match="authorization invalid"):
        module._prepare_candidate_transport(
            project_state=tmp_path / "PROJECT_STATE.json",
            runtime_authorization=tmp_path / "authorization.json",
            telegram_prepare_state_root=telegram_root,
            preview=preview,
            expected_main="b" * 40,
            continuous_session=True,
            key=b"k" * 32,
            key_id="key-2",
            publisher=object(),
            topic_path="projects/p/topics/orders",
        )

    assert not (telegram_root / "current-run").exists()
