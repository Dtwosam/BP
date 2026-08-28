from __future__ import annotations

from sqlalchemy.dialects import postgresql

from bp_engine.dashboard.repository import PostgresDashboardRepository


def test_compact_feed_latest_state_query_is_index_bounded() -> None:
    query = PostgresDashboardRepository._latest_compact_feed_state_query("bybit", "spot")
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()

    assert "MARKET_STATE_1S.SOURCE = 'BYBIT'" in sql
    assert "MARKET_STATE_1S.STREAM = 'SPOT'" in sql
    assert "ORDER BY MARKET_STATE_1S.BUCKET_AT DESC" in sql
    assert "LIMIT 1" in sql
    assert "GROUP BY" not in sql
