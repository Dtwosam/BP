from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import Connection, Engine, select

from bp_engine.backfill.provenance import canonical_json_sha256
from bp_engine.labels.service import generate_labels
from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.historical import HistoricalRepository, PolymarketMarketSnapshot
from bp_engine.storage.schema import market_features, market_labels
from bp_engine.v2_research.config import (
    EXPECTED_OFFSETS_SECONDS,
    V2_FEATURE_VERSION,
    V2_LABEL_VERSION,
)
from bp_engine.v2_research.service import _non_holdout_ids, _verify_plan


class GammaMarketClient(Protocol):
    async def get_market_by_slug(self, slug: str) -> Mapping[str, Any] | None: ...


class GateBLabelRecoveryIntegrityError(RuntimeError):
    """Raised when frozen Gate B non-holdout label recovery cannot stay leakage-safe."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GateBLabelRecoveryIntegrityError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _condition_rows(
    connection: Connection,
    *,
    condition_ids: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    rows = connection.execute(
        select(
            market_features.c.condition_id,
            market_features.c.slug,
            market_features.c.horizon_seconds,
            market_features.c.market_start_at,
            market_features.c.market_end_at,
            market_features.c.feature_offset_seconds,
        )
        .where(
            market_features.c.feature_version == V2_FEATURE_VERSION,
            market_features.c.condition_id.in_(condition_ids),
        )
        .order_by(
            market_features.c.condition_id,
            market_features.c.feature_offset_seconds,
        )
    ).mappings()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["condition_id"])].append(dict(row))
    return grouped


def _static_feature_identity(
    condition_id: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    offsets = tuple(int(row["feature_offset_seconds"]) for row in rows)
    if offsets != EXPECTED_OFFSETS_SECONDS:
        raise GateBLabelRecoveryIntegrityError(
            f"{condition_id} must have the frozen four V2 offsets {EXPECTED_OFFSETS_SECONDS}"
        )

    first = rows[0]
    identity = {
        "condition_id": condition_id,
        "slug": str(first["slug"]),
        "horizon_seconds": int(first["horizon_seconds"]),
        "market_start_at": _utc(first["market_start_at"]),
        "market_end_at": _utc(first["market_end_at"]),
    }
    if identity["horizon_seconds"] != 300:
        raise GateBLabelRecoveryIntegrityError(
            f"unexpected horizon for {condition_id}: {identity['horizon_seconds']}"
        )
    if identity["market_end_at"] <= identity["market_start_at"]:
        raise GateBLabelRecoveryIntegrityError(
            f"invalid market window for {condition_id}"
        )

    expected = (
        identity["slug"],
        identity["horizon_seconds"],
        identity["market_start_at"],
        identity["market_end_at"],
    )
    for row in rows[1:]:
        observed = (
            str(row["slug"]),
            int(row["horizon_seconds"]),
            _utc(row["market_start_at"]),
            _utc(row["market_end_at"]),
        )
        if observed != expected:
            raise GateBLabelRecoveryIntegrityError(
                f"static V2 feature identity mismatch for {condition_id}"
            )
    return identity


def audit_gate_b_non_holdout_labels(
    connection: Connection,
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Audit canonical labels for the already-frozen Gate B non-holdout population."""
    _verify_plan(plan)
    condition_ids = _non_holdout_ids(plan)
    if not condition_ids:
        raise GateBLabelRecoveryIntegrityError("frozen Gate B non-holdout is empty")

    grouped = _condition_rows(connection, condition_ids=condition_ids)
    conditions: list[dict[str, Any]] = []
    for condition_id in condition_ids:
        identity = _static_feature_identity(condition_id, grouped.get(condition_id, []))
        conditions.append(
            {
                "condition_id": identity["condition_id"],
                "slug": identity["slug"],
                "horizon_seconds": identity["horizon_seconds"],
                "market_start_at": identity["market_start_at"].isoformat(),
                "market_end_at": identity["market_end_at"].isoformat(),
            }
        )

    present_ids = set(
        str(value)
        for value in connection.execute(
            select(market_labels.c.condition_id).where(
                market_labels.c.condition_id.in_(condition_ids),
                market_labels.c.label_version == V2_LABEL_VERSION,
            )
        ).scalars()
    )
    missing = [condition_id for condition_id in condition_ids if condition_id not in present_ids]

    return {
        "holdout_touched": False,
        "non_holdout_condition_count": len(condition_ids),
        "label_present_count": len(present_ids),
        "missing_label_count": len(missing),
        "missing_condition_ids": missing,
        "conditions": conditions,
    }


