from datetime import UTC, datetime

from bp_engine.backfill.provenance import (
    BackfillArtifact,
    BackfillRun,
    BackfillStats,
    ProvenanceRepository,
    artifact_key,
    canonical_json_sha256,
)
from sqlalchemy import create_engine, func, select

from bp_engine.storage.schema import (
    historical_backfill_artifacts,
    historical_backfill_runs,
    metadata,
)


def make_repository():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, ProvenanceRepository()


def test_canonical_json_hash_and_artifact_key_are_order_independent() -> None:
    left = {"market": "asset-1", "startTs": 10, "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "startTs": 10, "market": "asset-1"}

    assert canonical_json_sha256(left) == canonical_json_sha256(right)
    assert artifact_key("polymarket", "prices", left) == artifact_key(
        "polymarket", "prices", right
    )
    assert artifact_key("bybit", "prices", left) != artifact_key("polymarket", "prices", left)


def test_run_lifecycle_and_response_artifacts_remain_auditable() -> None:
    engine, repository = make_repository()
    started_at = datetime(2026, 8, 24, 20, 0, tzinfo=UTC)
    completed_at = datetime(2026, 8, 24, 20, 1, tzinfo=UTC)
    run = BackfillRun(
        run_id="00000000-0000-0000-0000-000000000001",
        dataset="polymarket_prices",
        source="polymarket",
        requested_start=datetime(2026, 8, 20, tzinfo=UTC),
        requested_end=datetime(2026, 8, 21, tzinfo=UTC),
        parameters={"fidelity": 1},
        started_at=started_at,
    )

    params = {"market": "asset-1", "startTs": 1, "endTs": 2, "fidelity": 1}
    first_payload = {"history": [{"t": 1, "p": 0.4}]}
    revised_payload = {"history": [{"t": 1, "p": 0.41}]}
    key = artifact_key("polymarket", "prices", params)

    with engine.begin() as connection:
        repository.start_run(connection, run)
        first = repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run.run_id,
                artifact_key=key,
                source="polymarket",
                dataset="prices",
                request_params=params,
                downloaded_at=started_at,
                response_sha256=canonical_json_sha256(first_payload),
                row_count=1,
            ),
        )
        duplicate = repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run.run_id,
                artifact_key=key,
                source="polymarket",
                dataset="prices",
                request_params=params,
                downloaded_at=started_at,
                response_sha256=canonical_json_sha256(first_payload),
                row_count=1,
            ),
        )
        revised = repository.record_artifact(
            connection,
            BackfillArtifact(
                run_id=run.run_id,
                artifact_key=key,
                source="polymarket",
                dataset="prices",
                request_params=params,
                downloaded_at=completed_at,
                response_sha256=canonical_json_sha256(revised_payload),
                row_count=1,
            ),
        )
        repository.finish_run(
            connection,
            run.run_id,
            completed_at,
            BackfillStats(rows_inserted=1, rows_existing=2, chunks_fetched=3),
        )

        artifacts = connection.scalar(
            select(func.count()).select_from(historical_backfill_artifacts)
        )
        stored_run = connection.execute(
            select(historical_backfill_runs).where(historical_backfill_runs.c.run_id == run.run_id)
        ).mappings().one()

    assert first.created is True
    assert duplicate.created is False
    assert revised.created is True
    assert artifacts == 2
    assert stored_run["status"] == "success"
    assert stored_run["rows_inserted"] == 1
    assert stored_run["rows_existing"] == 2
    assert stored_run["chunks_fetched"] == 3
    assert stored_run["completed_at"] == completed_at.replace(tzinfo=None)
