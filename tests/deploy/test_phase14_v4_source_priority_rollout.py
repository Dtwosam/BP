from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_source_priority_rollout_cloudshell.sh"
)
RECORDER_SERVICE = ROOT / "src" / "bp_engine" / "recorder" / "service.py"

FROM_HEAD = "a694c2299cd34f0b2ee92ded4a4da1643eff0604"
CANDIDATE_HEAD = "300a9e47500f2e0d6fb653786fc22c12f116b772"
CANDIDATE_BRANCH = "ops/v4-two-priority-batched-candidate-20261007"
SHADOW_RUN_ID = "v4-fresh-book-shadow-20261006T185619Z-271db613e003"


def _source() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_v4_source_priority_rollout_has_valid_bash() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_source_priority_rollout_is_exact_candidate_and_approval_bound() -> None:
    source = _source()
    for marker in (
        FROM_HEAD,
        CANDIDATE_HEAD,
        CANDIDATE_BRANCH,
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_HELPER_HEAD",
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_V4_SOURCE_PRIORITY_ROLLOUT",
        SHADOW_RUN_ID,
        "candidate_branch_changed",
        "candidate_scope_mismatch",
        "candidate_blob_not_exact_main",
        "production_approval_mismatch",
        "EXPECTED_PRIORITY_WRITER_WORKERS=2",
        "EXPECTED_BULK_WRITER_WORKERS=2",
        "EXPECTED_SPOT_TICKER_TOPIC='tickers.BTCUSDT'",
        "EXPECTED_PRIORITY_EVENT_CONTRACT='ticker_only'",
        "EXPECTED_TICKER_SOCKET_CONTRACT='dedicated_bybit_spot_and_linear'",
    ):
        assert marker in source
    assert source.index("production_approval_mismatch") < source.index(
        "gcloud compute ssh"
    )


def test_v4_source_priority_rollout_has_local_only_preflight() -> None:
    source = _source()
    for marker in (
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_PREFLIGHT_ONLY",
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_PREFLIGHT=PASS",
        "EXPECTED_PRIORITY_WRITER_WORKERS=$EXPECTED_PRIORITY_WRITER_WORKERS",
        "EXPECTED_BULK_WRITER_WORKERS=$EXPECTED_BULK_WRITER_WORKERS",
        "EXPECTED_SPOT_TICKER_TOPIC=$EXPECTED_SPOT_TICKER_TOPIC",
        "EXPECTED_PRIORITY_EVENT_CONTRACT=$EXPECTED_PRIORITY_EVENT_CONTRACT",
        "EXPECTED_TICKER_SOCKET_CONTRACT=$EXPECTED_TICKER_SOCKET_CONTRACT",
        "EXPECTED_APPROVAL=$EXPECTED_APPROVAL",
        "PRODUCTION_MUTATION=false",
        "GCLOUD_CONTACT=false",
        "preflight_only_invalid",
    ):
        assert marker in source

    preflight = source.index('if [[ "$PREFLIGHT_ONLY" == "true" ]]')
    approval = source.index('[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]]')
    gcloud = source.index("command -v gcloud")
    assert preflight < approval < gcloud
    assert source.index('echo "GCLOUD_CONTACT=false"') < gcloud
    assert source.index("exit 0", preflight) < approval


def test_v4_source_priority_rollout_scope_is_eight_validated_files() -> None:
    source = _source()
    for path in (
        "src/bp_engine/recorder/writer.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "src/bp_engine/recorder/service.py",
        "src/bp_engine/config.py",
        "tests/recorder/test_writer.py",
        "tests/storage/test_partitioned_raw_postgres.py",
        "tests/recorder/test_recorder_service.py",
        "tests/test_config.py",
    ):
        assert path in source
    assert 'git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD"' in source


def test_v4_source_priority_rollout_preserves_compact_dedupe_contract() -> None:
    source = _source()
    for marker in (
        "require_compact_dedupe_complete",
        "COMPACT_DEDUPE_COMPLETE=true",
        "LEGACY_DEDUPE_PARENT_PK_PRESENT=false",
        "COMPACT_DIGEST_INDEX_COUNT=16",
        "raw_event_dedupe_pkey",
        "raw_event_dedupe_h%_digest_uidx",
        "expected 16 dedupe children",
    ):
        assert marker in source
    assert "CREATE INDEX" not in source
    assert "DROP INDEX" not in source
    assert "REINDEX" not in source


