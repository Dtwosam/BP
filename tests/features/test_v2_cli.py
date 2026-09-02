import inspect
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, event, insert

from bp_engine.features import v2_cli, v2_service
from bp_engine.features.v2_models import V2FeatureTarget
from bp_engine.storage import schema

START = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
END = START + timedelta(hours=1)


def _market_values(*, suffix: str, start_at: datetime, horizon_seconds: int) -> dict:
    return {
        "gamma_market_id": f"gamma-{suffix}",
        "event_id": f"event-{suffix}",
        "condition_id": f"condition-{suffix}",
        "slug": f"btc-updown-{suffix}",
        "question": f"BTC up or down {suffix}?",
        "horizon_seconds": horizon_seconds,
        "start_at": start_at,
        "end_at": start_at + timedelta(seconds=horizon_seconds),
        "up_token_id": f"up-{suffix}",
        "down_token_id": f"down-{suffix}",
        "resolution_source": "official",
        "rules_text": "static test rules",
        "rules_hash": f"rules-{suffix}",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "resolved_outcome": "Up",
        "discovered_at": START - timedelta(hours=1),
        "updated_at": START - timedelta(minutes=1),
    }


def test_v2_target_loader_reads_static_5m_market_identity_only() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "polymarket_markets" in statement:
            statements.append(statement.lower())

    with engine.begin() as connection:
        connection.execute(
            insert(schema.polymarket_markets),
            [
                _market_values(
                    suffix="inside-5m",
                    start_at=START + timedelta(minutes=5),
                    horizon_seconds=300,
                ),
                _market_values(
                    suffix="inside-15m",
                    start_at=START + timedelta(minutes=10),
                    horizon_seconds=900,
                ),
                _market_values(
                    suffix="end-boundary",
                    start_at=END,
                    horizon_seconds=300,
                ),
            ],
        )
        targets = v2_cli.load_v2_targets(connection, start=START, end=END)

    assert targets == (
        V2FeatureTarget(
            condition_id="condition-inside-5m",
            slug="btc-updown-inside-5m",
            horizon_seconds=300,
            market_start_at=START + timedelta(minutes=5),
            market_end_at=START + timedelta(minutes=10),
            up_token_id="up-inside-5m",
            down_token_id="down-inside-5m",
        ),
    )
    assert statements
    query = statements[-1]
    for forbidden_column in (
        "question",
        "resolution_source",
        "rules_text",
        "active",
        "closed",
        "accepting_orders",
        "resolved_outcome",
    ):
        assert forbidden_column not in query


def test_v2_feature_modules_are_outcome_and_price_history_isolated() -> None:
    source = "\n".join((inspect.getsource(v2_cli), inspect.getsource(v2_service))).lower()
    for forbidden in (
        "market_labels",
        "official_outcome",
        "live_prediction_evaluations",
        "paper_settlements",
        "polymarket_price_history",
    ):
        assert forbidden not in source
