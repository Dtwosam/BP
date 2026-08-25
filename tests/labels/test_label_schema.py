from pathlib import Path

from sqlalchemy import UniqueConstraint

from bp_engine.storage.schema import market_labels


def test_market_labels_schema_has_immutable_natural_key_and_provenance() -> None:
    column_names = set(market_labels.c.keys())
    assert {
        "condition_id",
        "gamma_market_id",
        "slug",
        "horizon_seconds",
        "market_start_at",
        "market_end_at",
        "official_outcome",
        "start_reference",
        "end_reference",
        "resolution_source",
        "rules_hash",
        "label_source",
        "label_version",
        "source_snapshot_sha256",
        "source_observed_at",
        "generated_at",
    } <= column_names

    unique_column_sets = {
        tuple(constraint.columns.keys())
        for constraint in market_labels.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("condition_id", "label_version") in unique_column_sets
    assert market_labels.c.start_reference.nullable is True
    assert market_labels.c.end_reference.nullable is True
    assert market_labels.c.source_observed_at.nullable is False
    assert market_labels.c.generated_at.nullable is False


def test_market_labels_migration_is_additive_and_matches_natural_key() -> None:
    migration = Path("migrations/0005_market_labels.sql").read_text(encoding="utf-8")
    lowered = migration.lower()

    assert "create table if not exists market_labels" in lowered
    assert "unique (condition_id, label_version)" in lowered
    assert "drop table" not in lowered
    assert "truncate" not in lowered
    assert "delete from" not in lowered
