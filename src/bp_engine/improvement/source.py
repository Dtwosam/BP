from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy.engine import Connection

from bp_engine.backtesting.repository import BacktestRunRepository
from bp_engine.calibration.repository import CalibrationEdgeRunRepository
from bp_engine.improvement.models import ChampionRef
from bp_engine.modeling.repository import ModelTrainingRunRepository


class ChampionIntegrityError(ValueError):
    """Raised when the accepted champion provenance chain is inconsistent."""


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ChampionIntegrityError(f"{field_name} must be a 64-character SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ChampionIntegrityError(f"{field_name} must be a SHA-256 hex digest") from exc
    return value.lower()


def _validated_report(
    row: Mapping[str, Any],
    *,
    label: str,
    immutable_fields: tuple[str, ...],
) -> dict[str, Any]:
    report = row.get("report")
    if not isinstance(report, Mapping):
        raise ChampionIntegrityError(f"{label} report must be a mapping")

    for field_name in immutable_fields:
        if field_name not in report:
            raise ChampionIntegrityError(
                f"{label} report is missing immutable column {field_name}"
            )
        if report[field_name] != row.get(field_name):
            raise ChampionIntegrityError(
                f"{label} report {field_name} disagrees with immutable column"
            )

    _require_sha256(row.get("semantic_sha256"), field_name=f"{label}.semantic_sha256")
    return dict(report)


def _load_phase9_row(connection: Connection, calibration_run_id: str) -> dict[str, Any]:
    row = CalibrationEdgeRunRepository().get(connection, calibration_run_id)
    if row is None:
        raise ChampionIntegrityError(f"Phase 9 calibration run {calibration_run_id} was not found")
    _validated_report(
        row,
        label="Phase 9",
        immutable_fields=(
            "run_id",
            "source_backtest_run_id",
            "source_backtest_semantic_sha256",
            "source_training_run_id",
            "source_training_semantic_sha256",
            "horizon_seconds",
            "semantic_sha256",
        ),
    )
    return row


def _load_phase8_row(connection: Connection, backtest_run_id: str) -> dict[str, Any]:
    row = BacktestRunRepository().get(connection, backtest_run_id)
    if row is None:
        raise ChampionIntegrityError(f"Phase 8 backtest run {backtest_run_id} was not found")
    _validated_report(
        row,
        label="Phase 8",
        immutable_fields=(
            "run_id",
            "source_training_run_id",
            "source_training_semantic_sha256",
            "horizon_seconds",
            "semantic_sha256",
        ),
    )
    return row


def _load_phase7_row(connection: Connection, training_run_id: str) -> dict[str, Any]:
    row = ModelTrainingRunRepository().get(connection, training_run_id)
    if row is None:
        raise ChampionIntegrityError(f"Phase 7 training run {training_run_id} was not found")
    _validated_report(
        row,
        label="Phase 7",
        immutable_fields=(
            "run_id",
            "horizon_seconds",
            "semantic_sha256",
        ),
    )
    return row


def _require_equal(
    left: Any,
    right: Any,
    *,
    description: str,
) -> None:
    if left != right:
        raise ChampionIntegrityError(f"champion provenance mismatch: {description}")


def load_phase9_report(
    connection: Connection,
    calibration_run_id: str,
) -> Mapping[str, Any]:
    """Load and validate the immutable Phase 9 report without mutating its ledger."""

    row = _load_phase9_row(connection, calibration_run_id)
    return _validated_report(
        row,
        label="Phase 9",
        immutable_fields=(
            "run_id",
            "source_backtest_run_id",
            "source_backtest_semantic_sha256",
            "source_training_run_id",
            "source_training_semantic_sha256",
            "horizon_seconds",
            "semantic_sha256",
        ),
    )


def load_champion_ref(
    connection: Connection,
    calibration_run_id: str,
) -> ChampionRef:
    """Reconstruct and validate the exact Phase 9 -> Phase 8 -> Phase 7 hash chain."""

    phase9 = _load_phase9_row(connection, calibration_run_id)
    phase8 = _load_phase8_row(connection, str(phase9["source_backtest_run_id"]))

    phase9_backtest_sha = _require_sha256(
        phase9["source_backtest_semantic_sha256"],
        field_name="Phase 9.source_backtest_semantic_sha256",
    )
    phase8_sha = _require_sha256(
        phase8["semantic_sha256"],
        field_name="Phase 8.semantic_sha256",
    )
    _require_equal(
        phase9_backtest_sha,
        phase8_sha,
        description="Phase 9 source backtest hash does not match Phase 8",
    )
    _require_equal(
        phase9["source_backtest_run_id"],
        phase8["run_id"],
        description="Phase 9 source backtest id does not match Phase 8",
    )
    _require_equal(
        phase9["source_training_run_id"],
        phase8["source_training_run_id"],
        description="Phase 9 and Phase 8 source training ids differ",
    )

    phase9_training_sha = _require_sha256(
        phase9["source_training_semantic_sha256"],
        field_name="Phase 9.source_training_semantic_sha256",
    )
    phase8_training_sha = _require_sha256(
        phase8["source_training_semantic_sha256"],
        field_name="Phase 8.source_training_semantic_sha256",
    )
    _require_equal(
        phase9_training_sha,
        phase8_training_sha,
        description="Phase 9 and Phase 8 source training hashes differ",
    )

    phase7 = _load_phase7_row(connection, str(phase8["source_training_run_id"]))
    phase7_sha = _require_sha256(
        phase7["semantic_sha256"],
        field_name="Phase 7.semantic_sha256",
    )
    _require_equal(
        phase8_training_sha,
        phase7_sha,
        description="Phase 8 source training hash does not match Phase 7",
    )
    _require_equal(
        phase8["source_training_run_id"],
        phase7["run_id"],
        description="Phase 8 source training id does not match Phase 7",
    )

    for field_name in (
        "dataset_version",
        "feature_version",
        "label_version",
        "horizon_seconds",
        "dataset_sha256",
    ):
        _require_equal(
            phase9[field_name],
            phase8[field_name],
            description=f"Phase 9 and Phase 8 {field_name} differ",
        )
        _require_equal(
            phase8[field_name],
            phase7[field_name],
            description=f"Phase 8 and Phase 7 {field_name} differ",
        )

    _require_equal(
        phase9["source_plan_sha256"],
        phase8["plan_sha256"],
        description="Phase 9 source plan hash does not match Phase 8",
    )
    _require_equal(
        phase9["source_fold_membership_sha256"],
        phase8["fold_membership_sha256"],
        description="Phase 9 source fold membership does not match Phase 8",
    )

    calibration_sha = _require_sha256(
        phase9["semantic_sha256"],
        field_name="Phase 9.semantic_sha256",
    )
    return ChampionRef(
        calibration_run_id=str(phase9["run_id"]),
        calibration_semantic_sha256=calibration_sha,
        backtest_run_id=str(phase8["run_id"]),
        backtest_semantic_sha256=phase8_sha,
        training_run_id=str(phase7["run_id"]),
        training_semantic_sha256=phase7_sha,
    )
