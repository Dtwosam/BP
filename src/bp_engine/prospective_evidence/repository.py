from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, func, select

from bp_engine.dashboard.repository import PostgresDashboardRepository
from bp_engine.storage import schema


class PostgresProspectiveEvidenceRepository:
    """Read prospective evidence without mutating execution or evaluation ledgers."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.dashboard = PostgresDashboardRepository(engine)

    def list_settlements(self) -> list[dict[str, Any]]:
        settlements = schema.paper_settlements
        ranked = select(
            *settlements.c,
            func.row_number()
            .over(
                partition_by=settlements.c.paper_order_id,
                order_by=(settlements.c.settled_at.desc(), settlements.c.id.desc()),
            )
            .label("row_number"),
        ).subquery("prospective_latest_paper_settlements")
        query = (
            select(ranked)
            .where(ranked.c.row_number == 1)
            .order_by(ranked.c.settled_at.asc(), ranked.c.paper_order_id.asc())
        )
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(query).mappings().all()]
        for row in rows:
            row.pop("row_number", None)
        return rows

    def list_evaluations(self) -> list[dict[str, Any]]:
        return self.dashboard.list_evaluations()

    def get_reconciliation(self) -> dict[str, Any]:
        evidence = self.dashboard.get_paper_execution_evidence(history_limit=1)
        reconciliation = evidence["paper_pnl"]["reconciliation"]
        if not isinstance(reconciliation, dict):
            raise RuntimeError("paper reconciliation evidence is malformed")
        return dict(reconciliation)
