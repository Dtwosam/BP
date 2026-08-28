from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from bp_engine.config import Settings, get_settings
from bp_engine.dashboard.__main__ import validate_dashboard_safety
from bp_engine.dashboard.queries import DashboardQueries
from bp_engine.dashboard.service import DashboardService


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _response(value: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_json_safe(value))


def create_app(service: Any, settings: Settings) -> FastAPI:
    validate_dashboard_safety(settings)
    app = FastAPI(title="BP Research Dashboard API", version="1")

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        del request, exc
        return _response({"detail": "dashboard data unavailable"}, status_code=503)

    @app.get("/health")
    def health() -> JSONResponse:
        return _response(service.health())

    @app.get("/api/v1/overview")
    def overview() -> JSONResponse:
        return _response(service.overview())

    @app.get("/api/v1/markets")
    def markets(
        horizon_seconds: Literal[300, 900] | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> JSONResponse:
        return _response(
            service.markets(
                horizon_seconds=horizon_seconds,
                limit=limit,
            )
        )

    @app.get("/api/v1/predictions")
    def predictions(
        horizon_seconds: Literal[300, 900] | None = None,
        evaluation_state: Literal["pending", "evaluated"] | None = None,
        trade: bool | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before_recorded_at: datetime | None = None,
    ) -> JSONResponse:
        return _response(
            service.predictions(
                horizon_seconds=horizon_seconds,
                evaluation_state=evaluation_state,
                trade=trade,
                limit=limit,
                before_recorded_at=before_recorded_at,
            )
        )

    @app.get("/api/v1/performance")
    def performance() -> JSONResponse:
        return _response(service.performance())

    return app


def create_default_app() -> FastAPI:
    settings = get_settings()
    validate_dashboard_safety(settings)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    service = DashboardService(DashboardQueries(engine), settings)
    return create_app(service, settings)
