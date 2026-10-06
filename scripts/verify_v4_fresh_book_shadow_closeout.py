from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

STARTED_EVENT = "v4_fresh_book_shadow_started"
COMPLETED_EVENT = "v4_fresh_book_shadow_completed"
EXPECTED_SOURCE_FEATURE_VERSION = "v4-source-time-features-v2"
EXPECTED_CORE_SOURCE_POLICY = "require_market_start_and_current_all_venues"
EXPECTED_OFFSET_SECONDS = 240
EXPECTED_SOURCE_AGE_SECONDS = Decimal("2.0")
EXPECTED_FUTURE_SKEW_SECONDS = Decimal("1.0")
EXPECTED_DECISION_LAG_SECONDS = Decimal("2.0")
EXPECTED_QUOTE_FRESH_SECONDS = Decimal("0.25")
EXPECTED_TARGET_NOTIONAL_USD = Decimal("5.00")
EXPECTED_MIN_EDGE = Decimal("0.05")
EXPECTED_SOURCE_RETRY_POLICY = "retry_core_source_ineligible_within_max_decision_lag"
EXPECTED_SOURCE_RETRY_PROBE = "core_six_anchor_only"
EXPECTED_SOURCE_RECEIVED_CUTOFF = "received_at_lte_decision_at"


