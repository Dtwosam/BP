from __future__ import annotations

import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from bp_engine.backtesting.models import BACKTEST_VERSION
from bp_engine.backtesting.repository import BacktestRunRepository


@dataclass(frozen=True)
class SourceFoldSpec:
    index: int
    membership_sha256: str
    train_condition_ids: tuple[str, ...]
    validation_condition_ids: tuple[str, ...]
    test_condition_ids: tuple[str, ...]
    selected_offset_seconds: int


@dataclass(frozen=True)
class FinalSourceSpec:
    membership_sha256: str
    train_condition_ids: tuple[str, ...]
    validation_condition_ids: tuple[str, ...]
    holdout_condition_ids: tuple[str, ...]
    selected_offset_seconds: int


@dataclass(frozen=True)
class BacktestSourceSpec:
    run_id: str
    backtest_version: str
    semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    dataset_sha256: str
    config_sha256: str
    plan_sha256: str
    fold_membership_sha256: tuple[str, ...]
    folds: tuple[SourceFoldSpec, ...]
    final: FinalSourceSpec


class BacktestSourceNotFound(LookupError):
    """Raised when a requested immutable Phase 8 source run is absent."""


class BacktestSourceIntegrityError(ValueError):
    """Raised when a Phase 8 source run violates the restricted source contract."""


def _sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise BacktestSourceIntegrityError(f"{name} must be a 64-character SHA-256")
    if any(char not in string.hexdigits for char in value):
        raise BacktestSourceIntegrityError(f"{name} must be hexadecimal SHA-256")
    return value.lower()


