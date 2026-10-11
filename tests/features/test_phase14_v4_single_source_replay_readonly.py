"""Offline-only contracts for single-feature V4 source-fingerprint replay."""
from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.features.v4_service import build_v4_feature
from bp_engine.storage import schema

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit_phase14_v4_single_source_replay_readonly.py"
spec = importlib.util.spec_from_file_location("v4_source_replay_audit", SCRIPT)
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

START = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
FEATURE_AT = START + timedelta(seconds=180)
TARGET = V4FeatureTarget(
    condition_id="source-replay-condition",
    slug="btc-updown-5m-source-replay",
    horizon_seconds=300,
    market_start_at=START,
    market_end_at=START + timedelta(seconds=300),
)


def _seed_state(connection, *, when: datetime, price: str) -> None:
    connection.execute(insert(schema.market_state_1s).values(
        bucket_at=when,
        state_key="coinbase/spot/BTC-USD",
        source="coinbase",
        stream="spot",
        instrument="BTC-USD",
        market_id=None,
        asset_id=None,
        last_event_at=when,
        state={"last_price": price},
    ))


def _create_feature_fixture():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        _seed_state(
            connection,
            when=FEATURE_AT - timedelta(seconds=1),
            price="100000.50",
        )
        record = build_v4_feature(
            connection, TARGET, FEATURE_AT,
            generated_at=FEATURE_AT + timedelta(minutes=7),
        )
        connection.execute(
            insert(schema.market_features).values(**record.__dict__)
        )
    return engine, record


def test_exact_replay_matches_original_fingerprint_and_feature_hash() -> None:
    engine, record = _create_feature_fixture()
    with engine.connect() as connection:
        stored = connection.execute(
            select(schema.market_features)
        ).mappings().one()
        assert audit.classify_replay(dict(stored), record) == (
            "PASS_EXACT_SOURCE_REPLAY", ()
        )
        verdict, details = audit.replay_one(connection, int(stored["id"]))

    assert verdict == "PASS_EXACT_SOURCE_REPLAY"
    assert details["changed_fields"] == ()
    engine.dispose()


def test_late_historical_source_change_is_inconclusive_not_corruption() -> None:
    engine, _ = _create_feature_fixture()
    with engine.begin() as connection:
        # A distinct compact second available only after original feature
        # generation changes the as-of source selection on replay.
        _seed_state(connection, when=FEATURE_AT, price="90000.00")
    with engine.connect() as connection:
        verdict, details = audit.replay_one(connection, None)
    assert verdict == "INCONCLUSIVE_SOURCE_VIEW_CHANGED"
    assert "source_cutoffs" in details["changed_fields"]
    engine.dispose()


def test_absent_old_feature_fails_closed() -> None:
    engine, _ = _create_feature_fixture()
    with engine.connect() as connection:
        verdict, details = audit.replay_one(connection, 999999)
    assert verdict == "INCONCLUSIVE_FEATURE_NOT_FOUND"
    assert details == {}
    engine.dispose()


def test_stored_feature_hash_tamper_is_hard_failure() -> None:
    engine, original = _create_feature_fixture()
    with engine.connect() as connection:
        stored = dict(connection.execute(
            select(schema.market_features)
        ).mappings().one())
    stored["feature_hash"] = "0" * 64
    verdict, differences = audit.classify_replay(stored, original)
    assert verdict == "FAIL_STORED_FEATURE_HASH"
    assert differences == ("stored_feature_hash",)
    engine.dispose()


def test_changed_source_descriptor_is_not_automatically_corruption() -> None:
    engine, original = _create_feature_fixture()
    with engine.connect() as connection:
        stored = dict(connection.execute(
            select(schema.market_features)
        ).mappings().one())
    replayed = replace(original, input_fingerprint="f" * 64)
    verdict, differences = audit.classify_replay(stored, replayed)
    assert verdict == "INCONCLUSIVE_SOURCE_DESCRIPTOR_CHANGED"
    assert differences == ("input_fingerprint",)
    engine.dispose()


def test_static_read_only_and_query_budget_contract() -> None:
    source = SCRIPT.read_text()
    for required in (
        "default_transaction_read_only=on",
        'isolation_level="AUTOCOMMIT"',
        "STATEMENT_TIMEOUT_MS = 2000",
        "LOCK_TIMEOUT_MS = 500",
        "FEATURE_LOOKUPS_MAX = 1",
        "PASS_EXACT_SOURCE_REPLAY",
        "INCONCLUSIVE_SOURCE_VIEW_CHANGED",
        "INCONCLUSIVE_SOURCE_DESCRIPTOR_CHANGED",
        "PRODUCTION_MUTATION=false",
    ):
        assert required in source
    for forbidden in (
        "engine.begin(", ".store(", "generate_v4_features(",
        "DROP TABLE ", "ALTER TABLE ", "CREATE INDEX ",
        "systemctl restart", "systemctl start", "systemctl stop",
    ):
        assert forbidden not in source
