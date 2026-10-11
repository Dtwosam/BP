"""Offline regression and safety tests for fixed three-cohort V4 source replay."""
from __future__ import annotations

import importlib.util
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.features.v4_service import build_v4_feature
from bp_engine.storage import schema

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit_phase14_v4_three_cohort_source_replay_readonly.py"
spec = importlib.util.spec_from_file_location("v4_three_cohort_audit", SCRIPT)
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _seed(engine, *, omit: str | None = None) -> None:
    with engine.begin() as connection:
        for name, beginning in audit.COHORTS:
            if name == omit:
                continue
            start = beginning + timedelta(minutes=10)
            at = start + timedelta(seconds=60)
            connection.execute(insert(schema.market_state_1s).values(
                bucket_at=at - timedelta(seconds=1),
                state_key="coinbase/spot/BTC-USD",
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                market_id=None,
                asset_id=None,
                last_event_at=at - timedelta(seconds=1),
                state={"last_price": "101.25"},
            ))
            target = V4FeatureTarget(
                condition_id="v4-three-cohort-" + name,
                slug="btc-updown-5m-three-cohort-" + name,
                horizon_seconds=300,
                market_start_at=start,
                market_end_at=start + timedelta(seconds=300),
            )
            feature = build_v4_feature(
                connection, target, at,
                generated_at=at + timedelta(minutes=6),
            )
            connection.execute(
                insert(schema.market_features).values(**feature.__dict__)
            )


def _engine(*, omit: str | None = None):
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    _seed(engine, omit=omit)
    return engine


def test_fixed_windows_are_nonoverlapping_and_forward_only() -> None:
    assert len(audit.COHORTS) == audit.MAX_REPLAYS == 3
    assert tuple(name for name, _ in audit.COHORTS) == ("early", "middle", "recent")
    for (_, beginning), (_, next_beginning) in zip(
        audit.COHORTS, audit.COHORTS[1:], strict=False
    ):
        assert beginning >= audit.EPOCH
        assert beginning + audit.WINDOW <= next_beginning


def test_three_exact_replays_from_preselected_cohorts(capsys) -> None:
    engine = _engine()
    with engine.connect() as connection:
        selected = audit.select_cohort_rows(connection)
        assert [name for name, _ in selected] == ["early", "middle", "recent"]
        assert all(row is not None for _, row in selected)
        result = audit.audit(connection, time.monotonic() + 30)
    output = capsys.readouterr().out
    assert result == "PASS_THREE_PREREGISTERED_EXACT_REPLAYS"
    assert "COHORT_EARLY_REPLAY=PASS_EXACT_SOURCE_REPLAY" in output
    assert "COHORT_MIDDLE_REPLAY=PASS_EXACT_SOURCE_REPLAY" in output
    assert "COHORT_RECENT_REPLAY=PASS_EXACT_SOURCE_REPLAY" in output
    engine.dispose()


def test_missing_cohort_stops_without_replaying(capsys, monkeypatch) -> None:
    engine = _engine(omit="middle")
    invoked = []
    monkeypatch.setattr(
        audit, "replay_selected", lambda *_: invoked.append(True)
    )
    with engine.connect() as connection:
        result = audit.audit(connection, time.monotonic() + 30)
    assert result == "INCONCLUSIVE_MISSING_COHORT"
    assert "COHORT_MIDDLE_SELECTION=MISSING" in capsys.readouterr().out
    assert not invoked
    engine.dispose()


def test_changed_historical_state_fails_closed_before_later_replay(capsys) -> None:
    engine = _engine()
    middle = audit.COHORTS[1][1]
    at = middle + timedelta(minutes=11)
    with engine.begin() as connection:
        connection.execute(insert(schema.market_state_1s).values(
            bucket_at=at,
            state_key="coinbase/spot/BTC-USD",
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            market_id=None,
            asset_id=None,
            last_event_at=at,
            state={"last_price": "888.00"},
        ))
    with engine.connect() as connection:
        result = audit.audit(connection, time.monotonic() + 30)
    output = capsys.readouterr().out
    assert result == "STOPPED_ON_FIRST_DISCREPANCY"
    assert "COHORT_EARLY_REPLAY=PASS_EXACT_SOURCE_REPLAY" in output
    assert "COHORT_MIDDLE_REPLAY=INCONCLUSIVE_SOURCE_VIEW_CHANGED" in output
    assert "COHORT_RECENT_REPLAY=" not in output
    engine.dispose()


def test_budget_prevents_database_reads(monkeypatch) -> None:
    monkeypatch.setattr(
        audit, "select_cohort_rows",
        lambda *_: (_ for _ in ()).throw(AssertionError("queried")),
    )
    assert audit.audit(object(), time.monotonic() - 1) == "INCOMPLETE_WALL_BUDGET"


def test_source_enforces_read_only_and_fixed_limits() -> None:
    source = SCRIPT.read_text()
    for token in (
        "default_transaction_read_only=on",
        'isolation_level="AUTOCOMMIT"',
        "STATEMENT_TIMEOUT_MS = 2000",
        "LOCK_TIMEOUT_MS = 500",
        "MAX_WALL_SECONDS = 45.0",
        "MAX_REPLAYS = 3",
        ".limit(1)",
        "BETWEEN_REPLAYS_SECONDS = 0.25",
        "STOPPED_ON_FIRST_DISCREPANCY",
        "PRODUCTION_MUTATION=false",
    ):
        assert token in source
    for forbidden in (
        ".store(", "engine.begin(", "generate_v4_features(",
        "UPDATE market_features", "INSERT INTO market_features",
        "DELETE FROM market_features", "ALTER TABLE", "DROP TABLE",
        "systemctl start", "systemctl stop", "systemctl restart",
    ):
        assert forbidden not in source