def test_v4_source_priority_rollout_preserves_batch_and_writer_budget() -> None:
    source = _source()
    for marker in (
        "EXPECTED_BATCH_SIZE=500",
        "EXPECTED_QUEUE_MAXSIZE=50000",
        "EXPECTED_WRITER_WORKERS=4",
        "EXPECTED_PRIORITY_QUEUE_MAXSIZE=5000",
        "EXPECTED_PRIORITY_BATCH_SIZE=20",
        "recorder writer workers must equal 4",
        "recorder flush interval must equal 0.25",
        "EXPECTED_PRIORITY_WRITER_WORKERS=2",
        "EXPECTED_BULK_WRITER_WORKERS=2",
        'RECORDER_PRIORITY_WRITER_WORKERS=$EXPECTED_PRIORITY_WRITER_WORKERS',
        'RECORDER_BULK_WRITER_WORKERS=$EXPECTED_BULK_WRITER_WORKERS',
        '"recorder_priority_writer_workers": 2',
        '"recorder_bulk_writer_workers": 2',
        '"recorder_priority_batch_size": 20',
        '"recorder_priority_event_contract": "ticker_only"',
        '"bybit_ticker_socket_contract": "dedicated_bybit_spot_and_linear"',
        "recorder writer split mismatch",
        "RECORDER_TOTAL_WRITER_WORKERS=4",
        "require_priority_config",
    ):
        assert marker in source
    assert "TARGET_BATCH_SIZE" not in source
    assert "set_recorder_batch_size" not in source


def test_v4_source_priority_rollout_binds_priority_source_contract() -> None:
    source = _source()
    for marker in (
        "_is_v4_priority_anchor_event",
        "_RoutedBufferedEventSink",
        "_recorder_writer_split",
        '"writer_priority"',
        '"writer_bulk"',
        "priority_source_contract_missing",
    ):
        assert marker in source


def test_recorder_service_isolates_bybit_tickers_on_dedicated_sockets() -> None:
    source = RECORDER_SERVICE.read_text(encoding="utf-8")
    spot_start = source.index("    spot_bulk_topics = [")
    linear_start = source.index("    linear_bulk_topics = [", spot_start)
    ticker_start = source.index("    ticker_topics = ", linear_start)
    runner_start = source.index("    def bybit_runner(", ticker_start)
    spot_bulk = source[spot_start:linear_start]
    linear_bulk = source[linear_start:ticker_start]
    ticker_contract = source[ticker_start:runner_start]

    for topic in ('"orderbook.50.BTCUSDT"', '"publicTrade.BTCUSDT"'):
        assert topic in spot_bulk
    assert '"tickers.BTCUSDT"' not in spot_bulk

    for topic in (
        '"orderbook.50.BTCUSDT"',
        '"publicTrade.BTCUSDT"',
        '"allLiquidation.BTCUSDT"',
    ):
        assert topic in linear_bulk
    assert '"tickers.BTCUSDT"' not in linear_bulk

    assert 'ticker_topics = ["tickers.BTCUSDT"]' in ticker_contract
    for marker in (
        'bybit_spot = bybit_runner(venue="spot", topics=spot_bulk_topics)',
        'bybit_spot_ticker = bybit_runner(venue="spot", topics=ticker_topics)',
        'bybit_linear = bybit_runner(venue="linear", topics=linear_bulk_topics)',
        'bybit_linear_ticker = bybit_runner(venue="linear", topics=ticker_topics)',
        '"bybit_spot_ticker": bybit_spot_ticker',
        '"bybit_linear_ticker": bybit_linear_ticker',
    ):
        assert marker in source


