from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.storage import schema
from bp_engine.v2_research.config import (
    EXPECTED_OFFSETS_SECONDS,
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    V2_FEATURE_VERSION,
    V2_GATE_B_VERSION,
    V2_LABEL_VERSION,
)

BASE = datetime(2026, 9, 9, 10, 20, tzinfo=UTC)
IDS = {
    "train": "train-condition",
    "validation": "validation-condition",
    "test": "missing-condition",
    "final_train": "final-train-condition",
    "final_validation": "final-validation-condition",
    "holdout": "holdout-condition",
}
STARTS = {
    key: BASE + timedelta(minutes=index * 10)
    for index, key in enumerate(IDS)
}


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    return engine


def _partition(name: str, condition_id: str) -> dict[str, Any]:
    start = STARTS[name]
    return {
        "name": name,
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=5)).isoformat(),
        "condition_ids": [condition_id],
    }


def _plan() -> dict[str, Any]:
    research_config = {
        "fee_rate": 0.07,
        "slippage_buffer": 0.01,
        "min_edge_grid": [0.0, 0.01],
        "min_validation_trades": 1,
        "min_train_eligible_markets": 1,
        "min_validation_eligible_markets": 1,
    }
    plan: dict[str, Any] = {
        "gate_b_version": V2_GATE_B_VERSION,
        "feature_version": V2_FEATURE_VERSION,
        "market_start_at": BASE.isoformat(),
        "market_end_at": (BASE + timedelta(hours=2)).isoformat(),
        "coverage_input_sha256": FROZEN_COVERAGE_INPUT_SHA256,
        "freshness_candidates_seconds": list(FROZEN_FRESHNESS_CANDIDATES_SECONDS),
        "include_no_trade": True,
        "labels_read": False,
        "research_config": research_config,
        "research_config_sha256": canonical_hash(research_config),
        "folds": [
            {
                "train": _partition("train", IDS["train"]),
                "validation": _partition("validation", IDS["validation"]),
                "test": _partition("test", IDS["test"]),
            }
        ],
        "final": {
            "train_condition_ids": [IDS["final_train"]],
            "validation_condition_ids": [IDS["final_validation"]],
            "holdout_condition_ids": [IDS["holdout"]],
        },
    }
    plan["plan_sha256"] = canonical_hash(plan)
    return plan


def _slug(start: datetime) -> str:
    return f"btc-updown-5m-{int(start.timestamp())}"


def _insert_features(connection, key: str) -> None:
    condition_id = IDS[key]
    start = STARTS[key]
    for offset in EXPECTED_OFFSETS_SECONDS:
        connection.execute(
            insert(schema.market_features).values(
                condition_id=condition_id,
                slug=_slug(start),
                horizon_seconds=300,
                market_start_at=start,
                market_end_at=start + timedelta(minutes=5),
                feature_at=start + timedelta(seconds=offset),
                feature_offset_seconds=offset,
                feature_version=V2_FEATURE_VERSION,
                features={},
                missing_flags={},
                source_cutoffs={},
                input_fingerprint=f"{key}-{offset}".ljust(64, "0")[:64],
                feature_hash=f"hash-{key}-{offset}".ljust(64, "0")[:64],
                generated_at=start + timedelta(minutes=6),
            )
        )


def _insert_label(connection, key: str) -> None:
    condition_id = IDS[key]
    start = STARTS[key]
    connection.execute(
        insert(schema.market_labels).values(
            condition_id=condition_id,
            gamma_market_id=f"gamma-{condition_id}",
            slug=_slug(start),
            horizon_seconds=300,
            market_start_at=start,
            market_end_at=start + timedelta(minutes=5),
            official_outcome="Up",
            start_reference=None,
            end_reference=None,
            resolution_source="https://data.chain.link/streams/btc-usd",
            rules_hash="sha256:" + "a" * 64,
            label_source="polymarket_gamma_snapshot",
            label_version=V2_LABEL_VERSION,
            source_snapshot_sha256="b" * 64,
            source_observed_at=start + timedelta(minutes=6),
            generated_at=start + timedelta(minutes=6),
        )
    )


