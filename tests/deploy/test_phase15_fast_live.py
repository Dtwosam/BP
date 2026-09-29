from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_UNIT = ROOT / "deploy" / "bp-phase15-fast-live-source.service"
RECEIVER_UNIT = ROOT / "deploy" / "bp-phase15-fast-live-receiver.service"
REQUIREMENTS = ROOT / "deploy" / "phase15-fast-live-executor-requirements.txt"
SOURCE = ROOT / "scripts" / "run_phase15_v3_fast_live_source.py"
RECEIVER = ROOT / "scripts" / "run_phase15_v3_fast_live_receiver.py"
EXECUTOR = ROOT / "src" / "bp_engine" / "execution" / "fast_live_executor.py"


def test_fast_live_runtime_dependencies_are_pinned() -> None:
    assert REQUIREMENTS.read_text(encoding="utf-8").splitlines() == [
        "polymarket-client==0.7.1",
        "google-cloud-pubsub==2.41.0",
        "websockets==15.0.1",
    ]


def test_fast_live_source_has_no_wallet_or_live_money_runtime() -> None:
    text = SOURCE_UNIT.read_text(encoding="utf-8")
    for marker in (
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "UnsetEnvironment=POLYMARKET_PRIVATE_KEY POLYMARKET_WALLET_ADDRESS",
        "BP_PHASE15_FAST_LIVE_SOURCE_ENABLED=yes",
        "ConditionPathExists=/etc/bp-fast-live/authorization.json",
        "ConditionPathExists=/etc/bp-fast-live/PROJECT_STATE.json",
        "ConditionPathExists=/etc/bp-fast-live/transport.key",
        "--expected-main ${BP_FAST_LIVE_EXPECTED_MAIN}",
        "--topic-id ${BP_FAST_LIVE_TOPIC_ID}",
        "--result-subscription-id ${BP_FAST_LIVE_RESULT_SUBSCRIPTION_ID}",
        "ExecStart=/opt/bp-fast-live/.venv/bin/python",
    ):
        assert marker in text
    source = SOURCE.read_text(encoding="utf-8")
    assert "PublisherClient()" in source
    assert "SubscriberClient()" in source
    assert "verify_result_message" in source
    assert "record_fast_live_result" in source
    assert 'default=0.02' in source
    assert "POLYMARKET_PRIVATE_KEY" in source
    assert "must not be present in fast live source" in source
    assert "create_prepare_message" in source
    assert "create_approval_message" in source
    assert "preview_fast_live_candidate" in source
    assert "approval_prepared=preview" in source
    assert '"finalized.json"' in source
    assert '"cancel.json"' in source
    assert "fast live finalized risk candidate changed" in source
    assert '"parallel_timing"' in source
    assert '"risk_evaluation_ms"' in source
    assert '"preview_to_risk_complete_ms"' in source
    assert "approval_vs_risk_ms" in source
    assert "BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED" in source
    assert "BP_FAST_LIVE_CONTINUOUS_SESSION" in source
    assert "_pending_result_binding" in source
    assert "awaited_intent_id" in source
    assert "result_state_lock" in source
    assert 'status in {"skipped", "blocked"}' in source
    assert "fast_live_result_reconciliation_grace" in source
    assert "fast_live_result_reconciliation_timeout" in source
    assert "_result_wait_deadline" in source
    assert "awaited_result_deadline" in source
    assert '"result_wait_deadline"' in source
    assert "live_session_authorization_expired" in source
    assert "FAST_LIVE_RESULT_MAX_AGE_SECONDS" in source
    assert "/etc/bp-telegram-transport/transport.key" not in text
    assert "ExecStart=/opt/bp/.venv/bin/python" not in text
    assert "/etc/bp-telegram-transport/transport.key" not in source


