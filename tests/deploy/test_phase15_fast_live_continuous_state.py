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
