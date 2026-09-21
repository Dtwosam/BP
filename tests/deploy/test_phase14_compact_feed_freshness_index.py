from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_INSTALLER = ROOT / "scripts/deploy/ensure_storage_indexes.py"
SCHEMA = ROOT / "src/bp_engine/storage/schema.py"
MAINTENANCE = ROOT / "src/bp_engine/storage/maintenance.py"
HELPER = ROOT / "scripts/deploy/phase14_compact_feed_freshness_index_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"

INDEX_NAME = "ix_market_state_1s_feed_last_event"
INDEX_COLUMNS = "source, stream, last_event_at DESC"


def test_compact_feed_freshness_index_is_declared_for_fresh_and_existing_hosts() -> None:
    installer = INDEX_INSTALLER.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")

    assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}" in installer
    assert f"ON market_state_1s ({INDEX_COLUMNS})" in installer
    assert f'"{INDEX_NAME}"' in schema
    assert "market_state_1s.c.source" in schema
    assert "market_state_1s.c.stream" in schema
    assert "market_state_1s.c.last_event_at.desc()" in schema


def test_compact_feed_freshness_lookup_is_bounded_latest_row_not_full_feed_max() -> None:
    content = MAINTENANCE.read_text(encoding="utf-8")
    start = content.index("def _compact_feeds_advanced(")
    end = content.index("\ndef _terminal_partial_compact_cutoff(", start)
    function = content[start:end]

    assert "select(market_state_1s.c.last_event_at)" in function
    assert ".order_by(market_state_1s.c.last_event_at.desc())" in function
    assert ".limit(1)" in function
    assert ".scalar_one_or_none()" in function
    assert "func.max(market_state_1s.c.last_event_at)" not in function


def test_production_index_helper_is_exact_head_safety_bound_and_service_preserving() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_HELPER_HEAD",
        "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_APPROVAL",
        "I_APPROVE_PHASE14_COMPACT_FEED_FRESHNESS_INDEX",
        "production_approval_mismatch",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "7c3af78da1922a0e5187c24b799951130cc98887",
        INDEX_NAME,
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
        "indisvalid",
        "indisready",
        "planner did not select",
        "SET statement_timeout = '10s'",
        "insufficient_next_hour_headroom",
        "fail_closed_unit_active",
        "MODE",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "automatic_promotion must remain false",
        "PHASE14_COMPACT_FEED_FRESHNESS_INDEX_GATE=PASS",
    ):
        assert required in content

    assert 'systemctl start "$RECORDER_UNIT"' not in content
    assert 'systemctl start "$V3_PREDICTOR"' not in content
    assert 'systemctl start "$V3_EXECUTION"' not in content
    assert "LIVE_TRADING_ENABLED=true" not in content


def test_ci_syntax_checks_compact_feed_freshness_index_helper() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_compact_feed_freshness_index_cloudshell.sh"
        in ci
    )