def test_v4_source_priority_rollout_binds_dedicated_bybit_ticker_sockets_before_mutation() -> None:
    source = _source()
    for marker in (
        "EXPECTED_SPOT_TICKER_TOPIC='tickers.BTCUSDT'",
        "EXPECTED_TICKER_SOCKET_CONTRACT='dedicated_bybit_spot_and_linear'",
        "candidate_spot_orderbook_topic_missing",
        "candidate_spot_trade_topic_missing",
        "candidate_linear_orderbook_topic_missing",
        "candidate_linear_trade_topic_missing",
        "candidate_linear_liquidation_topic_missing",
        "candidate_spot_bulk_contains_ticker",
        "candidate_linear_bulk_contains_ticker",
        "candidate_ticker_topics_missing",
        "candidate_spot_ticker_socket_missing",
        "candidate_linear_ticker_socket_missing",
        "candidate_spot_bulk_socket_missing",
        "candidate_linear_bulk_socket_missing",
        "BYBIT_SPOT_TICKER_SOURCE_CONTRACT=true",
        "BYBIT_TICKER_SOCKET_CONTRACT=$EXPECTED_TICKER_SOCKET_CONTRACT",
        '"bybit_spot_ticker_topic": "tickers.BTCUSDT"',
        '"bybit_spot_ticker_source_contract": True',
        '"bybit_ticker_socket_contract": "dedicated_bybit_spot_and_linear"',
    ):
        assert marker in source

    semantic_gate = source.index('SPOT_BULK_TOPICS="$(git -C "$REPO" show')
    backup = source.index('BACKUP_DIR="$(mktemp -d', semantic_gate)
    mutation = source.index("MUTATION_STARTED=1", semantic_gate)
    assert semantic_gate < backup < mutation


def test_v4_source_priority_rollout_binds_writer_split_before_mutation() -> None:
    source = _source()
    for marker in (
        "candidate_priority_writer_split_missing",
        "candidate_bulk_writer_split_missing",
        "RECORDER_WRITER_SPLIT_CONTRACT=2_priority_2_bulk_at_total_4",
        "priority_workers = min(2, worker_count - 1)",
        "return priority_workers, worker_count - priority_workers",
    ):
        assert marker in source

    semantic_gate = source.index('WRITER_SPLIT_SOURCE="$(git -C "$REPO" show')
    backup = source.index('BACKUP_DIR="$(mktemp -d', semantic_gate)
    mutation = source.index("MUTATION_STARTED=1", semantic_gate)
    assert semantic_gate < backup < mutation


def test_v4_priority_classifier_extraction_captures_full_function() -> None:
    sed_range = "/^def _is_v4_priority_anchor_event/,/^class _RoutedBufferedEventSink/p"
    completed = subprocess.run(
        ["sed", "-n", sed_range, str(RECORDER_SERVICE)],
        check=True,
        capture_output=True,
        text=True,
    )
    extracted = completed.stdout
    assert 'event_type.startswith("ticker_")' in extracted
    assert 'event_type == "ticker"' in extracted

    source = _source()
    expected = (
        "sed -n '/^def _is_v4_priority_anchor_event/,"
        "/^class _RoutedBufferedEventSink/p'"
    )
    truncated = "sed -n '/^def _is_v4_priority_anchor_event/,/^$/p'"
    assert source.count(expected) == 2
    assert truncated not in source


def test_v4_source_priority_rollout_binds_ticker_only_priority_before_mutation() -> None:
    source = _source()
    for marker in (
        "candidate_coinbase_ticker_priority_missing",
        "candidate_bybit_ticker_priority_missing",
        "candidate_priority_contract_not_ticker_only",
        "RECORDER_PRIORITY_EVENT_CONTRACT=$EXPECTED_PRIORITY_EVENT_CONTRACT",
        "EXPECTED_PRIORITY_EVENT_CONTRACT='ticker_only'",
        "event_type.startswith(\"ticker_\")",
        'event_type == "ticker"',
    ):
        assert marker in source

    semantic_gate = source.index(
        'PRIORITY_CLASSIFIER_SOURCE="$(git -C "$REPO" show'
    )
    backup = source.index('BACKUP_DIR="$(mktemp -d', semantic_gate)
    mutation = source.index("MUTATION_STARTED=1", semantic_gate)
    assert semantic_gate < backup < mutation