def _identity_from_audit(report: dict[str, Any], condition_id: str) -> dict[str, Any]:
    for item in report["conditions"]:
        if item["condition_id"] == condition_id:
            return item
    raise GateBLabelRecoveryIntegrityError(
        f"missing audited identity for condition={condition_id}"
    )


def _validate_gamma_identity(expected: dict[str, Any], payload: Mapping[str, Any]) -> Any:
    market = parse_gamma_market(payload)
    actual = (
        market.condition_id,
        market.slug,
        market.horizon_seconds,
        market.window_start_at,
        market.window_end_at,
    )
    required = (
        expected["condition_id"],
        expected["slug"],
        int(expected["horizon_seconds"]),
        datetime.fromisoformat(expected["market_start_at"]),
        datetime.fromisoformat(expected["market_end_at"]),
    )
    if actual != required:
        raise GateBLabelRecoveryIntegrityError(
            f"official Gamma identity mismatch for condition={expected['condition_id']}"
        )
    return market


async def recover_gate_b_non_holdout_labels(
    engine: Engine,
    client: GammaMarketClient,
    *,
    plan: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    """Append missing canonical labels for frozen non-holdout IDs only."""
    observed_at = _utc(observed_at)
    repository = HistoricalRepository()

    with engine.begin() as connection:
        before = audit_gate_b_non_holdout_labels(connection, plan=plan)

    missing_before = list(before["missing_condition_ids"])
    pending: list[str] = []
    created_snapshots = 0
    existing_snapshots = 0
    created_labels = 0
    existing_labels = 0

    for condition_id in missing_before:
        identity = _identity_from_audit(before, condition_id)
        payload = await client.get_market_by_slug(identity["slug"])
        if payload is None:
            pending.append(condition_id)
            continue

        market = _validate_gamma_identity(identity, payload)
        if not market.closed or market.resolved_outcome is None:
            pending.append(condition_id)
            continue

        payload_dict = dict(payload)
        snapshot = PolymarketMarketSnapshot(
            condition_id=market.condition_id,
            gamma_market_id=market.gamma_market_id,
            slug=market.slug,
            downloaded_at=observed_at,
            payload_sha256=canonical_json_sha256(payload_dict),
            payload=payload_dict,
        )
        with engine.begin() as connection:
            snapshot_result = repository.store_polymarket_market_snapshot(
                connection,
                snapshot,
            )
            label_stats = generate_labels(
                connection,
                start=market.window_start_at,
                end=market.window_start_at + timedelta(microseconds=1),
                generated_at=observed_at,
                condition_ids=(condition_id,),
            )

        created_snapshots += int(snapshot_result.created)
        existing_snapshots += int(not snapshot_result.created)
        created_labels += label_stats.inserted
        existing_labels += label_stats.existing

    with engine.begin() as connection:
        after = audit_gate_b_non_holdout_labels(connection, plan=plan)

    return {
        "holdout_touched": False,
        "missing_before": missing_before,
        "missing_after": list(after["missing_condition_ids"]),
        "pending_condition_ids": pending,
        "created_snapshots": created_snapshots,
        "existing_snapshots": existing_snapshots,
        "created_labels": created_labels,
        "existing_labels": existing_labels,
        "non_holdout_condition_count": after["non_holdout_condition_count"],
        "label_present_count": after["label_present_count"],
    }
