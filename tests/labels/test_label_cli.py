import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.storage.schema import market_labels, metadata, polymarket_market_snapshots


def _cli():
    return importlib.import_module("bp_engine.labels.cli")


def _resolved_payload(start: datetime) -> dict[str, object]:
    return {
        "id": "market-cli",
        "conditionId": "condition-cli",
        "slug": f"btc-updown-5m-{int(start.timestamp())}",
        "question": "Bitcoin Up or Down",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": "Official BTC resolution rule",
        "outcomes": json.dumps(["Up", "Down"]),
        "outcomePrices": json.dumps(["1", "0"]),
        "clobTokenIds": json.dumps(["up-token", "down-token"]),
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "events": [{"id": "event-cli"}],
    }


def test_cli_rejects_naive_datetime() -> None:
    cli = _cli()
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--start",
                "2026-08-25T12:00:00",
                "--end",
                "2026-08-25T12:05:00Z",
            ]
        )


def test_cli_rejects_non_increasing_window() -> None:
    cli = _cli()

    with pytest.raises(SystemExit, match="start must be before end"):
        cli.main(
            [
                "--start",
                "2026-08-25T12:05:00Z",
                "--end",
                "2026-08-25T12:05:00Z",
                "--database-url",
                "sqlite+pysqlite:///:memory:",
            ]
        )


def test_cli_generates_labels_offline_and_emits_json(tmp_path, capsys) -> None:
    cli = _cli()
    database_path = tmp_path / "labels.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    metadata.create_all(engine)
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    payload = _resolved_payload(start)

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots).values(
                condition_id="condition-cli",
                gamma_market_id="market-cli",
                slug=payload["slug"],
                downloaded_at=start + timedelta(minutes=6),
                payload_sha256="sha256:cli",
                payload=payload,
            )
        )

    rc = cli.main(
        [
            "--start",
            "2026-08-25T12:00:00Z",
            "--end",
            "2026-08-25T12:05:00Z",
            "--database-url",
            database_url,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    with engine.begin() as connection:
        stored = connection.execute(select(market_labels)).mappings().one()

    assert rc == 0
    assert output == {
        "conditions_considered": 1,
        "existing": 0,
        "inserted": 1,
        "skipped": 0,
    }
    assert stored["official_outcome"] == "Up"


def test_cli_source_has_no_network_client_dependency() -> None:
    source = Path("src/bp_engine/labels/cli.py").read_text(encoding="utf-8")

    assert "httpx" not in source
    assert "GammaClient" not in source
    assert "Bybit" not in source
    assert "Coinbase" not in source