def _mapping(name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BacktestSourceIntegrityError(f"{name} must be a mapping")
    return value


def _sequence(name: str, value: object) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BacktestSourceIntegrityError(f"{name} must be a sequence")
    return value


def _ids(name: str, value: object) -> tuple[str, ...]:
    items = _sequence(name, value)
    ids = tuple(str(item) for item in items)
    if any(not item for item in ids):
        raise BacktestSourceIntegrityError(f"{name} contains an empty condition id")
    if len(ids) != len(set(ids)):
        raise BacktestSourceIntegrityError(f"{name} contains duplicate condition ids")
    return ids


def _aware_utc(value: object, name: str) -> datetime:
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise BacktestSourceIntegrityError(f"{name} must be an ISO datetime") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise BacktestSourceIntegrityError(f"{name} must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_equal(name: str, stored: object, report: object) -> None:
    if stored != report:
        raise BacktestSourceIntegrityError(
            f"source report {name} does not match stored immutable column"
        )


def _offset(payload: Mapping[str, Any], horizon_seconds: int, name: str) -> int:
    if "selected_offset_seconds" not in payload:
        raise BacktestSourceIntegrityError(f"{name} selected_offset_seconds is missing")
    value = payload["selected_offset_seconds"]
    if isinstance(value, bool):
        raise BacktestSourceIntegrityError(f"{name} selected_offset_seconds must be positive")
    try:
        offset = int(value)
    except (TypeError, ValueError) as exc:
        raise BacktestSourceIntegrityError(
            f"{name} selected_offset_seconds must be positive"
        ) from exc
    if offset <= 0 or offset >= horizon_seconds:
        raise BacktestSourceIntegrityError(
            f"{name} selected_offset_seconds must be within the market horizon"
        )
    return offset


def _require_disjoint(name: str, *partitions: tuple[str, ...]) -> None:
    seen: set[str] = set()
    for partition in partitions:
        overlap = seen.intersection(partition)
        if overlap:
            raise BacktestSourceIntegrityError(
                f"{name} partitions overlap: {sorted(overlap)[0]}"
            )
        seen.update(partition)


def load_backtest_source_spec(connection: Connection, run_id: str) -> BacktestSourceSpec:
    stored = BacktestRunRepository().get(connection, run_id)
    if stored is None:
        raise BacktestSourceNotFound(f"source backtest run not found: {run_id}")

    report = _mapping("report", stored["report"])
    if stored["backtest_version"] != BACKTEST_VERSION:
        raise BacktestSourceIntegrityError(
            f"backtest_version must be {BACKTEST_VERSION!r}"
        )

    scalar_fields = (
        "run_id",
        "backtest_version",
        "source_training_run_id",
        "source_training_semantic_sha256",
        "dataset_version",
        "feature_version",
        "label_version",
        "horizon_seconds",
        "dataset_sha256",
        "config_sha256",
        "plan_sha256",
        "semantic_sha256",
    )
    for field in scalar_fields:
        if field not in report:
            raise BacktestSourceIntegrityError(f"source report {field} is missing")
        _require_equal(field, stored[field], report[field])

    horizon_seconds = int(stored["horizon_seconds"])
    if horizon_seconds <= 0:
        raise BacktestSourceIntegrityError("horizon_seconds must be positive")
    start = _aware_utc(stored["requested_start"], "requested_start")
    end = _aware_utc(stored["requested_end"], "requested_end")
    if end <= start:
        raise BacktestSourceIntegrityError("requested_end must be after requested_start")
    report_start = _aware_utc(report.get("start"), "report start")
    report_end = _aware_utc(report.get("end"), "report end")
    if report_start != start or report_end != end:
        raise BacktestSourceIntegrityError("source report window does not match stored window")

    semantic_sha256 = _sha256("semantic_sha256", stored["semantic_sha256"])
    source_training_semantic_sha256 = _sha256(
        "source_training_semantic_sha256", stored["source_training_semantic_sha256"]
    )
    dataset_sha256 = _sha256("dataset_sha256", stored["dataset_sha256"])
    config_sha256 = _sha256("config_sha256", stored["config_sha256"])
    plan_sha256 = _sha256("plan_sha256", stored["plan_sha256"])

    stored_membership = tuple(
        _sha256("fold_membership_sha256 entry", item)
        for item in _sequence(
            "fold_membership_sha256", stored["fold_membership_sha256"]
        )
    )
    report_membership = tuple(
        _sha256("report fold_membership_sha256 entry", item)
        for item in _sequence(
            "report fold_membership_sha256", report.get("fold_membership_sha256")
        )
    )
    if stored_membership != report_membership:
        raise BacktestSourceIntegrityError(
            "source report fold_membership_sha256 does not match stored immutable column"
        )

    fold_payloads = _sequence("report folds", report.get("folds"))
    folds: list[SourceFoldSpec] = []
    seen_test: set[str] = set()
    for position, item in enumerate(fold_payloads):
        payload = _mapping(f"fold[{position}]", item)
        index = int(payload.get("index", -1))
        if index < 0:
            raise BacktestSourceIntegrityError(f"fold[{position}] index must be non-negative")
        membership_sha256 = _sha256(
            f"fold[{position}] membership_sha256", payload.get("membership_sha256")
        )
        train_ids = _ids(
            f"fold[{position}] train_condition_ids", payload.get("train_condition_ids")
        )
        validation_ids = _ids(
            f"fold[{position}] validation_condition_ids",
            payload.get("validation_condition_ids"),
        )
        test_ids = _ids(
            f"fold[{position}] test_condition_ids", payload.get("test_condition_ids")
        )
        _require_disjoint(f"fold[{position}]", train_ids, validation_ids, test_ids)
        overlap = seen_test.intersection(test_ids)
        if overlap:
            raise BacktestSourceIntegrityError(
                f"ordinary test market reused: {sorted(overlap)[0]}"
            )
        seen_test.update(test_ids)
        folds.append(
            SourceFoldSpec(
                index=index,
                membership_sha256=membership_sha256,
                train_condition_ids=train_ids,
                validation_condition_ids=validation_ids,
                test_condition_ids=test_ids,
                selected_offset_seconds=_offset(
                    payload, horizon_seconds, f"fold[{position}]"
                ),
            )
        )

    final_payload = _mapping("final_holdout", report.get("final_holdout"))
    final_train = _ids(
        "final_holdout train_condition_ids", final_payload.get("train_condition_ids")
    )
    final_validation = _ids(
        "final_holdout validation_condition_ids",
        final_payload.get("validation_condition_ids"),
    )
    final_holdout = _ids(
        "final_holdout holdout_condition_ids", final_payload.get("holdout_condition_ids")
    )
    _require_disjoint("final_holdout", final_train, final_validation, final_holdout)
    holdout_overlap = seen_test.intersection(final_holdout)
    if holdout_overlap:
        raise BacktestSourceIntegrityError(
            f"final holdout overlaps ordinary test markets: {sorted(holdout_overlap)[0]}"
        )
    final = FinalSourceSpec(
        membership_sha256=_sha256(
            "final_holdout membership_sha256", final_payload.get("membership_sha256")
        ),
        train_condition_ids=final_train,
        validation_condition_ids=final_validation,
        holdout_condition_ids=final_holdout,
        selected_offset_seconds=_offset(final_payload, horizon_seconds, "final_holdout"),
    )

    expected_membership = tuple(
        [*(fold.membership_sha256 for fold in folds), final.membership_sha256]
    )
    if expected_membership != stored_membership:
        raise BacktestSourceIntegrityError(
            "fold membership hashes do not match restricted fold/final reports"
        )

    return BacktestSourceSpec(
        run_id=str(stored["run_id"]),
        backtest_version=str(stored["backtest_version"]),
        semantic_sha256=semantic_sha256,
        source_training_run_id=str(stored["source_training_run_id"]),
        source_training_semantic_sha256=source_training_semantic_sha256,
        dataset_version=str(stored["dataset_version"]),
        feature_version=str(stored["feature_version"]),
        label_version=str(stored["label_version"]),
        horizon_seconds=horizon_seconds,
        start=start,
        end=end,
        dataset_sha256=dataset_sha256,
        config_sha256=config_sha256,
        plan_sha256=plan_sha256,
        fold_membership_sha256=stored_membership,
        folds=tuple(folds),
        final=final,
    )
