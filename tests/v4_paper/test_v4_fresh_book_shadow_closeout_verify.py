from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_v4_fresh_book_shadow_closeout.py"
RUN_ID = "v4-fresh-book-shadow-20261006T140323Z-f5c76576619c"
MODEL_SHA = "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"


def _load():
    spec = importlib.util.spec_from_file_location("v4_closeout", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _records(*, elapsed_seconds: int = 86400) -> list[dict[str, object]]:
    started_at = datetime(2026, 10, 6, 14, 3, 23, tzinfo=UTC)
    return [
        {
            "event": "v4_fresh_book_shadow_started",
            "started_at": started_at.isoformat(),
            "model_sha256": MODEL_SHA,
            "source_feature_version": "v4-source-time-features-v2",
            "decision_offset_seconds": 240,
            "max_btc_source_age_seconds": 2.0,
            "max_btc_future_skew_seconds": 1.0,
            "max_decision_lag_seconds": 2.0,
            "core_source_policy": "require_market_start_and_current_all_venues",
            "quote_fresh_seconds": 0.25,
            "target_notional_usd": "5.00",
            "frozen_min_edge": "0.05",
            "database_read_only": True,
            "order_submission_enabled": False,
            "wallet_material_loaded": False,
            "holdout_labels_read": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        },
        {
            "event": "v4_fresh_book_shadow_evaluated",
            "prediction_id": "p1",
        },
        {
            "event": "v4_fresh_book_shadow_completed",
            "completed_at": (
                started_at + timedelta(seconds=elapsed_seconds)
            ).isoformat(),
            "seen_market_count": 4,
            "prediction_count": 2,
            "evaluated_count": 1,
            "quote_unavailable_count": 1,
            "decision_missed_count": 1,
            "source_ineligible_count": 1,
            "core_source_policy": "require_market_start_and_current_all_venues",
            "extreme_edge_evaluated_count": 1,
            "extreme_edge_trade_count": 1,
            "database_read_only": True,
            "database_writes_performed": False,
            "order_submission_enabled": False,
            "order_submission_performed": False,
            "wallet_material_loaded": False,
            "holdout_labels_read": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        },
    ]


def _write(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / f"{RUN_ID}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def _verify(module, path: Path):
    return module.verify_closeout(
        path,
        expected_run_id=RUN_ID,
        expected_model_sha256=MODEL_SHA,
        expected_run_seconds=86400,
        duration_tolerance_seconds=120,
    )


def test_closeout_verifier_accepts_exact_completed_safe_run(tmp_path: Path) -> None:
    module = _load()
    report = _verify(module, _write(tmp_path, _records()))

    assert report["status"] == "PASS"
    assert report["run_id"] == RUN_ID
    assert report["elapsed_seconds"] == 86400.0
    assert len(report["evidence_sha256"]) == 64
    assert report["database_writes_performed"] is False
    assert report["order_submission_performed"] is False


def test_closeout_verifier_rejects_missing_completion(tmp_path: Path) -> None:
    module = _load()
    path = _write(tmp_path, _records()[:-1])

    with pytest.raises(
        module.V4FreshBookShadowCloseoutError,
        match="exactly one completion",
    ):
        _verify(module, path)


def test_closeout_verifier_rejects_nonterminal_completion(tmp_path: Path) -> None:
    module = _load()
    records = _records()
    records.append({"event": "unexpected_after_completion"})
    path = _write(tmp_path, records)

    with pytest.raises(
        module.V4FreshBookShadowCloseoutError,
        match="final evidence event",
    ):
        _verify(module, path)


def test_closeout_verifier_rejects_short_run(tmp_path: Path) -> None:
    module = _load()
    path = _write(tmp_path, _records(elapsed_seconds=86000))

    with pytest.raises(
        module.V4FreshBookShadowCloseoutError,
        match="duration mismatch",
    ):
        _verify(module, path)


def test_closeout_verifier_rejects_safety_violation(tmp_path: Path) -> None:
    module = _load()
    records = _records()
    records[-1]["order_submission_performed"] = True
    path = _write(tmp_path, records)

    with pytest.raises(
        module.V4FreshBookShadowCloseoutError,
        match="order_submission_performed mismatch",
    ):
        _verify(module, path)


def test_closeout_verifier_rejects_counter_mismatch(tmp_path: Path) -> None:
    module = _load()
    records = _records()
    records[-1]["prediction_count"] = 3
    path = _write(tmp_path, records)

    with pytest.raises(
        module.V4FreshBookShadowCloseoutError,
        match="seen-market accounting mismatch",
    ):
        _verify(module, path)