def _resolved_payload(key: str) -> dict[str, object]:
    condition_id = IDS[key]
    start = STARTS[key]
    return {
        "id": f"gamma-{condition_id}",
        "conditionId": condition_id,
        "slug": _slug(start),
        "question": "Bitcoin Up or Down?",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": f'["up-{condition_id}", "down-{condition_id}"]',
        "outcomePrices": '["1", "0"]',
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "description": "Resolves Up when the stated BTC TWAP is at least the opening value.",
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "events": [{"id": f"event-{condition_id}"}],
    }


class FakeGammaClient:
    def __init__(self, payloads: dict[str, dict[str, object] | None]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def get_market_by_slug(self, slug: str) -> dict[str, object] | None:
        self.calls.append(slug)
        return self.payloads.get(slug)


def _seed_all_features(engine) -> None:
    with engine.begin() as connection:
        for key in IDS:
            _insert_features(connection, key)


def test_audit_reports_only_frozen_non_holdout_label_gaps() -> None:
    from bp_engine.v2_research.label_recovery import audit_gate_b_non_holdout_labels

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "final_train", "final_validation"):
            _insert_label(connection, key)
        # A holdout label can exist in the database for unrelated reasons; the audit
        # must not include or use it in its target population.
        _insert_label(connection, "holdout")

        report = audit_gate_b_non_holdout_labels(connection, plan=_plan())

    assert report["holdout_touched"] is False
    assert report["non_holdout_condition_count"] == 5
    assert report["label_present_count"] == 4
    assert report["missing_label_count"] == 1
    assert report["missing_condition_ids"] == [IDS["test"]]
    assert IDS["holdout"] not in {
        item["condition_id"] for item in report["conditions"]
    }


def test_audit_rejects_incomplete_frozen_v2_feature_identity() -> None:
    from bp_engine.v2_research.label_recovery import (
        GateBLabelRecoveryIntegrityError,
        audit_gate_b_non_holdout_labels,
    )

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        connection.execute(
            schema.market_features.delete().where(
                schema.market_features.c.condition_id == IDS["test"],
                schema.market_features.c.feature_offset_seconds == 240,
            )
        )
        with pytest.raises(GateBLabelRecoveryIntegrityError, match="four V2 offsets"):
            audit_gate_b_non_holdout_labels(connection, plan=_plan())


@pytest.mark.asyncio
async def test_recovery_fetches_only_missing_non_holdout_and_creates_canonical_label() -> None:
    from bp_engine.v2_research.label_recovery import recover_gate_b_non_holdout_labels

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "final_train", "final_validation"):
            _insert_label(connection, key)

    missing_slug = _slug(STARTS["test"])
    client = FakeGammaClient({missing_slug: _resolved_payload("test")})
    observed_at = STARTS["holdout"] + timedelta(hours=1)

    report = await recover_gate_b_non_holdout_labels(
        engine,
        client,
        plan=_plan(),
        observed_at=observed_at,
    )

    with engine.begin() as connection:
        labels = connection.execute(
            select(schema.market_labels.c.condition_id).where(
                schema.market_labels.c.label_version == V2_LABEL_VERSION
            )
        ).scalars().all()
        snapshots = connection.execute(
            select(schema.polymarket_market_snapshots.c.condition_id)
        ).scalars().all()

    assert client.calls == [missing_slug]
    assert report["holdout_touched"] is False
    assert report["missing_before"] == [IDS["test"]]
    assert report["missing_after"] == []
    assert report["created_snapshots"] == 1
    assert report["created_labels"] == 1
    assert IDS["test"] in labels
    assert IDS["holdout"] not in snapshots