class V4FreshBookShadowCloseoutError(RuntimeError):
    """Raised when bounded V4 shadow evidence is incomplete or unsafe."""


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise V4FreshBookShadowCloseoutError(f"{name} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise V4FreshBookShadowCloseoutError(f"{name} must be numeric") from exc
    if not parsed.is_finite():
        raise V4FreshBookShadowCloseoutError(f"{name} must be finite")
    return parsed


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise V4FreshBookShadowCloseoutError(f"{name} must be an integer")
    if value < 0:
        raise V4FreshBookShadowCloseoutError(f"{name} must be non-negative")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise V4FreshBookShadowCloseoutError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V4FreshBookShadowCloseoutError(
            f"{name} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V4FreshBookShadowCloseoutError(
            f"{name} must be timezone-aware"
        )
    return parsed.astimezone(UTC)


def _require(record: dict[str, Any], name: str, expected: object) -> None:
    actual = record.get(name)
    if actual != expected:
        raise V4FreshBookShadowCloseoutError(
            f"{name} mismatch: expected={expected!r} actual={actual!r}"
        )


def _require_decimal(
    record: dict[str, Any],
    name: str,
    expected: Decimal,
) -> None:
    actual = _decimal(record.get(name), name)
    if actual != expected:
        raise V4FreshBookShadowCloseoutError(
            f"{name} mismatch: expected={expected} actual={actual}"
        )


def _load_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file() or path.is_symlink():
        raise V4FreshBookShadowCloseoutError(
            "evidence must be a regular non-symlink file"
        )
    records: list[dict[str, Any]] = []
    ignored_non_json_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            if not stripped.startswith("{"):
                ignored_non_json_lines += 1
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise V4FreshBookShadowCloseoutError(
                    f"malformed JSON at line {line_number}"
                ) from exc
            if not isinstance(parsed, dict):
                raise V4FreshBookShadowCloseoutError(
                    f"non-object JSON at line {line_number}"
                )
            records.append(parsed)
    if not records:
        raise V4FreshBookShadowCloseoutError(
            "evidence file contains no JSON event records"
        )
    return records, ignored_non_json_lines


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_closeout(
    path: Path,
    *,
    expected_run_id: str,
    expected_model_sha256: str,
    expected_run_seconds: int,
    duration_tolerance_seconds: int,
) -> dict[str, Any]:
    if not expected_run_id:
        raise V4FreshBookShadowCloseoutError("expected_run_id is required")
    if path.stem != expected_run_id:
        raise V4FreshBookShadowCloseoutError(
            f"run id mismatch: expected={expected_run_id} file={path.stem}"
        )
    if (
        len(expected_model_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in expected_model_sha256)
    ):
        raise V4FreshBookShadowCloseoutError(
            "expected_model_sha256 must be lowercase hex"
        )
    if expected_run_seconds <= 0:
        raise V4FreshBookShadowCloseoutError(
            "expected_run_seconds must be positive"
        )
    if duration_tolerance_seconds < 0:
        raise V4FreshBookShadowCloseoutError(
            "duration_tolerance_seconds must be non-negative"
        )

    records, ignored_non_json_lines = _load_records(path)
    starts = [record for record in records if record.get("event") == STARTED_EVENT]
    completions = [
        record for record in records if record.get("event") == COMPLETED_EVENT
    ]
    if len(starts) != 1:
        raise V4FreshBookShadowCloseoutError(
            f"expected exactly one start record, found {len(starts)}"
        )
    if len(completions) != 1:
        raise V4FreshBookShadowCloseoutError(
            f"expected exactly one completion record, found {len(completions)}"
        )
    if records[-1].get("event") != COMPLETED_EVENT:
        raise V4FreshBookShadowCloseoutError(
            "completion record must be the final evidence event"
        )

    started = starts[0]
    completed = completions[0]
    _require(started, "model_sha256", expected_model_sha256)
    _require(
        started,
        "source_feature_version",
        EXPECTED_SOURCE_FEATURE_VERSION,
    )
    _require(started, "decision_offset_seconds", EXPECTED_OFFSET_SECONDS)
    _require(started, "core_source_policy", EXPECTED_CORE_SOURCE_POLICY)
    _require_decimal(
        started,
        "max_btc_source_age_seconds",
        EXPECTED_SOURCE_AGE_SECONDS,
    )
    _require_decimal(
        started,
        "max_btc_future_skew_seconds",
        EXPECTED_FUTURE_SKEW_SECONDS,
    )
    _require_decimal(
        started,
        "max_decision_lag_seconds",
        EXPECTED_DECISION_LAG_SECONDS,
    )
    _require_decimal(
        started,
        "quote_fresh_seconds",
        EXPECTED_QUOTE_FRESH_SECONDS,
    )
    _require_decimal(
        started,
        "target_notional_usd",
        EXPECTED_TARGET_NOTIONAL_USD,
    )
    _require_decimal(started, "frozen_min_edge", EXPECTED_MIN_EDGE)
    for name, expected in (
        ("database_read_only", True),
        ("order_submission_enabled", False),
        ("wallet_material_loaded", False),
        ("holdout_labels_read", False),
        ("model_refit_performed", False),
        ("threshold_tuning_performed", False),
    ):
        _require(started, name, expected)

    _require(completed, "core_source_policy", EXPECTED_CORE_SOURCE_POLICY)
    for name, expected in (
        ("database_read_only", True),
        ("database_writes_performed", False),
        ("order_submission_enabled", False),
        ("order_submission_performed", False),
        ("wallet_material_loaded", False),
        ("holdout_labels_read", False),
        ("model_refit_performed", False),
        ("threshold_tuning_performed", False),
    ):
        _require(completed, name, expected)

    started_at = _timestamp(started.get("started_at"), "started_at")
    completed_at = _timestamp(completed.get("completed_at"), "completed_at")
    elapsed_seconds = (completed_at - started_at).total_seconds()
    lower = expected_run_seconds - duration_tolerance_seconds
    upper = expected_run_seconds + duration_tolerance_seconds
    if elapsed_seconds < lower or elapsed_seconds > upper:
        raise V4FreshBookShadowCloseoutError(
            "bounded run duration mismatch: "
            f"expected={expected_run_seconds}s tolerance={duration_tolerance_seconds}s "
            f"actual={elapsed_seconds}s"
        )

    seen = _integer(completed.get("seen_market_count"), "seen_market_count")
    predictions = _integer(
        completed.get("prediction_count"),
        "prediction_count",
    )
    evaluated = _integer(
        completed.get("evaluated_count"),
        "evaluated_count",
    )
    quote_unavailable = _integer(
        completed.get("quote_unavailable_count"),
        "quote_unavailable_count",
    )
    decision_missed = _integer(
        completed.get("decision_missed_count"),
        "decision_missed_count",
    )
    source_ineligible = _integer(
        completed.get("source_ineligible_count"),
        "source_ineligible_count",
    )

    retry_fields = (
        "source_retry_deferral_count",
        "source_retry_recovered_count",
        "source_retry_exhausted_count",
        "source_retry_pending_count",
    )
    retry_field_presence = tuple(name in completed for name in retry_fields)
    retry_accounting_present = any(retry_field_presence)
    retry_deferrals: int | None = None
    retry_recovered: int | None = None
    retry_exhausted: int | None = None
    retry_pending: int | None = None
    if retry_accounting_present:
        if not all(retry_field_presence):
            missing = [
                name
                for name, present in zip(retry_fields, retry_field_presence, strict=True)
                if not present
            ]
            raise V4FreshBookShadowCloseoutError(
                "partial source-retry accounting: missing=" + ",".join(missing)
            )
        retry_deferrals = _integer(
            completed.get("source_retry_deferral_count"),
            "source_retry_deferral_count",
        )
        retry_recovered = _integer(
            completed.get("source_retry_recovered_count"),
            "source_retry_recovered_count",
        )
        retry_exhausted = _integer(
            completed.get("source_retry_exhausted_count"),
            "source_retry_exhausted_count",
        )
        retry_pending = _integer(
            completed.get("source_retry_pending_count"),
            "source_retry_pending_count",
        )
        _require(started, "source_retry_policy", EXPECTED_SOURCE_RETRY_POLICY)
        _require(started, "source_retry_probe", EXPECTED_SOURCE_RETRY_PROBE)
        _require(
            started,
            "source_received_cutoff",
            EXPECTED_SOURCE_RECEIVED_CUTOFF,
        )
        _require(completed, "source_retry_policy", EXPECTED_SOURCE_RETRY_POLICY)
        _require(completed, "source_retry_probe", EXPECTED_SOURCE_RETRY_PROBE)
        _require(
            completed,
            "source_received_cutoff",
            EXPECTED_SOURCE_RECEIVED_CUTOFF,
        )
        if retry_pending != 0:
            raise V4FreshBookShadowCloseoutError(
                "source-retry pending count must be zero at completion"
            )
        if retry_recovered + retry_exhausted > retry_deferrals:
            raise V4FreshBookShadowCloseoutError(
                "source-retry terminal accounting exceeds deferrals"
            )
        if retry_recovered > predictions:
            raise V4FreshBookShadowCloseoutError(
                "source-retry recovered count exceeds predictions"
            )
        if retry_exhausted > source_ineligible:
            raise V4FreshBookShadowCloseoutError(
                "source-retry exhausted count exceeds source-ineligible count"
            )

    extreme_evaluated = _integer(
        completed.get("extreme_edge_evaluated_count"),
        "extreme_edge_evaluated_count",
    )
    extreme_trades = _integer(
        completed.get("extreme_edge_trade_count"),
        "extreme_edge_trade_count",
    )
    if seen != predictions + decision_missed + source_ineligible:
        raise V4FreshBookShadowCloseoutError(
            "seen-market accounting mismatch"
        )
    if predictions != evaluated + quote_unavailable:
        raise V4FreshBookShadowCloseoutError(
            "prediction accounting mismatch"
        )
    if extreme_evaluated > evaluated:
        raise V4FreshBookShadowCloseoutError(
            "extreme-edge evaluated count exceeds evaluated count"
        )
    if extreme_trades > extreme_evaluated:
        raise V4FreshBookShadowCloseoutError(
            "extreme-edge trade count exceeds extreme evaluated count"
        )

    return {
        "status": "PASS",
        "run_id": expected_run_id,
        "evidence_file": str(path),
        "evidence_sha256": _sha256(path),
        "ignored_non_json_line_count": ignored_non_json_lines,
        "model_sha256": expected_model_sha256,
        "source_feature_version": EXPECTED_SOURCE_FEATURE_VERSION,
        "expected_run_seconds": expected_run_seconds,
        "duration_tolerance_seconds": duration_tolerance_seconds,
        "elapsed_seconds": elapsed_seconds,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "seen_market_count": seen,
        "prediction_count": predictions,
        "evaluated_count": evaluated,
        "quote_unavailable_count": quote_unavailable,
        "decision_missed_count": decision_missed,
        "source_ineligible_count": source_ineligible,
        "source_retry_accounting_present": retry_accounting_present,
        "source_retry_deferral_count": retry_deferrals,
        "source_retry_recovered_count": retry_recovered,
        "source_retry_exhausted_count": retry_exhausted,
        "source_retry_pending_count": retry_pending,
        "source_retry_policy": (
            EXPECTED_SOURCE_RETRY_POLICY if retry_accounting_present else None
        ),
        "source_retry_probe": (
            EXPECTED_SOURCE_RETRY_PROBE if retry_accounting_present else None
        ),
        "source_received_cutoff": (
            EXPECTED_SOURCE_RECEIVED_CUTOFF if retry_accounting_present else None
        ),
        "database_read_only": True,
        "database_writes_performed": False,
        "order_submission_performed": False,
        "wallet_material_loaded": False,
        "holdout_labels_read": False,
        "model_refit_performed": False,
        "threshold_tuning_performed": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that one exact V4 fresh-book shadow completed its bounded "
            "zero-money run normally and preserved the frozen safety contract."
        )
    )
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-model-sha256", required=True)
    parser.add_argument("--expected-run-seconds", required=True, type=int)
    parser.add_argument(
        "--duration-tolerance-seconds",
        type=int,
        default=120,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = verify_closeout(
            Path(args.evidence),
            expected_run_id=args.expected_run_id,
            expected_model_sha256=args.expected_model_sha256,
            expected_run_seconds=args.expected_run_seconds,
            duration_tolerance_seconds=args.duration_tolerance_seconds,
        )
    except V4FreshBookShadowCloseoutError as exc:
        print(f"PHASE14_V4_FRESH_BOOK_SHADOW_CLOSEOUT_VERIFY=FAIL:{exc}")
        return 1

    print(json.dumps(report, sort_keys=True, indent=2))
    print("PHASE14_V4_FRESH_BOOK_SHADOW_CLOSEOUT_VERIFY=PASS")
    print(f"EVIDENCE_SHA256={report['evidence_sha256']}")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    print("WALLET_MATERIAL_LOADED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
