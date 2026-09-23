import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_INSTALLER = ROOT / "scripts/deploy/ensure_storage_indexes.py"
SCHEMA = ROOT / "src/bp_engine/storage/schema.py"
MAINTENANCE = ROOT / "src/bp_engine/storage/maintenance.py"
HELPER = ROOT / "scripts/deploy/phase14_compact_feed_freshness_index_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"
STATE = ROOT / "PROJECT_STATE.json"

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
        "DROP INDEX CONCURRENTLY IF EXISTS",
        "SET lock_timeout = '0'",
        "SET statement_timeout = '15min'",
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

    assert "SET lock_timeout = '5s'" not in content
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


def test_source_truth_keeps_compact_feed_index_production_gate_closed() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    assert state["source_of_truth_version"] == "0.14.176"

    storage = state["phase_14_storage_reliability_followup"]
    assert storage["concurrent_partition_retirement_rollout_last_attempt_status"] == "PASS"
    assert storage["concurrent_partition_retirement_rollout_candidate_checkout_performed"] is True
    assert storage["concurrent_partition_retirement_rollout_precheckout_rollback_complete"] is False
    assert storage["concurrent_partition_retirement_rollout_last_attempt_mutation_started"] is True
    assert storage["concurrent_partition_retirement_rollout_last_attempt_eligibility_result"] == (
        "ONE_ELIGIBLE_PARTITION_EXERCISED"
    )
    assert storage["concurrent_partition_retirement_production_deployed"] is True
    assert storage["concurrent_partition_retirement_rollout_gate_production_performed"] is True
    assert (
        storage["concurrent_partition_retirement_rollout_gate_repository_status"]
        == "PRODUCTION_PASS"
    )
    assert storage["concurrent_partition_retirement_rollout_pass_partitions_retired"] == 1
    assert storage["concurrent_partition_retirement_rollout_pass_dedupe_rows_removed"] == 35983
    assert storage["concurrent_partition_retirement_rollout_pass_storage_after_status"] == "ok"
    assert (
        storage[
            "concurrent_partition_retirement_rollout_pass_storage_after_retention_lag_hours"
        ]
        == 0.0
    )
    assert (
        storage[
            "concurrent_partition_retirement_rollout_pass_detached_retirement_leftovers"
        ]
        == 0
    )
    assert (
        storage["concurrent_partition_retirement_rollout_final_checkout_rollback_confirmed"]
        is True
    )
    assert (
        storage[
            "concurrent_partition_retirement_rollout_rollback_confirmed_production_head"
        ]
        == "7c3af78da1922a0e5187c24b799951130cc98887"
    )
    assert (
        storage[
            "concurrent_partition_retirement_rollout_rollback_confirmed_storage_health"
        ]
        == "ok"
    )
    assert (
        storage[
            "concurrent_partition_retirement_rollout_rollback_confirmed_retention_lag_hours"
        ]
        == 0.0
    )
    assert storage["compact_feed_freshness_index_name"] == INDEX_NAME
    assert (
        storage["compact_feed_freshness_index_repository_status"]
        == "PRODUCTION_PASS"
    )
    assert storage["compact_feed_freshness_index_last_attempt_status"] == "PASS"
    assert storage["compact_feed_freshness_index_last_attempt_mutation_started"] is True
    assert storage["compact_feed_freshness_index_invalid_stub_present"] is False
    assert storage["compact_feed_freshness_index_invalid_stub_indisvalid"] is None
    assert storage["compact_feed_freshness_index_invalid_stub_indisready"] is None
    assert storage["compact_feed_freshness_index_invalid_stub_indislive"] is None
    assert storage["compact_feed_freshness_index_production_performed"] is True
    assert storage["compact_feed_freshness_index_production_pass_plan"] == (
        "INDEX_ONLY_SCAN_ALL_REQUIRED_FEEDS"
    )
    assert storage["compact_feed_freshness_index_hardening_pr"] == 233
    assert storage["compact_feed_freshness_index_hardening_validation_head"] == (
        "6eaf5095ec0c33db424e19d2ebee63b7ce231db9"
    )
    assert storage["compact_feed_freshness_index_hardening_ci_run_id"] == 35711018287
    assert (
        storage[
            "compact_feed_freshness_index_hardening_historical_backfill_smoke_run_id"
        ]
        == 35711019406
    )
    assert (
        storage["compact_feed_freshness_index_hardening_live_recorder_smoke_run_id"]
        == 35711018953
    )
    assert (
        storage["compact_feed_freshness_index_hardening_recorder_short_soak_run_id"]
        == 35711019046
    )
    assert storage["compact_feed_freshness_index_hardening_merge_commit"] == (
        "50f226173f881c0e139cfe3325e975c21048432b"
    )
    assert storage["compact_feed_freshness_index_hardening_exact_head_gates_passed"] is True
    assert storage["compact_feed_freshness_index_pr"] == 229
    assert storage["compact_feed_freshness_index_validation_head"] == (
        "abc53100b624ebdb300599c7a29e617c79c6ebe5"
    )
    assert storage["compact_feed_freshness_index_ci_run_id"] == 35663923482
    assert storage["compact_feed_freshness_index_historical_backfill_smoke_run_id"] == 35663923521
    assert storage["compact_feed_freshness_index_live_recorder_smoke_run_id"] == 35663923483
    assert storage["compact_feed_freshness_index_recorder_short_soak_run_id"] == 35663923480
    assert (
        storage[
            "prior_recovery_rollout_sha_bound_authorization_invalidated_by_main_advance"
        ]
        is True
    )
    assert storage["concurrent_partition_retirement_production_rollout_authorized"] is False
    assert storage["concurrent_partition_retirement_rollout_gate_production_authorized"] is False
    assert storage["recorder_v3_recovery_authorized"] is False
    assert storage["compact_feed_freshness_index_production_authorized"] is False
    assert storage["compact_feed_freshness_index_production_performed"] is True