@pytest.mark.asyncio
async def test_recovery_skips_existing_labels_without_gamma_requests() -> None:
    from bp_engine.v2_research.label_recovery import recover_gate_b_non_holdout_labels

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "test", "final_train", "final_validation"):
            _insert_label(connection, key)

    client = FakeGammaClient({_slug(STARTS["holdout"]): _resolved_payload("holdout")})
    report = await recover_gate_b_non_holdout_labels(
        engine,
        client,
        plan=_plan(),
        observed_at=STARTS["holdout"] + timedelta(hours=1),
    )

    assert client.calls == []
    assert report["missing_before"] == []
    assert report["missing_after"] == []
    assert report["holdout_touched"] is False


@pytest.mark.asyncio
async def test_unresolved_non_holdout_remains_pending_without_label() -> None:
    from bp_engine.v2_research.label_recovery import recover_gate_b_non_holdout_labels

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "final_train", "final_validation"):
            _insert_label(connection, key)

    payload = _resolved_payload("test")
    payload["closed"] = False
    payload["active"] = True
    payload["outcomePrices"] = '["0.5", "0.5"]'
    missing_slug = _slug(STARTS["test"])
    client = FakeGammaClient({missing_slug: payload})

    report = await recover_gate_b_non_holdout_labels(
        engine,
        client,
        plan=_plan(),
        observed_at=STARTS["holdout"] + timedelta(hours=1),
    )

    assert client.calls == [missing_slug]
    assert report["pending_condition_ids"] == [IDS["test"]]
    assert report["missing_after"] == [IDS["test"]]
    assert report["created_snapshots"] == 0
    assert report["created_labels"] == 0
    assert report["holdout_touched"] is False


@pytest.mark.asyncio
async def test_recovery_identity_mismatch_fails_closed_before_write() -> None:
    from bp_engine.v2_research.label_recovery import (
        GateBLabelRecoveryIntegrityError,
        recover_gate_b_non_holdout_labels,
    )

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "final_train", "final_validation"):
            _insert_label(connection, key)

    payload = _resolved_payload("test")
    payload["conditionId"] = "wrong-condition"
    missing_slug = _slug(STARTS["test"])
    client = FakeGammaClient({missing_slug: payload})

    with pytest.raises(GateBLabelRecoveryIntegrityError, match="identity"):
        await recover_gate_b_non_holdout_labels(
            engine,
            client,
            plan=_plan(),
            observed_at=STARTS["holdout"] + timedelta(hours=1),
        )

    with engine.begin() as connection:
        assert connection.execute(
            select(schema.polymarket_market_snapshots.c.id)
        ).first() is None
        assert connection.execute(
            select(schema.market_labels.c.condition_id).where(
                schema.market_labels.c.condition_id == IDS["test"]
            )
        ).first() is None


@pytest.mark.asyncio
async def test_recovery_rerun_is_idempotent() -> None:
    from bp_engine.v2_research.label_recovery import recover_gate_b_non_holdout_labels

    engine = _engine()
    _seed_all_features(engine)
    with engine.begin() as connection:
        for key in ("train", "validation", "final_train", "final_validation"):
            _insert_label(connection, key)

    missing_slug = _slug(STARTS["test"])
    client = FakeGammaClient({missing_slug: _resolved_payload("test")})
    observed_at = STARTS["holdout"] + timedelta(hours=1)

    first = await recover_gate_b_non_holdout_labels(
        engine,
        client,
        plan=_plan(),
        observed_at=observed_at,
    )
    second = await recover_gate_b_non_holdout_labels(
        engine,
        client,
        plan=_plan(),
        observed_at=observed_at + timedelta(minutes=1),
    )

    assert first["created_labels"] == 1
    assert second["created_labels"] == 0
    assert client.calls == [missing_slug]
    assert second["missing_before"] == []
    assert second["missing_after"] == []
    assert second["holdout_touched"] is False
