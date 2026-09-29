from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LISTENER = ROOT / "scripts" / "run_phase15_v3_canary_telegram_approval.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "phase15_fast_live_telegram_listener",
        LISTENER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime, *, risk_pending: bool) -> dict[str, object]:
    prepared: dict[str, object] = {
        "status": "prepared",
        "action": "submit",
        "intent_id": "candidate-fast-preview",
        "prediction_id": "prediction-fast-preview",
        "paper_order_id": "paper-fast-preview",
        "market_end_at": (now + timedelta(seconds=50)).isoformat(),
        "request": {
            "selected_side": "up",
            "limit_price": "0.59",
            "requested_shares": "8.238141",
            "target_notional_usd": "5",
            "token_id": "token-fast-preview",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }
    if risk_pending:
        prepared["risk_status"] = "pending"
    return prepared


def test_fast_live_preview_prompt_explains_parallel_final_gates() -> None:
    module = _module()
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    prompt = module._prompt_text(
        _prepared(now, risk_pending=True),
        observed_at=now,
    )

    assert "LIVE TRADE CANDIDATE" in prompt
    assert "Final live risk and Johannesburg execution checks are still running." in prompt
    assert "Approval does not bypass them." in prompt
    assert "when every final gate passes" in prompt
    assert "Limit: 0.59" in prompt
    assert "Maximum spend: $5" in prompt


def test_non_preview_prompt_uses_reviewed_legacy_contract() -> None:
    module = _module()
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    prompt = module._prompt_text(
        _prepared(now, risk_pending=False),
        observed_at=now,
    )

    assert prompt.startswith("BP V3 LIVE TRADE READY")
    assert "Final live risk and Johannesburg execution checks" not in prompt
