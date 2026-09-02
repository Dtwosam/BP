from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_v2_forward_coverage_rollout_cloudshell.sh"
INDEX_INSTALLER = ROOT / "scripts" / "deploy" / "ensure_storage_indexes.py"
SCHEMA = ROOT / "src" / "bp_engine" / "storage" / "schema.py"

INDEX_NAME = "ix_market_state_1s_polymarket_market_lookup"
INDEX_COLUMNS = "instrument, asset_id, bucket_at DESC, last_event_at DESC, id DESC"
INDEX_PREDICATE = "WHERE source = 'polymarket' AND stream = 'market'"


def test_storage_index_installer_declares_targeted_polymarket_market_lookup_index() -> None:
    content = INDEX_INSTALLER.read_text(encoding="utf-8")
    assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}" in content
    assert f"ON market_state_1s ({INDEX_COLUMNS})" in content
    assert INDEX_PREDICATE in content


def test_schema_declares_targeted_polymarket_market_lookup_index() -> None:
    content = SCHEMA.read_text(encoding="utf-8")
    assert INDEX_NAME in content
    for column in ("instrument", "asset_id", "bucket_at", "last_event_at", "id"):
        assert f"market_state_1s.c.{column}" in content
    assert "postgresql_where" in content


def test_v2_forward_rollout_installs_storage_indexes_before_collector_start() -> None:
    content = HELPER.read_text(encoding="utf-8")
    installer = '"$REPO/scripts/deploy/ensure_storage_indexes.py"'
    service_start = 'systemctl start "$SERVICE_UNIT"'
    assert "scripts/deploy/ensure_storage_indexes.py" in content
    assert "src/bp_engine/storage/schema.py" in content
    assert installer in content
    assert content.index(installer) < content.index(service_start)