def test_fast_live_receiver_is_continuous_approval_gated_and_fail_closed() -> None:
    unit = RECEIVER_UNIT.read_text(encoding="utf-8")
    for marker in (
        "ConditionPathExists=/etc/bp-fast-live/authorization.json",
        "ConditionPathExists=/etc/bp-fast-live/PROJECT_STATE.json",
        "ConditionPathExists=/etc/bp-canary/live.env",
        "BP_PHASE15_FAST_LIVE_EXECUTOR_ENABLED=yes",
        "--expected-main ${BP_FAST_LIVE_EXPECTED_MAIN}",
        "--subscription-id ${BP_FAST_LIVE_SUBSCRIPTION_ID}",
        "--result-topic-id ${BP_FAST_LIVE_RESULT_TOPIC_ID}",
        "fast-live-service-stop > /var/lib/bp-canary/fast-live/KILL",
    ):
        assert marker in unit
    assert "ConditionPathExists=/etc/bp-fast-live/transport.key" in unit
    assert "ReadOnlyPaths=/opt/bp-fast-live /etc/bp-fast-live" in unit
    assert "ReadWritePaths=/var/lib/bp-canary/fast-live" in unit
    receiver = RECEIVER.read_text(encoding="utf-8")
    assert "SubscriberClient()" in receiver
    assert "StreamingBookCache()" in receiver
    assert "SafetyRefresher" in receiver
    assert "verify_runtime_authorization" in receiver
    assert "verify_envelope" in receiver
    assert "verify_prepare_message" in receiver
    assert "verify_approval_message" in receiver
    assert "prepare_order" in receiver
    assert "prepared_order=prepared_order" in receiver
    assert "prepare_recovered_after_restart" in receiver
    assert "approval_execution_lock" in receiver
    assert "_claim_approval_once" in receiver
    assert "_write_approval_result" in receiver
    assert "approval_recovery_blocked" in receiver
    assert '"status": "pre_submission_blocked"' in receiver
    assert "except FastLiveError as exc:" in receiver
    assert "if executor.attempt_path_for(verified).exists():" in receiver
    assert 'approved["prepare_sha256"]' in receiver
    assert 'approved["prediction_id"]' in receiver
    assert "execute_with_bounded_pre_attempt_retry" in receiver
    assert "create_result_message" in receiver
    assert "result_publisher.publish" in receiver
    assert "message.nack()" in receiver
    assert "executor.attempt_path_for(verified).exists()" in receiver
    assert "BP_FAST_LIVE_CONTINUOUS_SESSION" in receiver
    assert "not continuous_session" in receiver
    assert "/etc/bp-telegram-transport/transport.key" not in unit
    assert "/etc/bp-telegram-transport/transport.key" not in receiver


def test_fast_live_source_recovers_reconciliation_after_session_expiry() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    settlement = source.index("if pending_settlement:")
    transport_key = source.index("key = load_transport_key_file", settlement)
    assert settlement < transport_key
    assert "reconciliation_only = False" in source
    assert "authorization_validation_observed_at" in source
    assert "runtime_expires_at - timedelta(microseconds=1)" in source
    assert "fast_live_result_reconciliation_only" in source
    assert "pending_result_deadline" in source
    assert "fast_live_result_reconciliation_timeout" in source


def test_fast_live_source_starts_approval_and_johannesburg_before_risk_join() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    preview = source.index("preview = preview_fast_live_candidate")
    prepare = source.index("prepare_message = create_prepare_message", preview)
    finalize = source.index("finalized = prepare_fast_live_candidate", prepare)
    approve = source.index("approval_message = create_approval_message", finalize)
    assert preview < prepare < finalize < approve
    assert "approval_prepared=preview" in source
    assert "fast live finalized risk candidate changed" in source


def test_fast_live_executor_quotes_before_attempt_and_post() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    sign = text.index("create_limit_order")
    quote = text.index("get_order_book", sign)
    marketability = text.index("marketable_depth", quote)
    attempt = text.index("_write_exclusive_json(attempt_path", marketability)
    post = text.index("post_order", attempt)
    assert sign < quote < marketability < attempt < post
    assert "time.sleep(float(self._order_ttl_seconds))" in text
    assert "attempt_path_for" in text
    assert "result_path_for" in text
    assert "if not self._continuous_session:" in text
    assert "fast-live-one-shot-attempt-consumed" in text
