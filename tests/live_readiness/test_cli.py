from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bp_engine.live_readiness.cli import build_parser, main
from sqlalchemy import create_engine, delete

from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

DATABASE_URL = os.environ.get("BP_TEST_DATABASE_URL")
BASE = datetime(2026, 8, 29, 17, 0, tzinfo=UTC)
GIT_SHA = "a" * 64


def _cleanup() -> None:
    if DATABASE_URL is None:
        return
    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(delete(schema.live_reconciliation_runs))
        connection.execute(delete(schema.live_readiness_checks))
    engine.dispose()


def test_parser_exposes_only_read_only_phase14_commands() -> None:
    help_text = build_parser().format_help().lower()
    for command in (
        "report",
        "validate-activation-manifest",
        "geoblock",
        "reconcile-fixture",
    ):
        assert command in help_text

    for forbidden in (
        "submit",
        "place-order",
        "cancel-live",
        "enable-live",
        "buy",
        "sell",
    ):
        assert forbidden not in help_text


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL CLI coverage",
)
def test_report_reads_latest_readiness_evidence_from_database(capsys) -> None:
    assert DATABASE_URL is not None
    _cleanup()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        repository.store_readiness_check(
            connection,
            candidate_git_sha=GIT_SHA,
            observed_at=BASE,
            eligible=False,
            reasons=("live_trading_disabled",),
            evidence={
                "mode": "research",
                "live_trading_enabled": False,
                "activation": {"authorized": False},
                "kill_switch": {"engaged": True},
                "geoblock": {
                    "blocked": None,
                    "country": None,
                    "region": None,
                },
                "sdk_healthy": True,
                "wallet_configured": False,
                "reconciliation": {
                    "status": "unavailable",
                    "critical_count": None,
                },
            },
        )
    engine.dispose()

    try:
        exit_code = main(["report", "--database-url", DATABASE_URL])
        payload = json.loads(capsys.readouterr().out)
    finally:
        _cleanup()

    assert exit_code == 0
    assert payload == {
        "activation_authorized": False,
        "candidate_git_sha": GIT_SHA,
        "country": None,
        "critical_discrepancy_count": None,
        "eligible": False,
        "geoblock_blocked": None,
        "kill_switch_engaged": True,
        "live_trading_enabled": False,
        "mode": "research",
        "observed_at": BASE.isoformat(),
        "reasons": ["live_trading_disabled"],
        "reconciliation_status": "unavailable",
        "region": None,
        "sdk_healthy": True,
        "wallet_configured": False,
    }


def test_malformed_activation_manifest_returns_generic_failure(tmp_path, capsys) -> None:
    manifest = tmp_path / "activation.json"
    manifest.write_text("{not-json", encoding="utf-8")

    exit_code = main(
        [
            "validate-activation-manifest",
            "--path",
            str(manifest),
            "--expected-head",
            GIT_SHA,
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "activation_manifest_invalid",
        "ok": False,
    }
    assert "not-json" not in captured.err


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL CLI coverage",
)
def test_reconcile_fixture_is_deterministic_and_non_network(tmp_path, capsys) -> None:
    assert DATABASE_URL is not None
    _cleanup()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    engine.dispose()
    fixture = tmp_path / "orders.json"
    fixture.write_text(
        json.dumps(
            {
                "observed_at": BASE.isoformat(),
                "official_orders": [],
            }
        ),
        encoding="utf-8",
    )

    try:
        first_code = main(
            [
                "reconcile-fixture",
                "--fixture",
                str(fixture),
                "--database-url",
                DATABASE_URL,
            ]
        )
        first = capsys.readouterr().out
        second_code = main(
            [
                "reconcile-fixture",
                "--fixture",
                str(fixture),
                "--database-url",
                DATABASE_URL,
            ]
        )
        second = capsys.readouterr().out
    finally:
        _cleanup()

    assert first_code == 0
    assert second_code == 0
    assert first == second
    assert json.loads(first) == {
        "critical_discrepancy_count": 0,
        "issues": [],
        "observed_at": BASE.isoformat(),
        "unresolved_count": 0,
    }


def test_runner_script_exists_and_delegates_to_readiness_cli() -> None:
    source = Path("scripts/run_live_readiness.py").read_text(encoding="utf-8")
    assert "bp_engine.live_readiness.cli" in source
    assert "main" in source