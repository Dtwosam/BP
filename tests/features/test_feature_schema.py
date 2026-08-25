from pathlib import Path

from bp_engine.storage import schema


def test_market_features_metadata_contract() -> None:
    table = schema.__dict__["market_features"]
    expected = {
        "condition_id",
        "slug",
        "horizon_seconds",
        "market_start_at",
        "market_end_at",
        "feature_at",
        "feature_offset_seconds",
        "feature_version",
        "features",
        "missing_flags",
        "source_cutoffs",
        "input_fingerprint",
        "feature_hash",
        "generated_at",
    }
    assert expected <= set(table.c.keys())
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("condition_id", "feature_at", "feature_version") in unique_columns


def test_market_features_migration_is_additive_and_guarded() -> None:
    sql = Path("migrations/0006_market_features.sql").read_text(encoding="utf-8")
    normalized = " ".join(sql.lower().split())
    assert "create table if not exists market_features" in normalized
    assert "unique (condition_id, feature_at, feature_version)" in normalized
    assert "check (horizon_seconds > 0)" in normalized
    assert "check (market_end_at > market_start_at)" in normalized
    assert "check (feature_at > market_start_at)" in normalized
    assert "check (feature_at < market_end_at)" in normalized
    assert "drop table" not in normalized
    assert "truncate" not in normalized
