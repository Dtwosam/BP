from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