def test_v4_source_priority_rollout_requires_strict_visibility_acceptance() -> None:
    source = _source()
    for marker in (
        "fewer than 40/80 samples saw a committed row",
        "p95 committed-row age exceeds 2s",
        "timestamp-window readiness below 50%",
        'payload.get("query_timeout_count", -1)',
        'payload.get("query_failure_count", -1)',
        "--ticker-only",
        "visibility event contract mismatch",
        "run_visibility_acceptance",
        "run_soak",
        "recorder_restarted_before_visibility",
        "v3_predictor_restarted_before_visibility",
        "v3_execution_restarted_before_visibility",
    ):
        assert marker in source


def test_v4_source_priority_rollout_assigns_backup_before_mutation() -> None:
    source = _source()
    assignment = (
        'BACKUP_DIR="$(mktemp -d '
        '/var/tmp/bp-v4-source-priority-rollout-backup.XXXXXX)"'
    )
    assert assignment in source
    assert '\n"$(mktemp -d /var/tmp/bp-v4-source-priority-rollout-backup.XXXXXX)"\n' not in source

    backup_at = source.index(assignment)
    env_backup_at = source.index('cp -a "$ENV_FILE" "$BACKUP_DIR/bp.env"')
    mutation_at = source.index("MUTATION_STARTED=1")
    rollback_at = source.index("ROLLBACK_ARMED=1")
    timer_stop_at = source.index('systemctl stop "$MAINTENANCE_TIMER"', mutation_at)

    assert backup_at < env_backup_at < mutation_at < timer_stop_at
    assert backup_at < rollback_at < timer_stop_at


def test_v4_source_priority_rollout_preserves_active_shadow_pid() -> None:
    source = _source()
    for marker in (
        'SHADOW_UNIT="$EXPECTED_SHADOW_UNIT"',
        "require_shadow_independent_of_recorder",
        "require_shadow_unchanged",
        'require_shadow_unchanged "pre_mutation"',
        'require_shadow_unchanged "recorder_stopped"',
        'require_shadow_unchanged "recorder_restarted"',
        'require_shadow_unchanged "post_visibility_acceptance"',
        'require_shadow_unchanged "post_timer_restore"',
        "SHADOW_RESTARTED=false",
        "shadow_preserved_without_restart",
    ):
        assert marker in source
    assert 'systemctl stop "$SHADOW_UNIT"' not in source
    assert 'systemctl start "$SHADOW_UNIT"' not in source
    assert 'systemctl restart "$SHADOW_UNIT"' not in source


def test_v4_source_priority_rollout_keeps_fast_live_closed() -> None:
    source = _source()
    for marker in (
        "bp-phase15-fast-live-source.service",
        "fast_live_source_active",
        "fast_live_source_enabled",
        "fast_live_source_active_after_rollout",
        "fast_live_source_enabled_after_rollout",
    ):
        assert marker in source
    assert 'systemctl start "$FAST_LIVE_SOURCE"' not in source


def test_v4_source_priority_rollout_rolls_back_checkout_and_chain() -> None:
    source = _source()
    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    for marker in (
        'systemctl stop "$V3_EXECUTION"',
        'systemctl stop "$V3_PREDICTOR"',
        'systemctl stop "$RECORDER_UNIT"',
        'git -C "$REPO" checkout --detach --force "$FROM_HEAD"',
        'systemctl start "$RECORDER_UNIT"',
        'systemctl start "$V3_PREDICTOR"',
        'systemctl start "$V3_EXECUTION"',
        'systemctl start "$MAINTENANCE_TIMER"',
        "SHADOW_ACTIVE=",
        "SHADOW_PID=",
        "SHADOW_RESTARTS=",
    ):
        assert marker in rollback
    assert 'systemctl stop "$SHADOW_UNIT"' not in rollback
    assert 'systemctl start "$SHADOW_UNIT"' not in rollback


def test_v4_source_priority_rollout_preserves_zero_money_boundary() -> None:
    source = _source()
    for marker in (
        '[[ "$mode" == "research" ]]',
        '[[ "$live" == "false" ]]',
        '[[ "$trade" == "0" ]]',
        '[[ "$loss" == "0" ]]',
        'echo "LIVE_TRADING_ENABLED=false"',
        'echo "MAX_TRADE_SIZE_USD=0"',
        'echo "MAX_DAILY_LOSS_USD=0"',
        "require_automatic_promotion_false",
    ):
        assert marker in source
    assert "LIVE_TRADING_ENABLED=true" not in source
